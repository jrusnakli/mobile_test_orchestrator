import asyncio
import datetime
import logging
import os
import re
import subprocess
import time
from asyncio import AbstractEventLoop
from contextlib import suppress, asynccontextmanager
from types import TracebackType
from typing import List, Tuple, Dict, Optional, AsyncContextManager, Union, Callable, IO, Any, AsyncIterator, Type, \
    AnyStr

from apk_bitminer.parsing import AXMLParser  # type: ignore

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)


class Device(object):
    """
    Class for interacting with a device via Google's adb command. This is intended to be a direct bridge to the same
    functionality as adb, with minimized embellishments
    """

    # These packages may appear as running when looking at the activities in a device's activity stack. The running
    # of these packages do not affect interaction with the app under test. With the exception of the Samsung
    # MtpApplication (pop-up we can't get rid of that asks the user to update their device), they are also not visible
    # to the user. We keep a list of them so we know which ones to disregard when trying to retrieve the actual
    # foreground application the user is interacting with.
    SILENT_RUNNING_PACKAGES = ["com.samsung.android.mtpapplication", "com.wssyncmldm", "com.bitbar.testdroid.monitor"]

    class InsufficientStorageError(Exception):
        """
        Raised on insufficient storage on device (e.g. in install)
        """

    ERROR_MSG_INSUFFICIENT_STORAGE = "INSTALL_FAILED_INSUFFICIENT_STORAGE"

    override_ext_storage = {
        # TODO: is this still needed (in lieu of updates to OS SW):
        "Google Pixel": "/sdcard"
    }

    SLEEP_SET_PROPERTY = 2
    SLEEP_PKG_INSTALL = 5

    # in seconds:
    TIMEOUT_SCREEN_CAPTURE = 2 * 60
    TIMEOUT_ADB_CMD = 10
    TIMEOUT_LONG_ADB_CMD = 4 * 60

    NORMAL_PERMISSIONS = [
        "ACCESS_LOCATION_EXTRA_COMMANDS",
        "ACCESS_NETWORK_STATE",
        "ACCESS_NOTIFICATION_POLICY",
        "ACCESS_WIFI_STATE",
        "BLUETOOTH",
        "BLUETOOTH_ADMIN",
        "BROADCAST_STICKY",
        "CHANGE_NETWORK_STATE",
        "CHANGE_WIFI_MULTICAST_STATE",
        "CHANGE_WIFI_STATE",
        "DISABLE_KEYGUARD",
        "EXPAND_STATUS_BAR",
        "GET_PACKAGE_SIZE",
        "INSTALL_SHORTCUT",
        "INTERNET",
        "KILL_BACKGROUND_PROCESSES",
        "MODIFY_AUDIO_SETTINGS",
        "NFC",
        "READ_SYNC_SETTINGS",
        "READ_SYNC_STATS",
        "RECEIVE_BOOT_COMPLETED",
        "REORDER_TASKS",
        "REQUEST_IGNORE_BATTERY_OPTIMIZATIONS",
        "REQUEST_INSTALL_PACKAGES",
        "SET_ALARM",
        "SET_TIME_ZONE",
        "SET_WALLPAPER",
        "SET_WALLPAPER_HINTS",
        "TRANSMIT_IR",
        "UNINSTALL_SHORTCUT",
        "USE_FINGERPRINT",
        "VIBRATE",
        "WAKE_LOCK",
        "WRITE_SYNC_SETTINGS",
    ]

    WRITE_EXTERNAL_STORAGE_PERMISSION = "android.permission.WRITE_EXTERNAL_STORAGE"

    class CommandExecutionFailureException(Exception):

        def __init__(self, return_code: int, msg: str):
            super().__init__(msg)
            self._return_code = return_code

        @property
        def return_code(self) -> int:
            return self._return_code

    @classmethod
    def set_default_adb_timeout(cls, timeout: int) -> None:
        """
        :param timeout: timeout in seconds
        """
        cls.TIMEOUT_ADB_CMD = timeout

    @classmethod
    def set_default_long_adb_timeout(cls, timeout: int) -> None:
        """
        :param timeout: timeout in seconds
        """
        cls.TIMEOUT_LONG_ADB_CMD = timeout

    def __init__(self, device_id: str, adb_path: str):
        """
        :param adb_path: path to the adb command on the host
        :param device_id: serial id of the device as seen by host (e.g. via 'adb devices')

        :raises FileNotFoundError: if adb path is invalid
        """
        if not os.path.isfile(adb_path):
            raise FileNotFoundError(f"Invalid adb path given: '{adb_path}'")
        self._device_id = device_id
        self._adb_path = adb_path

        # These will be populated on as-needed basis and cached through the associated @property's
        self._model: Optional[str] = None
        self._brand: Optional[str] = None
        self._manufacturer: Optional[str] = None

        self._name: Optional[str] = None
        self._ext_storage = Device.override_ext_storage.get(self.model)
        self._device_server_datetime_offset: Optional[datetime.timedelta] = None
        self._lock = asyncio.Semaphore()
        self._api_level: Optional[int] = None

    @property
    def api_level(self) -> int:
        """
        :return: api level of device
        """
        if self._api_level:
            return self._api_level

        device_api_level = self.get_system_property("ro.build.version.sdk")

        if device_api_level:
            self._api_level = int(device_api_level)
        else:
            # assume default setting of 28 :-(
            log.warning("Unable to determine api level, assuming 28")
            self._api_level = 28

        return self._api_level

    @property
    def device_server_datetime_offset(self) -> datetime.timedelta:
        """
        :return: Returns a datetime.timedelta object that represents the difference between the server/host datetime
            and the datetime of the Android device
        """
        if self._device_server_datetime_offset is not None:
            return self._device_server_datetime_offset

        # compute offset of clocks between host and device (roughly)
        def _device_datetime() -> Optional[datetime.datetime]:
            # There is a variable on Android devices that holds the current epoch time of the device. We use that to
            # retrieve the device's datetime so we can easily calculate the difference of the start time from
            # other times during the test.
            with suppress(Exception):
                output = self.execute_remote_cmd("shell", "echo", "$EPOCHREALTIME", capture_stdout=True)
                for msg_ in output.splitlines():
                    if re.search(r"^\d+\.\d+$", msg_):
                        return datetime.datetime.fromtimestamp(float(msg_.strip()))

            log.error("Unable to get datetime from device. No offset will be computed for timestamps")
            return None

        device_datetime = _device_datetime()
        self._device_server_datetime_offset = datetime.datetime.now() - \
            device_datetime if device_datetime is not None else datetime.timedelta()
        return self._device_server_datetime_offset

    @property
    def device_id(self) -> str:
        """
        :return: the unique serial id of this device
        """
        return self._device_id

    def _determine_system_property(self, property: str) -> str:
        """
        :param property: property to fetch
        :return: requested property or "UNKNOWN" if not present on device
        """
        prop = self.get_system_property(property)
        if not prop:
            log.error("Unable to get brand of device from system properties. Setting to \"UNKNOWN\".")
            prop = "UNKNOWN"
        return prop

    @property
    def brand(self) -> str:
        """
        :return: the brand of the device as provided in its system properties, or "UNKNOWN" if indeterminable
        """
        if not self._brand:
            self._brand = self._determine_system_property("ro.product.brand")
        return self._brand

    @property
    def model(self) -> str:
        """
        :return: the model of this device, or "UNKNOWN" if indeterminable
        """
        if not self._model:
            self._model = self._determine_system_property("ro.product.model")
        return self._model

    @property
    def manufacturer(self) -> str:
        """
        :return: the manufacturer of this device, or "UNKNOWN" if indeterminable
        """
        if not self._manufacturer:
            self._manufacturer = self._determine_system_property("ro.product.manufacturer")
        return self._manufacturer

    @property
    def device_name(self) -> str:
        """
        :return: a name for this device based on model and manufacturer
        """
        if self._name is None:
            self._name = self.manufacturer + " " + self.model
        return self._name

    def execute_remote_cmd(self, *args: str,
                           timeout: Optional[float] = None,
                           capture_stdout: bool = True,
                           stdout_redirect: Union[None, int, IO[AnyStr]] = subprocess.DEVNULL,
                           fail_on_presence_of_stderr: bool = False,
                           fail_on_error_code: Callable[[int], bool] = lambda x: x != 0) -> str:
        # TODO: remove capture_stdout argument
        # TODO: return type is really Optional[str], dependent on capture_stdout or stdout_redirect arg...
        """
        Execute a command on this device (via adb)

        :param args: args to be executed (via adb command)
        :param timeout: raise asyncio.TimeoutError if command fails to execute in specified time (in seconds)
        :param capture_stdout: whether to capture and return stdout output (otherwise return None)
        :param stdout_redirect: where to redirect stdout, defaults to subprocess.DEVNULL
        :param fail_on_presence_of_stderr: Some commands return code 0 and still fail, so must check stderr
            (NOTE however that some commands like monkey return 0 and use stderr as though it was stdout :-( )
        :param fail_on_error_code: optional function that takes an error code and returns true if it represents an
            error, False otherwise

        :return: None if no stdout output requested, otherwise a string containing the stdout output of the command

        :raises CommandExecutionFailureException: if command fails to execute on remote device
        """
        timeout = timeout or Device.TIMEOUT_ADB_CMD
        log.debug(f"Executing remote command: {self.formulate_adb_cmd(*args)}")
        completed = subprocess.run(self.formulate_adb_cmd(*args), timeout=timeout,
                                   stderr=subprocess.PIPE,
                                   stdout=subprocess.PIPE if capture_stdout and stdout_redirect == subprocess.DEVNULL else stdout_redirect,
                                   encoding='utf-8', errors='ignore')
        if fail_on_error_code(completed.returncode) or (fail_on_presence_of_stderr and completed.stderr):
            raise self.CommandExecutionFailureException(completed.returncode,
                                                        f"Failed to execute '{' '.join(args)}' on device {self.device_id} [{completed.stderr}]")

        return completed.stdout  # type: ignore

    def execute_remote_cmd_background(self, *args: str, stdout: Union[None, int, IO[AnyStr]] = subprocess.PIPE,
                                      **kwargs: Any) -> subprocess.Popen:
        """
        Run the given command args in the background.

        :param args: command + list of args to be executed
        :param stdout: an optional file-like objection to which stdout is to be redirected (piped).
            defaults to subprocess.PIPE. If None, stdout is redirected to /dev/null
        :param kargs: dict arguments passed to subprocess.Popen

        :return: subprocess.Open
        """
        args = (self._adb_path, "-s", self.device_id, *args)
        log.debug(f"Executing: {' '.join(args)} in background")
        if 'encoding' not in kwargs:
            kwargs['encoding'] = 'utf-8'
            kwargs['errors'] = 'ignore'
        return subprocess.Popen(args,
                                stdout=stdout or subprocess.DEVNULL,
                                stderr=subprocess.PIPE,
                                **kwargs)

    async def execute_remote_cmd_async(self, *args: str, unresponsive_timeout: Optional[float] = None,
                                       proc_completion_timeout: Optional[float] = 0.0,
                                       loop: Optional[AbstractEventLoop] = None
                                       ) -> AsyncContextManager[AsyncIterator[str]]:
        """
        Coroutine for executing a command on this remote device asynchronously, allowing the client to iterate over
        lines of output.

        :param args: command to execute
        :param unresponsive_timeout: if non None and a read of next line of stdout takes longer than specified,
            TimeoutException will be raised
        :param proc_completion_timeout: Once client stops iteration or generator completes, wait
            proc_completion_timeout seconds for process to complete; otherwise kill the process. A value of 0 will kill
            the process immediately after client breaks out of iteration
        :param loop: event loop to asynchronously run under, or None for default event loop

        :return: AsyncGenerator iterating over lines of output from command

        :raises Device.CommandExecutionFailure: if process is allowed to complete normally (e.g.
           proc_completion_timeout is set and non-zero) and return code is non-zero

        >>> async with await device.execute_remote_cmd_async("some_cmd", "with", "args", unresponsive_timeout=10) as stdout:
        ...     async for line in stdout:
        ...         process(line)

        """
        # This is a lower level routine that clients of mdl-integration should not directly call. Other classes will
        # provide a cleaner and more direct API (e.g. TestApplication.run and DeviceLog.logcat will call this function
        # to do the heavy lifting, but they provide a clean external-facing interface to perform those functions).
        cmd = self.formulate_adb_cmd(*args)
        print(f"Executing: {' '.join(cmd)}")
        proc = await asyncio.subprocess.create_subprocess_exec(*cmd,  # type: ignore
                                                               stdout=asyncio.subprocess.PIPE,
                                                               stderr=asyncio.subprocess.PIPE,
                                                               loop=loop,  # noqa
                                                               bufsize=0)  # noqa

        class LineGenerator:
            """
            Wraps below async generator in context manager to ensure proper closure
            """
            async def __aenter__(self) -> AsyncIterator[str]:
                return self.lines()

            async def __aexit__(self, exc_type: Optional[Type[BaseException]], exc_val: Optional[BaseException],
                                exc_tb: Optional[TracebackType]) -> None:
                if proc_completion_timeout is None or proc_completion_timeout > 0.0:
                    return_code = await asyncio.wait_for(proc.wait(), proc_completion_timeout,
                                                         loop=loop)  # loop arg type is optional. bug in typeshed.
                    # mypy: end ignore
                    if return_code != 0:
                        assert proc.stderr, "Expected proc to have stderr pipe"
                        stderr_bytes = await proc.stderr.read()
                        stderr = stderr_bytes.decode('utf-8', errors='ignore')
                        error_msg = f"Remote command '{' '.join(args)}' exited with code {return_code} [{stderr}]"
                        raise Device.CommandExecutionFailureException(return_code, error_msg)
                # todo: if timeout == 0.0, we should kill the proc immediately as the doc says

            async def lines(self) -> AsyncIterator[str]:
                assert proc.stdout, "Expected proc to have stdout pipe"
                if unresponsive_timeout is not None:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=unresponsive_timeout)
                else:
                    line = await proc.stdout.readline()
                while line:
                    yield line.decode('utf-8')
                    if unresponsive_timeout is not None:
                        line = await asyncio.wait_for(proc.stdout.readline(), timeout=unresponsive_timeout)
                    else:
                        line = await proc.stdout.readline()

            async def stop(self, force: bool = False) -> None:
                if force:
                    proc.kill()
                else:
                    proc.terminate()
                await proc.wait()

        return LineGenerator()

    def set_device_setting(self, namespace: str, key: str, value: str) -> Optional[str]:
        """
        Change a setting of the device

        :param namespace: system, etc. -- and android namespace for settings
        :param key: which setting
        :param value: new value for setting

        :return: previous value setting, in case client wishes to restore setting at some point
        """
        if value == '' or value == '""':
            value = '""""'

        previous_value = self.get_device_setting(namespace, key)
        if previous_value is not None or key in ["location_providers_allowed"]:
            try:
                self.execute_remote_cmd("shell", "settings", "put", namespace, key, value, capture_stdout=False)
            except Exception as e:
                log.error(f"Failed to set device setting {namespace}:{key}. Ignoring error [{str(e)}]")
        else:
            log.warning(f"Unable to detect device setting {namespace}:{key}")
        return previous_value

    def get_device_setting(self, namespace: str, key: str) -> Optional[str]:
        """
        Get a device setting

        :param namespace: android setting namespace
        :param key: which setting to get

        :return: value of the requested setting as string, or None if setting could not be found
        """
        try:
            output = self.execute_remote_cmd("shell", "settings", "get", namespace, key)
            if output.startswith("Invalid namespace"):  # some devices output a message with no error return code
                return None
            return output.rstrip()
        except Exception as e:
            log.error(f"Could not get setting for {namespace}:{key} [{str(e)}]")
            return None

    def set_system_property(self, key: str, value: str) -> Optional[str]:
        """
        Set a system property on this device

        :param key: system property key to be set
        :param value: value to set to

        :return: previous value, in case client wishes to restore at some point
        """
        previous_value = self.get_system_property(key)
        self.execute_remote_cmd("shell", "setprop", key, value, capture_stdout=False)
        return previous_value

    def get_system_property(self, key: str) -> Optional[str]:
        """
        :param key: the key of the property to be retrieved

        :return: the property from the device associated with the given key, or None if no such property exists
        """
        try:
            output = self.execute_remote_cmd("shell", "getprop", key)
            return output.rstrip()
        except Exception as e:
            log.error(f"Unable to get system property {key} [{str(e)}]")
            return None

    def get_device_properties(self) -> Dict[str, str]:
        """
        :return: full dict of properties
        """
        results: Dict[str, str] = {}
        output = self.execute_remote_cmd("shell", "getprop", timeout=Device.TIMEOUT_ADB_CMD,)
        for line in output.splitlines():
            if ':' in line:
                property_name, property_value = line.split(':', 1)
                results[property_name.strip()[1:-1]] = property_value.strip()

        return results

    def get_device_datetime(self) -> datetime.datetime:
        """
        :return: Best estimate of device's current datetime. If device's original datetime could not be computed during
            init phase, the server's datetime is returned.
        """
        current_device_time = datetime.datetime.utcnow() - self.device_server_datetime_offset
        return current_device_time

    def get_locale(self) -> str:
        """
        :return: device's current locale setting
        """
        # try old way:
        lang = self.get_system_property('persist.sys.language') or ""
        lang = lang.strip()
        country = self.get_system_property('persist.sys.country') or ""
        country = country.strip()

        device_locale: Optional[str]

        if lang and country:
            device_locale = '_'.join([lang.strip(), country.strip()])
        else:
            device_locale = self.get_system_property('persist.sys.locale')
            if not device_locale:
                device_locale = self.get_system_property("ro.product.locale")
            assert device_locale, "ro.product.locale returned None?!"
            device_locale = device_locale.replace('-', '_').strip()

        return device_locale

    # NOTE: This function currently does not work. We need to store the AdbChangeLanguage app and install it on
    # the device before using. Otherwise, this function will fail.
    # def set_locale(self, locale: str) -> None:
    #     """
    #     Set device's locale to new locale
    #
    #     :param locale: locale to set on device
    #     """
    #     locale_pkg_name = "net.sanapeli.adbchangelanguage"
    #     with suppress(self.CommandExecutionFailureException):
    #         self.execute_remote_cmd("shell", "am", "stop", locale_pkg_name)
    #     # invoke intent on test butler service to change system locale:
    #     self.execute_remote_cmd("shell", "am", "start", "-n", "%s/.AdbChangeLanguage" % locale_pkg_name,
    #                             "-e", "language", locale.replace("_", "-r"))
    #     if self.get_locale() != locale:
    #         # allow time for device and app to catch up
    #         time.sleep(self.SLEEP_SET_PROPERTY)
    #     if self.get_locale() != locale:
    #         raise Exception("System error: failed to set locale to %s" % locale)

    def list(self, kind: str) -> List[str]:
        """
        List available items of a given kind on the device
        :param kind: instrumentation or package

        :return: list of available items of given kind on the device
        """
        output = self.execute_remote_cmd("shell", "pm", "list", kind)
        return output.splitlines()

    def list_installed_packages(self) -> List[str]:
        """
        :return: list of all packages installed on device
        """
        items = []
        for item in self.list("package"):
            if "package" in item:
                items.append(item.replace("package:", '').strip())
        return items

    def list_instrumentation(self) -> List[str]:
        """
        :return: list of instrumentations for a (test) app
        """
        return self.list("instrumentation")

    @property
    def external_storage_location(self) -> str:
        """
        :return: location on remote device of external storage
        """
        if not self._ext_storage:
            output = self.execute_remote_cmd("shell", "echo", "$EXTERNAL_STORAGE")
            for msg in output.splitlines():
                if msg:
                    self._ext_storage = msg.strip()
        return self._ext_storage or "/sdcard"

    def take_screenshot(self, local_screenshot_path: str) -> None:
        """
        :param local_screenshot_path: path to store screenshot

        :return: full path to file name pulled to server, or None if failed
        """
        with open(local_screenshot_path, 'w+b') as f:
            self.execute_remote_cmd("shell", "screencap", "-p", capture_stdout=False,
                                    stdout_redirect=f.fileno(),
                                    timeout=Device.TIMEOUT_SCREEN_CAPTURE)

    # PyCharm detects erroneously that parens below are not required when they are
    # noinspection PyRedundantParentheses
    def formulate_adb_cmd(self, *args: str) -> Tuple[str, ...]:
        """
        :param args: args to the adb command

        :return: the adb command that executes the given arguments on the remote device from this host
        """
        if self.device_id:
            return (self._adb_path, "-s", self.device_id, *args)
        else:
            return (self._adb_path, *args)

    def input(self, subject: str, source: Optional[str] = None) -> None:
        """
        Send event subject through given source

        :param subject: event to send
        :param source: source of event, or None to default to "keyevent"
        """
        self.execute_remote_cmd("shell", "input", source or "keyevent", subject, capture_stdout=False)

    def check_network_connection(self, domain: str, count: int = 3) -> int:
        """
        Check network connection to domain

        :param domain: domain to ping
        :param count: how many times to ping domain

        :return: 0 on success, number of failed packets otherwise
        """
        try:
            output = self.execute_remote_cmd("shell", "ping", "-c", str(count), domain, timeout=Device.TIMEOUT_LONG_ADB_CMD)
            for msg in output.splitlines():
                if "64 bytes" in str(msg):
                    count -= 1
                if count <= 0:
                    break
            return count
        except subprocess.TimeoutExpired:
            log.error("ping is hanging and not yielding any results. Returning error code.")
            return -1
        except self.CommandExecutionFailureException:
            return -1

    def get_version(self, package: str) -> Optional[str]:
        """
        Get version of given package

        :param package: package of inquiry

        :return: version of given package or None if no such package
        """
        version = None
        try:
            output = self.execute_remote_cmd("shell", "dumpsys", "package", package)
            for line in output.splitlines():
                if line and "versionName" in line and '=' in line:
                    version = line.split('=')[1].strip()
                    break
        except Exception as e:
            log.error(f"Unable to get version for package {package} [{str(e)}]")
        return version

    def _verify_install(self, appl_path: str, package: str) -> None:
        """
        Verify installation of an app, taking a screenshot on failure

        :param appl_path: For logging which apk failed to install (upon any failure)
        :param package: package name of app

        :raises Exception: if failure to verify
        """
        packages = self.list_installed_packages()
        if package not in packages:
            # some devices (may??) need time for package install to be detected by system
            time.sleep(self.SLEEP_PKG_INSTALL)
            packages = self.list_installed_packages()
        if package not in packages:
            if package is not None:
                screenshot_dir = "test-screenshots"
                if not os.path.exists(screenshot_dir):
                    os.makedirs(screenshot_dir)
                self.take_screenshot("install_failure-%s.png" % package)
                log.error("Did not find installed package %s;  found: %s" % (package, packages))
                log.error("Device failure to install %s on model %s;  install status succeeds,"
                          "but package not found on device" %
                          (appl_path, self.model))
            raise Exception("Failed to verify installation of app '%s', event though output indicated otherwise" %
                            package)
        else:
            log.info("Package %s installed" % str(package))

    def install_synchronous(self, apk_path: str, as_upgrade: bool) -> None:
        """
        install the given bundle, blocking until complete
        :param apk_path: local path to the apk to be installed
        :param as_upgrade: install as upgrade or not
        """
        if as_upgrade:
            cmd: Tuple[str, ...] = ("install", "-r", apk_path)
        else:
            cmd = ("install", apk_path)

        self.execute_remote_cmd(*cmd, timeout=Device.TIMEOUT_LONG_ADB_CMD)

    async def install(self, apk_path: str, as_upgrade: bool,
                      conditions: Optional[List[str]] = None,
                      on_full_install: Optional[Callable[[], None]] = None) -> None:
        """
        Install given apk asynchronously, monitoring output for messages containing any of the given conditions,
        executing a callback if given when any such condition is met.

        :param apk_path: bundle to install
        :param as_upgrade: whether as upgrade or not
        :param conditions: list of strings to look for in stdout as a trigger for callback. Some devices are
            non-standard and will provide a pop-up request explicit user permission for install once the apk is fully
            uploaded and prepared. This param defaults to ["100%", "pkg:", "Success"] as indication that bundle was
            fully prepared (pre-pop-up).
        :param on_full_install: if not None the callback to be called

        :raises Device.InsufficientStorageError: if there is not enough space on device
        """
        conditions = conditions or ["100%", "pkg:", "Success"]
        parser = AXMLParser.parse(apk_path)
        package = parser.package_name
        if not as_upgrade:
            # avoid unnecessary conflicts with older certs and crap:
            # TODO: client should handle clean install -- this code really shouldn't be here??
            with suppress(Exception):
                self.execute_remote_cmd("uninstall", package, capture_stdout=False)

        # Execute the installation of the app, monitoring output for completion in order to invoke any extra commands
        if as_upgrade:
            cmd: Tuple[str, ...] = ("install", "-r", apk_path)
        else:
            cmd = ("install", apk_path)
        # Do not allow more than one install at a time on a specific device, as this can be problematic
        async with self.lock():
            async with await self.execute_remote_cmd_async(*cmd, proc_completion_timeout=Device.TIMEOUT_LONG_ADB_CMD) as lines:
                async for msg in lines:
                    log.debug(msg)

                    if self.ERROR_MSG_INSUFFICIENT_STORAGE in msg:
                        raise self.InsufficientStorageError("Insufficient storage for install of %s" %
                                                            apk_path)
                    # Some devices have non-standard pop-ups that must be cleared by accepting usb installs
                    # (non-standard Android):
                    if on_full_install and msg and any([condition in msg for condition in conditions]):
                        on_full_install()

        # On some devices, a pop-up may prevent successful install even if return code from adb install showed success,
        # so must explicitly verify the install was successful:
        log.debug("Verifying install...")
        self._verify_install(apk_path, package)  # raises exception on failure to verify

    @asynccontextmanager
    async def lock(self) -> AsyncIterator["Device"]:
        await self._lock.acquire()
        yield self
        self._lock.release()

    # todo: why this is a property instead of a function?
    @property
    def home_screen_active(self) -> bool:
        """
        :return: True if the home screen is currently in the foreground. Note that system pop-ups will result in this
        function returning False.

        :raises Exception: if unable to make determination
        """
        found_potential_stack_match = False
        stdout = self.execute_remote_cmd("shell", "dumpsys", "activity", "activities", capture_stdout=True,
                                         timeout=Device.TIMEOUT_ADB_CMD)
        # Find lines that look like this:
        #   Stack #0:
        # or
        #   Stack #0: type=home mode=fullscreen
        app_stack_pattern = re.compile(r'^Stack #(\d*):')
        stdout_lines = stdout.splitlines()
        for line in stdout_lines:
            matches = app_stack_pattern.match(line.strip())
            if matches:
                if matches.group(1) == "0":
                    return True
                else:
                    found_potential_stack_match = True
                    break

        # Went through entire activities stack, but no line matched expected format for displaying activity
        if not found_potential_stack_match:
            raise Exception(
                f"Could not determine if home screen is in foreground because no lines matched expected "
                f"format of \"dumpsys activity activities\" pattern. Please check that the format did not change:\n"
                f"{stdout_lines}")

        # Format of activities was fine, but detected home screen was not in foreground. But it is possible this is a
        # Samsung device with silent packages in foreground. Need to check if that's the case, and app after them
        # is the launcher/home screen.
        foreground_activity = self.foreground_activity(ignore_silent_apps=True)
        return bool(foreground_activity and foreground_activity.lower() == "com.sec.android.app.launcher")

    def return_home(self, keycode_back_limit: int = 10) -> None:
        """
        Return to home screen as though the user did so via one or many taps on the back button.

        In this scenario, subsequent launches of the app will need to recreate the app view, but may
        be able to take advantage of some saved state, and is considered a warm app launch.

        NOTE: This function assumes the app is currently in the foreground. If not, it may still return to the home
        screen, but the process of closing activities on the back stack will not occur.

        :param keycode_back_limit: The maximum number of times to press the back button to attempt to get back to
           the home screen
        """
        back_button_attempt = 0

        while back_button_attempt <= keycode_back_limit:
            back_button_attempt += 1
            self.input("KEYCODE_BACK")
            if self.home_screen_active:
                return
            # Sleep for a second to allow for complete activity destruction.
            # TODO: ouch!! almost a 10 second overhead if we reach limit
            time.sleep(1)

        foreground_activity = self.foreground_activity(ignore_silent_apps=True)

        raise Exception(f"Max number of back button presses ({keycode_back_limit}) to get to Home screen has "
                        f"been reached. Found foreground activity {foreground_activity}. App closure failed.")

    def go_home(self) -> None:
        """
        Equivalent to hitting home button to go to home screen
        """
        self.input("KEYCODE_HOME")

    def _activity_stack_top(self, filter: Callable[[str], bool] = lambda x: True) -> Optional[str]:
        """
        :return: List of the app packages in the activities stack, with the first item being at the top of the stack
        """
        stdout = self.execute_remote_cmd("shell", "dumpsys", "activity", "activities", capture_stdout=True)
        # Find lines that look like this:
        #   * TaskRecord{133fbae #1340 I=com.google.android.apps.nexuslauncher/.NexusLauncherActivity U=0 StackId=0 sz=1}
        # or
        #   * TaskRecord{94c8098 #1791 A=com.android.chrome U=0 StackId=454 sz=1}
        app_record_pattern = re.compile(r'^\* TaskRecord\{[a-f0-9-]* #\d* [AI]=([a-zA-Z].[a-zA-Z0-9.]*)[ /].*')
        for line in stdout.splitlines():
            matches = app_record_pattern.match(line.strip())
            app_package = matches.group(1) if matches else None
            if app_package and filter(app_package):
                return app_package
        return None  # to be explicit

    def foreground_activity(self, ignore_silent_apps: bool = True) -> Optional[str]:
        """
        :param ignore_silent_apps: whether or not to ignore silent-running apps (ignoring those if they are in the
            stack. They show up as the foreground activity, even if the normal activity we care about is behind it and
            running as expected).

        :return: package name of current foreground activity
        """
        ignored = self.SILENT_RUNNING_PACKAGES if ignore_silent_apps else []
        return self._activity_stack_top(filter=lambda x: x.lower() not in ignored)


class RemoteDeviceBased(object):
    """
    Classes that are based on the context of a remote device
    """

    def __init__(self, device: Device) -> None:
        """
        :param device: which device is associated with this instance
        """
        self._device = device

    @property
    def device(self) -> Device:
        """
        :return: the device associated with this instance
        """
        return self._device
