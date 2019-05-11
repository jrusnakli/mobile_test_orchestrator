import logging
import subprocess
import time

from apk_bitminer.parsing import AXMLParser
from typing import List, TypeVar, Type, Optional, AsyncContextManager, Union

from .device import Device, RemoteDeviceBased

log = logging.getLogger(__name__)

# for returning instance of "cls" in install class-method.  Recommended way of doing this, but there
# may be better way in later Python 3 interpreters?
_Ty = TypeVar('T', bound='Application')


class Application(RemoteDeviceBased):
    """
    Defines an application installed on a remotely USB-connected device. Provides an interface for stopping,
    starting and such for an applicatoin
    """

    SLEEP_GRANT_PERMISSION = 4

    def __init__(self, package_name: str, device: Device):
        """
        Create an instance of a remote app and the interface to manipulate it.
        It is recommended to  create instances via the class-method `install`:

        >>> device = Device("some_serial_id", "/path/to/adb")
        >>> app = asyncio.wait(Application.from_apk("some.apk", device))
        >>> app.grant_permissions(["android.permission.WRITE_EXTERNAL_STORAGE"])

        :param package_name: package name of app
        :param device: which device app resides on
        """
        super(Application, self).__init__(device)
        self._package_name = package_name
        self._version = None  # loaded on-demand first time self.version called

    @property
    def package_name(self):
        """
        :return: Android package name associated with this app
        """
        return self._package_name

    @property
    def version(self):
        """
        :return: version of this app
        """
        if self._version is None:
            self._version = self.device.get_version(self.package_name)
        return self._version

    @property
    def pid(self):
        """
        :return: pif of app if running (either in foreground or background) or None if not running
        """
        stdout = self.device.execute_remote_cmd("shell", "pidof", "-s", self.package_name, capture_stdout=True)
        split_output = stdout.splitlines()
        if len(split_output) > 0:
            return split_output[0].strip()
        return None

    @classmethod
    async def from_apk_async(cls: Type[_Ty], apk_path: str, device: Device, as_upgrade=False) -> _Ty:
        """
        Install provided application

        :param apk_path: path to apk
        :param device: device to install on
        :param as_upgrade: whether to install as upgrade or not

        :return: remote installed application

        :raises: Exception if failure ot install or verify installation
        """
        parser = AXMLParser.parse(apk_path)
        package = parser.package_name
        await device.install(apk_path, as_upgrade, package)
        return cls(package, device)

    @classmethod
    def from_apk(cls: Type[_Ty], apk_path: str, device: Device, as_upgrade=False) -> _Ty:
        """
        Install provided application

        :param apk_path: path to apk
        :param device: device to install on
        :param as_upgrade: whether to install as upgrade or not

        :return: remote installed application

        :raises: Exception if failure ot install or verify installation
        """
        parser = AXMLParser.parse(apk_path)
        package = parser.package_name
        device.install_synchronous(apk_path, as_upgrade, package)
        return cls(package, device)

    def uninstall(self):
        """
        Uninstall this app from remote device.
        """
        self.stop()
        try:
            self.device.execute_remote_cmd("uninstall", self.package_name, capture_stdout=False)
        except subprocess.TimeoutExpired:
            log.warning("adb command froze on uninstall.  Ignoring issue as device specific")
        except Exception as e:
            if self.package_name in self.device.list_installed_packages():
                log.error("Failed to uninstall app %s [%s]", (self.package_name, str(e)))

    def grant_permissions(self, permissions: List[str]) -> List[str]:
        """
        Grant permissions for a package on a device

        :param permissions: string list of Android permissions to be granted

        :return: the list of permissions successfully granted
        """
        succeeded = []
        # workaround for xiaomi:
        if 'xiaomi' in self.device.model.lower():
            # gives initial time for UI to appear, otherwise can bring up wrong window and block tests
            time.sleep(self.SLEEP_GRANT_PERMISSION)
        permissions_filtered = list(set([p.strip() for p in permissions if
                                         p not in Device.NORMAL_PERMISSIONS]))
        if not permissions_filtered:
            log.info("Permissions %s already requested or no 'dangerous' permissions requested, so nothing to do" %
                     permissions)
            return []
        # note "block grants" do not work on all Android devices, so grant 'em one-by-one
        for p in permissions_filtered:
            try:
                self.device.execute_remote_cmd("shell", "pm", "grant", self.package_name, p, capture_stdout=False)
            except Exception as e:
                log.error("Failed to grant permission %s for package %s [%s]" % (p, self.package_name, str(e)))
            succeeded.append(p)
        return succeeded

    def start(self, activity: Optional[str], *options: str, intent: Optional[str] = None) -> None:
        """
        start an app on the device

        :param activity: which Android Activity to invoke on start of app
        :param intent: which Intent to invoke, or None for default intent
        :param options: string list of options to pass on to the "am start" command on the remote devie, or None

        """
        # embellish to fully qualified name as Android expects
        activity = "%s/%s" % (self.package_name, activity) if activity else "%s/.MainActivity" % self.package_name
        if intent:
            options = ("-a", intent, *options)
        self.device.execute_remote_cmd("shell", "am", "start", "-n", activity, *options, capture_stdout=False)

    def monkey(self, count: int = 1) -> None:
        """
        Run monkey against application
        """
        cmd = ["shell", "monkey", "-p", self._package_name, "-c", "android.intent.category.LAUNCHER", str(count)]
        self.device.execute_remote_cmd(*cmd, capture_stdout=False)

    def stop(self, force: bool = True) -> None:
        """
        stop an app on the device
        """
        try:
            if force:
                self.device.execute_remote_cmd("shell", "am", "force-stop", self.package_name, capture_stdout=False)
            else:
                self.device.execute_remote_cmd("shell", "am", "stop", self.package_name, capture_stdout=False)
        except Exception as e:
            log.error("Failed to force-stop app %s: error; %s" % (self.package_name, str(e)))

    def clean_kill(self) -> None:
        """
        Running this command to close the app is equivalent to the app being backgrounded, and then having the system
        kill the app to clear up resources for other apps. It is also equivalent to closing the app from the "Recent
        Apps" menu.

        Subsequent launches of the app will need to recreate the app from scratch, and is considered a
        cold app launch.
        NOTE: Currently appears to only work with Android 9.0 devices
        """
        self.device.input("KEYCODE_HOME")
        # Sleep for a second to allow for app to be backgrounded
        # TODO: Get rid of sleep call as this is bad practice.
        time.sleep(1)
        if not self.device.home_screen_active:
            raise Exception(f"Failed to background current foreground app. Cannot complete app closure.")
        self.device.execute_remote_cmd("shell", "am", "kill", self.package_name)
        if self.pid is not None:
            raise Exception(
                f"Detected app process is still running, despite background command succeeding. App closure failed.")

    def clear_data(self) -> None:
        """
        clears app data for given package
        """
        self.device.execute_remote_cmd("shell", "pm", "clear", self.package_name, capture_stdout=False)


class ServiceApplication(Application):
    """
    Class representing an Android application that is specifically a service
    """

    def start(self, activity: str, *options: str, intent: Optional[str] = None, foreground: bool = True):
        """
        invoke an intent associated with this service

        :param options: string list of options to supply to "am startservice" command
        :param activity: activity defaulting to "MainActivity" if None
        :param intent: if not None, invoke specific intent otherwise invoke default intent
        :param foreground: whether to start in foreground or not (Android O+
            does not allow background starts any longer)
        """
        activity = "%s/%s" % (self.package_name, activity)
        options = ["\"%s\"" % item for item in options]
        if intent:
            options = ["-a", intent] + options
        if foreground:
            self.device.execute_remote_cmd("shell", "am", "start-foreground-service", "-n", activity, *options, capture_stdout=False)
        else:
            self.device.execute_remote_cmd("shell", "am", "startservice", "-n", activity, *options, capture_stdout=False)


class TestApplication(Application):
    """
    Class representing an Android test application installed on a remote device
    """

    def __init__(self, package_name: str, device: Device, target_package, runner):
        """
        Create an instance of a remote test app and the interface to manipulate it.
        It is recommended to  create instances via the class-method `install`:

        >>> device = Device("some_serial_id", "/path/to/adb")
        >>> test_app = TestApplication.install("some.apk", device)
        >>> test_app.run()

        :param package_name: package name of app
        :param device: which device app resides on
        :param runner: runner to use when running tests
        """
        super(TestApplication, self).__init__(package_name, device)
        self._runner = runner
        self._target_application = Application(target_package, device)

    @property
    def target_application(self):
        """
        :return: target application under test for this test app
        """
        return self._target_application

    @property
    def runner(self):
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
                log.info("ADB: matched on line : %s" % line)
                runner = line.replace('instrumentation:', '').split(' ')[0].strip()
                items.append(runner)
        return items

    async def run(self, *options: str, loop=None, unresponsive_timeout=None,
                  max_test_time: Union[float, None] = None) -> AsyncContextManager:
        """
        Run an instrumentation test package, yielding lines from std output

        :param options: arguments to pass to instrument command
        :param loop: event loop to execute under, or None for default event loop
        :param unresponsive_timeout: time to wait for run to complete, or None to wait indefinitely
        :param max_test_time: maximum test time before Timeout is raised, or None for noe timeout

        :returns: return coroutine wrapping an asyncio context manager for iterating over lines

        :raises Device.CommandExecutionFailureException with non-zero return code information on non-zero exit status

        >>> app = TestApplication.fromApk("some.apk", device)
        ...
        ... async def run():
        ...     async  for line in app.run():
        ...         print(line)
        """
        if self._target_application.package_name not in self.device.list_installed_packages():
            raise Exception("App under test, as designatee by this test app's manifest, is not installed!")
        # surround each arg with quotes to preserve spaces in any arguments when sent to remote device:
        options = ['"%s"' % arg if not arg.startswith('"') else arg for arg in options]
        return await self.device.execute_remote_cmd_async("shell", "am", "instrument", "-w", *options, "-r",
                                                          "/".join([self._package_name, self._runner]),
                                                          proc_completion_timeout=unresponsive_timeout,
                                                          unresponsive_timeout=max_test_time,
                                                          loop=loop)

    @classmethod
    async def from_apk_async(cls, apk_path: str, device: Device, as_upgrade=False) -> "TestApplication":
        """
        install apk as a test application on the given device

        :param apk_path: path to test apk (containing a runner)
        :param device: device to install on
        :param as_upgrade: whether to install as upgrade or not

        :return: `TestApplication` of installed apk
        """
        parser = AXMLParser.parse(apk_path)
        # The manifest of the package should contain an instrumentation section, if not, it is not
        # a test apk:
        valid = (hasattr(parser, "instrumentation") and (parser.instrumentation is not None) and
                 bool(parser.instrumentation.target_package) and bool(parser.instrumentation.runner))
        if not valid:
            raise Exception("Test application's manifest does not specify proper instrumentation element."
                            "Are you sure this is a test app")
        await device.install(apk_path, as_upgrade, parser.package_name)
        return cls(parser.package_name, device, parser.instrumentation.target_package, parser.instrumentation.runner)

    @classmethod
    def from_apk(cls, apk_path: str, device: Device, as_upgrade=False) -> "TestApplication":
        """
        install apk as a test application on the given device

        :param apk_path: path to test apk (containing a runner)
        :param device: device to install on
        :param as_upgrade: whether to install as upgrade or not

        :return: `TestApplication` of installed apk
        """
        parser = AXMLParser.parse(apk_path)
        # The manifest of the package should contain an instrumentation section, if not, it is not
        # a test apk:
        valid = (hasattr(parser, "instrumentation") and (parser.instrumentation is not None) and
                 bool(parser.instrumentation.target_package) and bool(parser.instrumentation.runner))
        if not valid:
            raise Exception("Test application's manifest does not specify proper instrumentation element."
                            "Are you sure this is a test app")
        device.install_synchronous(apk_path, as_upgrade, parser.package_name)
        return cls(parser.package_name, device, parser.instrumentation.target_package, parser.instrumentation.runner)
