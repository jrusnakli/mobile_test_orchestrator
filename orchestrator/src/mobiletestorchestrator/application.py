"""
This package holds the API for installing apks, and starting/stopping/querying apps installed on a device
"""
import asyncio
import logging
import subprocess
import time

from apk_bitminer.parsing import AXMLParser  # type: ignore
from contextlib import suppress
from typing import List, TypeVar, Type, Optional, Dict, Union, Set, Iterable, Callable, AsyncIterator

from .device import Device, RemoteDeviceBased, _device_lock
from .device_interactions import DeviceInteraction, AsyncDeviceInteraction

log = logging.getLogger(__name__)

# for returning instance of "cls" in install class-method.  Recommended way of doing this, but there
# may be better way in later Python 3 interpreters?
# noinspection PyTypeChecker
_TApp = TypeVar('_TApp', bound='Application')
# noinspection PyTypeChecker
_TTestApp = TypeVar('_TTestApp', bound='TestApplication')

__all__ = ["Application", "TestApplication", "ServiceApplication",
           "AsyncApplication", "AsyncTestApplication", "AsyncServiceApplication"]


class ApplicationBase(RemoteDeviceBased):

    # noinspection SpellCheckingInspection
    SILENT_RUNNING_PACKAGES = ["com.samsung.android.mtpapplication", "com.wssyncmldm", "com.bitbar.testdroid.monitor"]
    SLEEP_GRANT_PERMISSION = 4

    def __init__(self, device: Device, manifest: Union[AXMLParser, Dict[str, str]]):
        super().__init__(device)
        self._version: Optional[str] = None  # loaded on-demand first time self.version called
        self._package_name: str = manifest.package_name if isinstance(manifest, AXMLParser) \
            else manifest.get("package_name", "<<unspecified>>")
        if self._package_name is None:
            raise ValueError("manifest argument as dictionary must contain \"package_name\" as key")
        self._permissions: Set[str] = set(manifest.permissions if isinstance(manifest, AXMLParser)
                                          else manifest.get("permissions", []))
        self._granted_permissions: Set[str] = set()

    @property
    def package_name(self) -> str:
        """
        :return: Android package name associated with this app
        """
        return self._package_name

    @property
    def permissions(self) -> Set[str]:
        """
        :return: set of permissions required by this app
        """
        return self._permissions

    @property
    def version(self) -> Optional[str]:
        """
        :return: version of this app
        """
        if self._version is None:
            self._version = self.device.get_version(self.package_name)
        return self._version

    @property
    def pid(self) -> Optional[str]:
        """
        :return: pid of app if running (either in foreground or background) or None if not running
        """
        # noinspection SpellCheckingInspection
        completed = self.device.execute_remote_cmd("shell", "pidof", "-s", self.package_name, stdout=subprocess.PIPE,
                                                   fail_on_error_code=lambda x: False)
        if completed.returncode != 0:
            return None
        split_output = completed.stdout.splitlines()
        return split_output[0].strip() if split_output else None

    @property
    def granted_permissions(self) -> Set[str]:
        """
        :return: set of all permissions granted to the app
        """
        return self._granted_permissions


class Application(ApplicationBase):
    """
    Defines an application installed on a remotely USB-connected device. Provides an interface for stopping,
    starting an application, and such.

    An application is distinguished from a bundle (an apk).  A bundle is a package that only after installed
    creates an application on the target device.  Thus, an application only makes sense in the context of the
    device on which it can be launched.

    :param manifest: AXMLParser instance representing manifest from an apk, or Dict of key/value string pairs of
       package name and permissions for app;  if Dict, the dictionary MUST contain "package_name" as a key, and
       optionally contain "permissions" as a key (otherwise assumed to be empty)
    :param device: which device app resides on

    >>> device = Device("some_serial_id")
    ... async def install_my_app():
    ...     app = await AsyncApplication.from_apk("some.apk", device)
    ...     app.grant_permissions(["android.permission.WRITE_EXTERNAL_STORAGE"])
    """

    def __init__(self, device: Device, manifest: Union[AXMLParser, Dict[str, str]]):
        super().__init__(device, manifest)
        self._device_navigation = DeviceInteraction(device)

    @classmethod
    def from_apk(cls: Type[_TApp], apk_path: str, device: Device, as_upgrade: bool = False,
                 as_test_app: bool = False,
                 timeout: Optional[int] = Device.TIMEOUT_LONG_ADB_CMD) -> _TApp:
        """
        Install provided application, blocking until install is complete

        :param apk_path: path to apk
        :param device: device to install on
        :param as_upgrade: whether to install as upgrade or not
        :param as_test_app: whether app can be isntalled as test app (aka only installable via adb)
        :param timeout: raises TimeoutError if specified and install takes too long
        :return: remote installed application
        :raises: Exception if failure ot install or verify installation
        :raises: TimeoutError if install takes more than timeout parameter

        >>> app = Application.from_apk("/local/path/to/apk", device, as_upgrade=True)
        """
        parser = AXMLParser.parse(apk_path)
        args = []
        if as_upgrade:
            args.append("-r")
        if as_test_app:
            args.append("-t")
        cls._install(device, apk_path, *args, timeout=timeout)
        return cls(device, parser)

    @classmethod
    def _install(cls, device: Device, apk_path: str, *args: str,
                 timeout: Optional[int] = Device.TIMEOUT_LONG_ADB_CMD) -> None:
        """
        install the given bundle, blocking until complete

        :param device: Device to install on
        :param apk_path: local path to the apk to be installed
        :param timeout: timeout if install takes too long
        :param args: list of additional arguments to pass to he install command (adb install <*args> apk_path)
        :raises; TimeoutError if install takes more than timeout parameter
        """
        cmd = ["install"] + list(args) + [apk_path]
        device.execute_remote_cmd(*cmd, timeout=timeout, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def uninstall(self) -> None:
        """
        Uninstall this app from remote device.
        """
        with suppress(Exception):
            self.stop()
        try:
            completed = self.device.execute_remote_cmd("uninstall", self.package_name)
            if completed.returncode != 0:
                log.error("Failed to uninstall app %s [%d]\n %s", self.package_name, completed.returncode,
                          completed.stderr)
        except subprocess.TimeoutExpired:
            log.warning("adb command froze on uninstall.  Ignoring issue as device specific")
        except Exception as e:
            if self.package_name in self.device.list_installed_packages():
                log.error("Failed to uninstall app %s [%s]", self.package_name, str(e))

    def grant_permissions(self, permissions: Optional[Iterable[str]] = None) -> Set[str]:
        """
        Grant permissions for a package on a device

        :param permissions: string list of Android permissions to be granted, or None to grant app's defined
           user permissions
        :return: the set of all permissions granted to the app
        """
        permissions = permissions or self.permissions
        # workaround for xiaomi:
        permissions_filtered = set(p.strip() for p in permissions if p in Device.DANGEROUS_PERMISSIONS)
        if not permissions_filtered:
            log.info("Permissions %s already requested or no 'dangerous' permissions requested, so nothing to do" %
                     permissions)
            return self._granted_permissions
        # note "block grants" do not work on all Android devices, so grant 'em one-by-one
        for p in permissions_filtered:
            try:
                self.device.execute_remote_cmd("shell", "pm", "grant", self.package_name, p)
            except Exception as e:
                log.warning(f"Failed to grant permission {p} for package {self.package_name} [{str(e)}]")
            self._granted_permissions.add(p)
        return self._granted_permissions

    # noinspection SpellCheckingInspection
    def regrant_permissions(self) -> Set[str]:
        """
        Regrant permissions (e.g. if an app's data was cleared) that were previously granted

        :return: set of permissions that are currently granted to the app
        """
        return self.grant_permissions(self._granted_permissions)

    def start(self, activity: str, *options: str, intent: Optional[str] = None) -> None:
        """
        start an app on the device

        :param activity: which Android Activity to invoke on start of app, or None for default (MainActivity)
        :param intent: which Intent to invoke, or None for default intent
        :param options: string list of options to pass on to the "am start" command on the remote device, or None
        """
        if activity.startswith("."):
            # embellish to fully qualified name as Android expects
            activity = f"{self.package_name}{activity}"
        activity = f"{self.package_name}/{activity}"
        if intent:
            options = ("-a", intent, *options)
        self.device.execute_remote_cmd("shell", "am", "start", "-n", activity, *options)

    def monkey(self, count: int = 1) -> None:
        """
        Run monkey against application
        More to read about adb monkey at https://developer.android.com/studio/test/monkey#command-options-reference
        """
        cmd = ["shell", "monkey", "-p", self._package_name, "-c", "android.intent.category.LAUNCHER", str(count)]
        self.device.execute_remote_cmd(*cmd, timeout=Device.TIMEOUT_LONG_ADB_CMD)

    def stop(self, force: bool = True) -> None:
        """
        stop this app on the device

        :param force: perform a force-stop if true (kill of app) rather than normal stop
        """
        try:
            basic_cmd = "stop" if not force else "force-stop"
            if force:
                self._device_navigation.go_home()
            self.device.execute_remote_cmd("shell", "am", basic_cmd, self.package_name)
        except Exception as e:
            log.error("Failed to (force) stop app %s with error: %s", self.package_name, str(e))

    def clean_kill(self) -> None:
        """
        Running this command to close the app is equivalent to the app being background-ed, and then having the system
        kill the app to clear up resources for other apps. It is also equivalent to closing the app from the "Recent
        Apps" menu.

        Subsequent launches of the app will need to recreate the app from scratch, and is considered a
        cold app launch.
        NOTE: Currently appears to only work with Android 9.0 devices
        """
        self._device_navigation.input("KEYCODE_HOME")
        tries = 3
        # poll a few times to ensure input is enacted
        while tries and not self._device_navigation.home_screen_active():
            tries -= 1
            time.sleep(0.5)
        self.device.execute_remote_cmd("shell", "am", "kill", self.package_name)
        time.sleep(2)
        if self.pid is not None:
            if not self._device_navigation.home_screen_active():
                raise Exception("Failed to background current foreground app. Cannot complete app closure.")
            else:
                raise Exception("Detected app process is still running. App closure failed.")

    # noinspection SpellCheckingInspection
    def clear_data(self, regrant_permissions: bool = True) -> None:
        """
        clears app data for given package
        """
        self.device.execute_remote_cmd("shell", "pm", "clear", self.package_name)
        if regrant_permissions:
            self.regrant_permissions()
        else:
            self._granted_permissions = set()

    def in_foreground(self, ignore_silent_apps: bool = True) -> bool:
        """
        :return: whether app is currently in foreground
        """
        activity_stack = self.device.get_activity_stack() or []
        index = 0
        if ignore_silent_apps:
            while activity_stack and (activity_stack[index].lower() in self.SILENT_RUNNING_PACKAGES):
                index += 1
        if index > len(activity_stack):
            return False
        foreground_activity = activity_stack[index]
        return foreground_activity.lower() == self.package_name.lower()


class AsyncApplication(ApplicationBase):
    """
    Defines an application installed on a remotely USB-connected device. Provides an interface for stopping,
    starting an application, and such.

    An application is distinguished from a bundle (an apk).  A bundle is a package that only after installed
    creates an application on the target device.  Thus, an application only makes sense in the context of the
    device on which it can be launched.

    :param manifest: AXMLParser instance representing manifest from an apk, or Dict of key/value string pairs of
       package name and permissions for app;  if Dict, the dictionary MUST contain "package_name" as a key, and
       optionally contain "permissions" as a key (otherwise assumed to be empty)
    :param device: which device app resides on

    >>> device = Device("some_serial_id")
    ... async def install_my_app():
    ...     app = Application.from_apk("some.apk", device)
    ...     app.grant_permissions(["android.permission.WRITE_EXTERNAL_STORAGE"])
    """

    def __init__(self, device: Device, manifest: Union[AXMLParser, Dict[str, str]]):
        super().__init__(device, manifest)
        self._device_navigation = AsyncDeviceInteraction(device)

    @classmethod
    async def _install(cls, device: Device, apk_path: str, *args: str,
                       timeout: Optional[int] = Device.TIMEOUT_LONG_ADB_CMD) -> None:
        """
        install the given bundle, blocking until complete

        :param device: Device to install on
        :param apk_path: local path to the apk to be installed
        :param timeout: timeout if install takes too long
        :param args: list of additional arguments to pass to he install command (adb install <*args> apk_path)
        :raises; TimeoutError if install takes more than timeout parameter
        """
        cmd = ["install"] + list(args) + [apk_path]
        await device.execute_remote_cmd_async(*cmd, timeout=timeout)

    @classmethod
    async def from_apk(cls: Type[_TApp],
                       apk_path: str,
                       device: Device,
                       as_upgrade: bool = False,
                       as_test_app: bool = False,
                       timeout: Optional[int] = Device.TIMEOUT_LONG_ADB_CMD,
                       callback: Optional[Callable[[Device.Process], None]] = None) -> _TApp:
        """
        Install provided application asynchronously.  This allows the output of the install to be processed
        in a streamed fashion.  This can be useful on some devices that are non-standard android where installs
        cause (for example) a pop-up requesting direct user permission for the install -- monitoring for a specific
        message to simulate the tap to confirm the install.

        :param apk_path: path to apk
        :param device: device to install on
        :param as_upgrade: whether to install as upgrade or not
        :param as_test_app: if app is compiled as test app or not
        :param timeout: raises TimeoutError if specified and install takes too long
        :param callback: if not None, callback to be made on successful push and at start of install; the
           `Device.Process` that is installing from device storage is passed as the only parameter to the callback
        :return: remote installed application
        :raises: Exception if failure of install or verify installation
        :raises: TimeoutError if install takes more than timeout parameter

        >>> async def install():
        ...     async with AsyncApplication.from_apk("/some/local/path/to/apk", device) as stdout:
        ...         async for line in stdout:
        ...            if "some trigger message" in line:
        ...               print(line)

        """
        parser = AXMLParser.parse(apk_path)
        args = []
        if as_upgrade:
            args.append("-r")
        if as_test_app:
            args.append("-t")
        await cls._monitor_install(device, apk_path, *args, callback=callback, timeout=timeout)
        return cls(device, parser)

    @classmethod
    async def _monitor_install(cls, device: Device, apk_path: str, *args: str,
                               timeout: Optional[int] = Device.TIMEOUT_LONG_ADB_CMD,
                               callback: Optional[Callable[[Device.Process], None]] = None) -> None:
        """
        Install given apk asynchronously, monitoring output for messages containing any of the given conditions,
        executing a callback if given when any such condition is met.

        :param device: Device to install on
        :param apk_path: bundle to install
        :param timeout: timeout if install takes too long
        :param callback: if not None, callback to be made on successful push and at start of install; the
           `Device.Process` that is installing from device storage is passed as the only parameter to the callback
        :raises Device.InsufficientStorageError: if there is not enough space on device
        :raises IOError if push of apk to device was unsuccessful
        :raises; TimeoutError if install takes more than timeout parameter
        """
        parser = AXMLParser.parse(apk_path)
        package = parser.package_name
        remote_data_path = f"/data/local/tmp/{package}"
        try:
            # We try Android Studio's method of pushing to device and installing from there, but if push is
            # unsuccessful, we fallback to plain adb install
            push_cmd = ("push", apk_path, remote_data_path)
            await device.execute_remote_cmd_async(*push_cmd, timeout=timeout)

            # Execute the installation of the app, monitoring output for completion in order to invoke any extra
            # commands or detect insufficient storage issues
            cmd: List[str] = ["shell", "pm", "install"] + list(args) + [remote_data_path]
            # Do not allow more than one install at a time on a specific device, as this can be problematic
            async with _device_lock(device), device.monitor_remote_cmd(*cmd) as proc:
                if callback:
                    callback(proc)
                await proc.wait(timeout=Device.TIMEOUT_LONG_ADB_CMD)

        except Device.CommandExecutionFailure as e:
            raise IOError(f"Unable to push apk to device {e}") from e

        finally:
            with suppress(Exception):
                rm_cmd = ("shell", "rm", remote_data_path)
                device.execute_remote_cmd(*rm_cmd, timeout=Device.TIMEOUT_ADB_CMD)

    async def uninstall(self) -> None:
        """
        Uninstall this app from remote device.
        """
        with suppress(Exception):
            await self.stop()
        try:
            return_code, output, stderr = await self.device.execute_remote_cmd_async("uninstall", self.package_name,
                                                                                     stderr=asyncio.subprocess.PIPE)
            if return_code != 0:
                log.error("Failed to uninstall app %s [%d]\n %s", self.package_name, return_code,
                          stderr)
        except subprocess.TimeoutExpired:
            log.warning("adb command froze on uninstall.  Ignoring issue as device specific")
        except Exception as e:
            if self.package_name in self.device.list_installed_packages():
                log.error("Failed to uninstall app %s [%s]", self.package_name, str(e))

    async def grant_permissions(self, permissions: Optional[Iterable[str]] = None) -> Set[str]:
        """
        Grant permissions for a package on a device

        :param permissions: string list of Android permissions to be granted, or None to grant app's defined
           user permissions
        :return: the set of all permissions granted to the app
        """
        permissions = permissions or self.permissions
        # workaround for xiaomi:
        permissions_filtered = set(p.strip() for p in permissions if p in Device.DANGEROUS_PERMISSIONS)
        if not permissions_filtered:
            log.info("Permissions %s already requested or no 'dangerous' permissions requested, so nothing to do" %
                     permissions)
            return self._granted_permissions
        # note "block grants" do not work on all Android devices, so grant 'em one-by-one
        for p in permissions_filtered:
            try:
                await self.device.execute_remote_cmd_async("shell", "pm", "grant", self.package_name, p,
                                                           timeout=Device.TIMEOUT_LONG_ADB_CMD)
            except Exception as e:
                log.warning(f"Failed to grant permission {p} for package {self.package_name} [{str(e)}]")
            self._granted_permissions.add(p)
        return self._granted_permissions

    # noinspection SpellCheckingInspection
    async def regrant_permissions(self) -> Set[str]:
        """
        Regrant permissions (e.g. if an app's data was cleared) that were previously granted

        :return: set of permissions that are currently granted to the app
        """
        return await self.grant_permissions(self._granted_permissions)

    async def start(self, activity: str, *options: str, intent: Optional[str] = None,
                    timeout: Optional[float] = None) -> None:
        """
        start an app on the device

        :param activity: which Android Activity to invoke on start of app, or None for default (MainActivity)
        :param intent: which Intent to invoke, or None for default intent
        :param options: string list of options to pass on to the "am start" command on the remote device, or None
        :param timeout: timeout after this many seconds (optional)

        :raises: asyncio.TimeoutError if timeout is given and execution exceeds that timeout
        """
        if activity.startswith("."):
            # embellish to fully qualified name as Android expects
            activity = f"{self.package_name}{activity}"
        activity = f"{self.package_name}/{activity}"
        if intent:
            options = ("-a", intent, *options)
        await self.device.execute_remote_cmd_async("shell", "am", "start", "-n", activity, *options,
                                                   timeout=timeout)

    async def monkey(self, count: int = 1) -> None:
        """
        Run monkey against application
        More to read about adb monkey at https://developer.android.com/studio/test/monkey#command-options-reference
        """
        cmd = ["shell", "monkey", "-p", self._package_name, "-c", "android.intent.category.LAUNCHER", str(count)]
        await self.device.execute_remote_cmd_async(*cmd, timeout=Device.TIMEOUT_LONG_ADB_CMD)

    async def stop(self, force: bool = True, timeout: Optional[float] = None) -> None:
        """
        stop this app on the device

        :param force: perform a force-stop if true (kill of app) rather than normal stop
        :param timeout: optional timeout
        :raises asyncio.TimeoutError: if timeout is specified and command execution is taking too long
        """
        try:
            basic_cmd = "stop" if not force else "force-stop"
            await self.device.execute_remote_cmd_async("shell", "am", basic_cmd, self.package_name,
                                                       timeout=timeout)
        except Exception as e:
            log.error("Failed to (force) stop app %s with error: %s", self.package_name, str(e))

    async def clean_kill(self) -> None:
        """
        Running this command to close the app is equivalent to the app being background-ed, and then having the system
        kill the app to clear up resources for other apps. It is also equivalent to closing the app from the "Recent
        Apps" menu.

        Subsequent launches of the app will need to recreate the app from scratch, and is considered a
        cold app launch.
        NOTE: Currently appears to only work with Android 9.0 devices
        """
        await self._device_navigation.input("KEYCODE_HOME")
        tries = 3
        # poll a few times to ensure input is enacted
        while tries and not await self._device_navigation.home_screen_active():
            tries -= 1
            time.sleep(0.5)
        await self.device.execute_remote_cmd_async("shell", "am", "kill", self.package_name)
        if self.pid is not None:
            if not await self._device_navigation.home_screen_active():
                raise Exception("Failed to background current foreground app. Cannot complete app closure.")
            else:
                raise Exception("Detected app process is still running. App closure failed.")

    # noinspection SpellCheckingInspection
    async def clear_data(self, regrant_permissions: bool = True) -> None:
        """
        clears app data for given package
        """
        await self.device.execute_remote_cmd_async("shell", "pm", "clear", self.package_name)
        if regrant_permissions:
            await self.regrant_permissions()
        else:
            self._granted_permissions = set()

    async def in_foreground(self, ignore_silent_apps: bool = True) -> bool:
        """
        :return: whether app is currently in foreground
        """
        async for item in self.device.iterate_activity_stack():
            if ignore_silent_apps and item in self.SILENT_RUNNING_PACKAGES:
                continue
            if item.lower() == self.package_name.lower():
                return True
        return False


class ServiceApplication(Application):
    """
    Class representing an Android application that is specifically a service
    """

    def start(self, activity: str,
              *options: str, intent: Optional[str] = None, foreground: bool = True) -> None:

        # noinspection SpellCheckingInspection
        """
                invoke an intent associated with this service by calling start the service

                :param options: string list of options to supply to "am startservice" command
                :param activity: activity handles the intent
                :param intent: if not None, invoke specific intent otherwise invoke default intent
                :param foreground: whether to start in foreground or not (Android O+
                    does not allow background starts any longer)
                """
        if activity and activity.startswith("."):
            activity = f"{self.package_name}{activity}"
        # embellish to fully qualified name as Android expects
        activity = \
            f"{self.package_name}/{activity}" if activity else f"{self.package_name}/{self.package_name}.MainActivity"
        options = tuple(f'"{item}"' for item in options)
        if intent:
            options = ("-a", intent) + options
        if foreground and self.device.api_level and self.device.api_level >= 26:
            self.device.execute_remote_cmd("shell", "am", "start-foreground-service", "-n", activity, *options)
        else:
            # noinspection SpellCheckingInspection
            self.device.execute_remote_cmd("shell", "am", "startservice", "-n", activity, *options)

    def broadcast(self, activity: str, *options: str, action: Optional[str]) -> None:
        """
        Invoke an intent associated with this service by broadcasting an event
        :param activity: activity that handles the intent
        :param options: string list of options to supply to "am broadcast" command
        :param action: if not None, invoke specific action
        :return:
        """
        if not activity:
            raise Exception("Must provide an activity for ServiceApplication")

        activity = f"{self.package_name}/{activity}"
        options = tuple(f'"{item}"' for item in options)
        if action:
            options = ("-a", action) + options
        self.device.execute_remote_cmd("shell", "am", "broadcast", "-n", activity, *options,
                                       timeout=Device.TIMEOUT_LONG_ADB_CMD)


class AsyncServiceApplication(AsyncApplication):
    """
    Class representing an Android application that is specifically a service
    """

    async def start(self, activity: str,
                    *options: str, intent: Optional[str] = None, foreground: bool = True,
                    timeout: Optional[float] = None) -> None:

        # noinspection SpellCheckingInspection
        """
                invoke an intent associated with this service by calling start the service

                :param options: string list of options to supply to "am startservice" command
                :param activity: activity handles the intent
                :param intent: if not None, invoke specific intent otherwise invoke default intent
                :param foreground: whether to start in foreground or not (Android O+
                    does not allow background starts any longer)
                :param timeout: optional timeout upon which TimeoutError is raised

                :raises: asyncio.TimeoutError if timeout is specified and reached during execution
                """
        if activity and activity.startswith("."):
            activity = f"{self.package_name}{activity}"
        # embellish to fully qualified name as Android expects
        activity = \
            f"{self.package_name}/{activity}" if activity else f"{self.package_name}/{self.package_name}.MainActivity"
        options = tuple(f'"{item}"' for item in options)
        if intent:
            options = ("-a", intent) + options
        if foreground and self.device.api_level and self.device.api_level >= 26:
            await self.device.execute_remote_cmd_async("shell", "am", "start-foreground-service", "-n", activity,
                                                       *options, timeout=timeout)
        else:
            # noinspection SpellCheckingInspection
            await self.device.execute_remote_cmd_async("shell", "am", "startservice", "-n", activity, *options,
                                                       timeout=timeout)

    async def broadcast(self, activity: str, *options: str, action: Optional[str],
                        timeout: Optional[float] = Device.TIMEOUT_LONG_ADB_CMD) -> None:
        """
        Invoke an intent associated with this service by broadcasting an event

        :param activity: activity that handles the intent
        :param options: string list of options to supply to "am broadcast" command
        :param action: if not None, invoke specific action
        :param timeout: command will timeout after this many seconds

        :raises asyncio.TimeoutError if command execution time exceeds timeout
        """
        if not activity:
            raise Exception("Must provide an activity for ServiceApplication")

        activity = f"{self.package_name}/{activity}"
        options = tuple(f'"{item}"' for item in options)
        if action:
            options = ("-a", action) + options
        await self.device.execute_remote_cmd_async("shell", "am", "broadcast", "-n", activity, *options,
                                                   timeout=timeout)


class TestApplication(Application):
    """
    Class representing an Android test application installed on a remote device
    """

    def __init__(self, device: Device, manifest: AXMLParser):
        """
        Create an instance of a remote test app and the interface to manipulate it.
        It is recommended to  create instances via the class-method `install`:

        :param device: which device app resides on
        :param manifest: manifest describing the apk package to be installed

        >>> device = Device("some_serial_id")
        >>> test_app = TestApplication.from_apk("some.apk", device)
        >>> test_app.run()
        """
        super(TestApplication, self).__init__(device=device, manifest=manifest)
        valid = (hasattr(manifest, "instrumentation") and (manifest.instrumentation is not None) and
                 bool(manifest.instrumentation.target_package) and bool(manifest.instrumentation.runner))
        if not valid:
            raise Exception("Test application's manifest does not specify proper instrumentation element."
                            "Are you sure this is a test app")
        self._runner: str = manifest.instrumentation.runner
        self._target_application = Application(device,
                                               manifest={'package_name': manifest.instrumentation.target_package})
        self._permissions = manifest.permissions

    @property
    def target_application(self) -> Application:
        """
        :return: target application under test for this test app
        """
        return self._target_application

    @property
    def runner(self) -> str:
        """
        :return: runner associated with this test app
        """
        return self._runner

    def list_runners(self) -> List[str]:
        """
        :return: all test runners available for that package
        """
        items = []
        for line in self.device.list_instrumentation():
            if line and self.package_name in line:
                runner = line.replace('instrumentation:', '').split(' ')[0].strip()
                items.append(runner)
        return items

    async def run(self, *options: str) -> Device.AsyncProcessContext:
        """
        Run an instrumentation test package, yielding lines from std output.
        NOTE: Return value is instance of a class that implement __aenter__ and __aexit__ (i.e. is
          an AsyncContextManager instance and should be used in an async context as shown in example below

        :param options: arguments to pass to instrument command
        :returns: return coroutine wrapping an asyncio context manager for iterating over lines
        :raises Device.CommandExecutionFailureException with non-zero return code information on non-zero exit status

        >>> device = Device("some_id")
        ... app = TestApplication.from_apk("some.apk", device)
        ...
        ... async def run():
        ...     async with app.run() as proc:
        ...         async  for line in proc.output():
        ...             print(line)
        """
        found = False
        items = self.device.list_installed_packages()
        for item in items:
            if self._target_application.package_name == item:
                found = True
                break
        if not found:
            raise Exception("App under test, as designated by this test app's manifest, is not installed!")
        # surround each arg with quotes to preserve spaces in any arguments when sent to remote device:
        options = tuple('"%s"' % arg if not arg.startswith('"') else arg for arg in options)
        return self.device.monitor_remote_cmd("shell", "am", "instrument", "-w", *options, "-r",
                                              "/".join([self._package_name, self._runner]))

    def run_orchestrated(self, *options: str) -> Device.AsyncProcessContext:
        """
        Run an instrumentation test package via Google's test orchestrator that

        :param options: arguments to pass to instrument command
        :returns: coroutine wrapping an asyncio context manager for iterating over lines
        :raises: Device.CommandExecutionFailureException with non-zero return code information on non-zero exit status
        :raises: Exception if supporting apks for orchestrated runs are not installed

        >>> device = Device("some_id")
        ... app = TestApplication.from_apk("some.apk", device)
        ...     async with app.run_orchestrated() as proc:
        ...         async  for line in proc.output():
        ...             print(line)

        """
        packages = self.device.list_installed_packages()
        if not {'android.support.test.services', 'android.support.test.orchestrator'} < set(packages):
            raise Exception("Must install both test-services-<version>.apk and orchestrator-<version>.apk to run "
                            + "under Google's Android Test Orchestrator")
        if self._target_application.package_name not in self.device.list_installed_packages():
            raise Exception("App under test, as designated by this test app's manifest, is not installed!")
        options_text = " ".join(['"%s"' % arg if not arg.startswith('"') and not arg.startswith("-") else arg
                                 for arg in options])
        target_instrumentation = '/'.join([self._package_name, self._runner])
        # noinspection SpellCheckingInspection
        return self.device.monitor_remote_cmd(
            "shell",
            "CLASSPATH=$(pm path android.support.test.services) "
            + "app_process / android.support.test.services.shellexecutor.ShellMain am instrument "
            + f"-r -w -e -v -targetInstrumentation {target_instrumentation} {options_text} "
            + "android.support.test.orchestrator/android.support.test.orchestrator.AndroidTestOrchestrator")

    @classmethod
    def from_apk(cls: Type[_TTestApp], apk_path: str, device: Device, as_upgrade: bool = False,
                 as_test_app: bool = False,
                 timeout: Optional[int] = Device.TIMEOUT_LONG_ADB_CMD) -> _TTestApp:
        parser = AXMLParser.parse(apk_path)
        args = []
        if as_upgrade:
            args.append("-r")
        if as_test_app:
            args.append("-t")
        cls._install(device, apk_path, *args, timeout=timeout)
        return cls(device, parser)


class AsyncTestApplication(AsyncApplication):
    """
    Class representing an Android test application installed on a remote device
    """

    def __init__(self, device: Device, manifest: AXMLParser):
        """
        Create an instance of a remote test app and the interface to manipulate it.
        It is recommended to  create instances via the class-method `install`:

        :param device: which device app resides on
        :param manifest: manifest describing the apk package to be installed

        >>> device = Device("some_serial_id")
        >>> test_app = TestApplication.from_apk("some.apk", device)
        >>> test_app.run()
        """
        super().__init__(device=device, manifest=manifest)
        valid = (hasattr(manifest, "instrumentation") and (manifest.instrumentation is not None) and
                 bool(manifest.instrumentation.target_package) and bool(manifest.instrumentation.runner))
        if not valid:
            raise Exception("Test application's manifest does not specify proper instrumentation element."
                            "Are you sure this is a test app")
        self._runner: str = manifest.instrumentation.runner
        self._target_application = Application(device,
                                               manifest={'package_name': manifest.instrumentation.target_package})
        self._permissions = manifest.permissions

    @property
    def target_application(self) -> Application:
        """
        :return: target application under test for this test app
        """
        return self._target_application

    @property
    def runner(self) -> str:
        """
        :return: runner associated with this test app
        """
        return self._runner

    async def iterate_runners(self) -> AsyncIterator[str]:
        """
        :return: all test runners available for that package
        """
        async for runner in self.device.iterate_instrumentation():
            if self.package_name in runner:
                yield runner.replace('instrumentation', '').split(' ')[0].strip()

    async def run(self, *options: str) -> Device.AsyncProcessContext:
        """
        Run an instrumentation test package, yielding lines from std output.
        NOTE: Return value is instance of a class that implement __aenter__ and __aexit__ (i.e. is
          an AsyncContextManager instance and should be used in an async context as shown in example below

        :param options: arguments to pass to instrument command
        :returns: return coroutine wrapping an asyncio context manager for iterating over lines
        :raises Device.CommandExecutionFailureException with non-zero return code information on non-zero exit status

        >>> device = Device("some_id")
        ... app = TestApplication.from_apk("some.apk", device)
        ...
        ... async def run():
        ...     async with app.run() as proc:
        ...         async  for line in proc.output():
        ...             print(line)
        """
        found = False
        async for item in self.device.iterate_installed_packages(timeout=Device.TIMEOUT_ADB_CMD):
            if self._target_application.package_name == item:
                found = True
                break
        if not found:
            raise Exception("App under test, as designated by this test app's manifest, is not installed!")
        # surround each arg with quotes to preserve spaces in any arguments when sent to remote device:
        options = tuple('"%s"' % arg if not arg.startswith('"') else arg for arg in options)
        return self.device.monitor_remote_cmd("shell", "am", "instrument", "-w", *options, "-r",
                                              "/".join([self._package_name, self._runner]))

    def run_orchestrated(self, *options: str) -> Device.AsyncProcessContext:
        """
        Run an instrumentation test package via Google's test orchestrator that

        :param options: arguments to pass to instrument command
        :returns: coroutine wrapping an asyncio context manager for iterating over lines
        :raises: Device.CommandExecutionFailureException with non-zero return code information on non-zero exit status
        :raises: Exception if supporting apks for orchestrated runs are not installed

        >>> device = Device("Some_id")
        ... app = AsyncTestApplication.from_apk("some.apk", device)
        ...     async with app.run_orchestrated() as proc:
        ...         async  for line in proc.output():
        ...             print(line)

        """
        packages = self.device.list_installed_packages()
        if not {'android.support.test.services', 'android.support.test.orchestrator'} < set(packages):
            raise Exception("Must install both test-services-<version>.apk and orchestrator-<version>.apk to run "
                            + "under Google's Android Test Orchestrator")
        if self._target_application.package_name not in self.device.list_installed_packages():
            raise Exception("App under test, as designated by this test app's manifest, is not installed!")
        options_text = " ".join(['"%s"' % arg if not arg.startswith('"') and not arg.startswith("-") else arg
                                 for arg in options])
        target_instrumentation = '/'.join([self._package_name, self._runner])
        # noinspection SpellCheckingInspection
        return self.device.monitor_remote_cmd(
            "shell",
            "CLASSPATH=$(pm path android.support.test.services) "
            + "app_process / android.support.test.services.shellexecutor.ShellMain am instrument "
            + f"-r -w -e -v -targetInstrumentation {target_instrumentation} {options_text} "
            + "android.support.test.orchestrator/android.support.test.orchestrator.AndroidTestOrchestrator")

    # noinspection PyMethodOverriding
    @classmethod
    async def from_apk(cls: Type[_TTestApp], apk_path: str, device: Device, as_upgrade: bool = False,
                       as_test_app: bool = False,
                       timeout: Optional[int] = Device.TIMEOUT_LONG_ADB_CMD) -> _TTestApp:
        parser = AXMLParser.parse(apk_path)
        args = []
        if as_upgrade:
            args.append("-r")
        if as_test_app:
            args.append("-t")
        await cls._install(device, apk_path, *args, timeout=timeout)
        return cls(device, parser)
