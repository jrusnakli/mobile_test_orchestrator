import os

import asyncio.subprocess
import logging
import subprocess
import time

from apk_bitminer.parsing import AXMLParser
from contextlib import suppress
from typing import List, TypeVar, Type, Optional, AsyncGenerator

from .device import Device, RemoteDeviceBased, TIMEOUT_LONG_ADB_CMD, TIMEOUT_ADB_CMD

log = logging.getLogger(__name__)

# for returning instance of "cls" in install class-method.  Recommended way of doing this, but there
# may be better way in later Python 3 interpreters?
_Ty = TypeVar('T', bound='TrivialClass')


class Application(RemoteDeviceBased):
    """
    Defines an application installed on a remotely USB-connected device. Provides an interface for stopping,
    starting, etc. for an application

    :param package_name: package name of app
    :param device: which device app resides on

    It is recommended to  create instances via the class-method `install`:

    >>> device = Device("some_serial_id", "/path/to/adb")
    >>> app = Application.install("some.apk", device)
    >>> app.grant_permissions(["android.permission.WRITE_EXTERNAL_STORAGE"])
    """
    class InsufficientStorageError(Exception):
        pass

    ERROR_MSG_INSUFFICIENT_STORAGE = "INSTALL_FAILED_INSUFFICIENT_STORAGE"

    SLEEP_GRANT_PERMISSION = 4

    def __init__(self, package_name: str, device: Device):
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

    @classmethod
    def _verify_install(cls, appl_path: str, package: str, device: Device):
        """
        verify installation of an app, taking a screenshot on failure

        :param appl_path: For logging which apk failed to install (upon any failure)
        :param package: pacakge name of app
        :param device: remote device app is being installed on

        "raises" Exception if failure to verify
        """
        packages = device.list_installed_packages()
        if package not in packages:
            # some devices (may??) need time for package install to be detected by system
            time.sleep(device.SLEEP_PKG_INSTALL)
            packages = device.list_installed_packages()
        if package not in packages:
            if package is not None:
                screenshot_dir = "test-screenshots"
                if not os.path.exists(screenshot_dir):
                    os.makedirs(screenshot_dir)
                device.take_screenshot("install_failure-%s.png" % package)
                log.error("Did not find installed package %s;  found: %s" % (package, packages))
                log.error("Device failure to install %s on model %s;  install status succeeds,"
                          "but package not found on device" %
                          (appl_path, device.model))
            raise Exception("Failed to verify installation of app '%s', event though output indicated otherwise" %
                            package)
        else:
            log.info("Package %s installed" % str(package))

    @classmethod
    def _install_common(cls, apk_path: str, device: Device, as_upgrade, package: str) -> None:
        if not as_upgrade:
            # avoid unnecessary conflicts with older certs and crap:
            with suppress(Exception):
                device.execute_remote_cmd("uninstall", package, capture_stdout=False)

        extra_commands = Device.special_install_instructions.get(device.model)

        # bypass this by executing extra commands if needed (but only once 100% of package is installed)
        def execute_extras():
            """
            Execute additional commands post-install of app required by some devices to do non-standard
            user interaction to properly install app over USB (for example)
            """
            for command, sleep_time in extra_commands['post_install_cmds']:
                if sleep_time:
                    time.sleep(sleep_time)
                try:
                    device.execute_remote_cmd(*command, timeout=TIMEOUT_LONG_ADB_CMD, capture_stdout=False)
                except Exception as e:
                    log.error(str(e))

        async def execute():
            """
            Execute the installation of the app, monitoring output for completion in order to invoke
            any extra commands
            """
            if as_upgrade:
                cmd = ("install", "-r", apk_path)
            else:
                cmd = ("install", apk_path)
            async with await device.execute_remote_cmd_async(*cmd, unresponsive_timeout=TIMEOUT_ADB_CMD) as line_generator:

                async for msg in line_generator:
                    log.debug(msg)

                    if cls.ERROR_MSG_INSUFFICIENT_STORAGE in msg:
                        raise cls.InsufficientStorageError("Insufficient storage for install of %s" %
                                                           apk_path)
                    # some devices have non-standard pop-ups that must be cleared by accepting usb installs
                    # (non-standard Android):
                    if extra_commands and msg and ("100%%" in msg or "pkg:" in msg or "Success" in msg):
                        # this is when pop-up shows up (roughly)
                        log.info("Execution extra commands on install...")
                        # successfully pushed, now execute special instructions, which
                        # is often a tap event to accept a pop-up
                        execute_extras()

        async def timer():
            """
            timer to timeout if install taking too long
            """
            await asyncio.wait_for(execute(), TIMEOUT_LONG_ADB_CMD)

        asyncio.get_event_loop().run_until_complete(timer())
        # on some devices, a pop-up may prevent successful install even if return code from adb install showed
        # success, so must explicitly verify the install was successful:
        log.debug("Verifying install...")
        cls._verify_install(apk_path, package, device)  # raises exception on failure to verify

    # noinspection PyCallingNonCallable
    @classmethod
    def install(cls: Type[_Ty], apk_path: str, device: Device, as_upgrade=False) -> _Ty:
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
        cls._install_common(apk_path, device, as_upgrade, package)
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

    def grant_permissions(self, permissions: List[str]):
        """
        Grant permissions for a package on a device

        :param permissions: string list of Android permissions to be granted

        :return: the set of permissions successfully granted
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
            return
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

    def monkey(self, count: int = 1):
        """
        Run monkey against application
        """
        cmd = ["shell", "monkey", "-p", self._package_name, "-c", "android.intent.category.LAUNCHER", str(count)]
        self.device.execute_remote_cmd(*cmd, capture_stdout=False)

    def stop(self, force: bool = True):
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

    def clear_data(self):
        """
        clears app data for given package
        """
        self.device.execute_remote_cmd("shell", "pm", "clear", self.package_name, capture_stdout=False)


class ServiceApplication(Application):
    """
    Class representing an Android application that is specifically a service
    """

    def start(self, activity: str, *options: str, intent: Optional[str] = None, foreground: bool = False):
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

    :param package_name: package name of app
    :param device: which device app resides on
    :param runner: runner to use when running tests

    It is recommended to  create instances via the class-method `install`:

    >>> device = Device("some_serial_id", "/path/to/adb")
    >>> test_app = TestApplication.install("some.apk", device)
    >>> test_app.run()
    """

    def __init__(self, package_name: str, device: Device, target_package, runner):
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
        for line in self.device.list("instrumentation"):
            if line and self.package_name in line:
                log.info("ADB: matched on line : %s" % line)
                runner = line.replace('instrumentation:', '').split(' ')[0].strip()
                items.append(runner)
        return items

    async def run(self, *options: str, unresponsive_timeout=TIMEOUT_LONG_ADB_CMD) -> AsyncGenerator[str, None]:
        """
        Run an instrumentation test package, yielding lines from std output

        :param options: arguments to pass to instrument command
        :param unresponsive_timeout: raise Timeout if there is no output from instrument command for longer than
           this period of time

        :returns: return coroutine that in returns an async generator yielding line-by-line output

        :raises Device.CommandExecutionFailureException with non-zero return code information on non-zero exit status

        >>> device = Device("some-id", "/path/to/adb")
        ... app = TestApplication.install("some.apk", device)
        ...
        ... async def run():
        ...     async  for line in app.run():
        ...         print(line)
        """
        if self._target_application.package_name not in self.device.list_installed_packages():
            raise Exception("App under test, as designated by this test app's manifest, is not installed!")
        # surround each arg with quotes to preserve spaces in any arguments when sent to remote device:
        options = ['"%s"' % arg if not arg.startswith('"') else arg for arg in options]
        async with await self.device.execute_remote_cmd_async("shell", "am", "instrument", "-w", "-r",
                                                              *options,
                                                              "/".join([self._package_name, self._runner]),
                                                              unresponsive_timeout=unresponsive_timeout) as line_generator:
            async for line in line_generator:
                yield line

    @classmethod
    def install(cls, apk_path: str, device: Device, as_upgrade=False) -> "TestApplication":
        """
        install apk as a test application

        :param apk_path: path to test apk (containing a runner)
        :param device: device to install on
        :param as_upgrade: whether to install as upgrade or not

        :return: `TestApplication` of installed apk
        """
        parser = AXMLParser.parse(apk_path)
        Application._install_common(apk_path, device, as_upgrade, parser.package_name)
        # The manifest of the package should contain an instrumentation section, if not, it is not
        # a test apk:
        valid = (hasattr(parser, "instrumentation") and (parser.instrumentation is not None) and
                 bool(parser.instrumentation.target_package) and bool(parser.instrumentation.runner))
        if not valid:
            raise Exception("Test application's manifest does not specify proper instrumentation element."
                            "Are you sure this is a test app")
        return cls(parser.package_name, device, parser.instrumentation.target_package,
                   parser.instrumentation.runner)
