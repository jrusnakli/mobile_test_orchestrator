import asyncio
import datetime
import logging
import os
import re
import subprocess
import time

from contextlib import suppress, asynccontextmanager
from types import TracebackType
from typing import (
    Any,
    AnyStr,
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    IO,
    List,
    Optional,
    Union,
    Tuple,
    Type,
)

log = logging.getLogger("MTO")
log.setLevel(logging.ERROR)


class DeviceLock:

    _locks: Dict[str, asyncio.Semaphore] = {}


@asynccontextmanager
async def _device_lock(device: "Device") -> AsyncIterator["Device"]:
    """
    lock this device while a command is being executed against it

    Is static to avoid possible pickling issues in parallelized execution
    :param device: device to lock
    :return: device
    """
    DeviceLock._locks.setdefault(device._device_id, asyncio.Semaphore())
    await DeviceLock._locks[device._device_id].acquire()
    yield device
    DeviceLock._locks[device._device_id].release()


class Device:
    """
    Class for interacting with a device via Google's adb command. This is intended to be a direct bridge to the same
    functionality as adb, with minimized embellishments

    :param adb_path: path to the adb command on the host
    :param device_id: serial id of the device as seen by host (e.g. via 'adb devices')
    :raises FileNotFoundError: if adb path is invalid
    """

    # These packages may appear as running when looking at the activities in a device's activity stack. The running
    # of these packages do not affect interaction with the app under test. With the exception of the Samsung
    # MtpApplication (pop-up we can't get rid of that asks the user to update their device), they are also not visible
    # to the user. We keep a list of them so we know which ones to disregard when trying to retrieve the actual
    # foreground application the user is interacting with.
    SILENT_RUNNING_PACKAGES = ["com.samsung.android.mtpapplication", "com.wssyncmldm", "com.bitbar.testdroid.monitor"]
    SCREEN_UNLOCK_BLACKLIST = {"MI 4LTE"}

    class InsufficientStorageError(Exception):
        """
        Raised on insufficient storage on device (e.g. in install)
        """

    class AsyncProcessContext:
        """
        Wraps the Device.Process class in a context manager to ensure proper cleanup.
        Upon exit of this context, the process will be stopped if it is still running.
        The client is responsible for calling wait() on the entered Device.Process if
        it is desired to cleanly exit the process before exiting.

        :param proc_future: future (coroutine) to underlying asyncio Subprocess

        >>> async with ProcessContext(some_proc) as proc:
        ...    async for line in proc.output():
        ...        yield line
        ...        proc.wait(timeout=10)
        """

        def __init__(self, proc_future: Awaitable[asyncio.subprocess.Process]):
            # we pass in a future mostly to avoid having to do something quirky like
            # async with await Process(...)
            self._proc_future = proc_future

        async def __aenter__(self) -> "Device.Process":
            self._proc = Device.Process(await self._proc_future)
            return self._proc

        async def __aexit__(self, exc_type: Optional[Type[BaseException]], exc_val: Optional[BaseException],
                            exc_tb: Optional[TracebackType]) -> None:
            if self._proc.returncode and self._proc.returncode != 0 and exc_type is not None:
                raise Device.CommandExecutionFailure(self._proc.returncode, "Remote command failed on device")
            if self._proc.returncode is None:
                log.info("Terminating process %d", self._proc._proc.pid)
                with suppress(Exception):
                    await self._proc.stop(timeout=3)
            if self._proc.returncode is None:
                # Second try, force-stop
                try:
                    await self._proc.stop(timeout=3, force=True)
                except TimeoutError:
                    log.error("Failed to kill subprocess while exiting its context")

    class Process:
        """
        Basic process interface that provides a means of monitoring line-by-line output
        of an `asyncio.subprocess.Process`.
        """

        def __init__(self, proc: asyncio.subprocess.Process):
            self._proc = proc

        @property
        def returncode(self) -> Optional[int]:
            return self._proc.returncode

        async def output(self,  unresponsive_timeout: Optional[float] = None) -> AsyncIterator[str]:
            """
            Async iterator over lines of output from process

            :param unresponsive_timeout: raise TimeoutException if not None and time to receive next line exceeds this
            """
            if self._proc.stdout is None:
                raise Exception("Failed to capture output from subprocess")
            if unresponsive_timeout is not None:
                line = await asyncio.wait_for(self._proc.stdout.readline(), timeout=unresponsive_timeout)
            else:
                line = await self._proc.stdout.readline()
            while line:
                yield line.decode('utf-8')
                if unresponsive_timeout is not None:
                    line = await asyncio.wait_for(self._proc.stdout.readline(), timeout=unresponsive_timeout)
                else:
                    line = await self._proc.stdout.readline()

        async def stop(self, force: bool = False, timeout: Optional[float] = None) -> None:
            """
            Signal process to terminate, and wait for process to end

            :param force: whether to kill (harsh) or simply terminate
            :param timeout: raise TimeoutException if process fails to truly terminate in timeout seconds
            """
            if force:
                self._proc.kill()
            else:
                self._proc.terminate()
            await self.wait(timeout)

        async def wait(self, timeout: Optional[float] = None) -> None:
            """
            Wait for process to end

            :param timeout: raise TimeoutException if waiting beyond this many seconds
            """
            if timeout is None:
                await self._proc.wait()
            else:
                await asyncio.wait_for(self._proc.wait(), timeout=timeout)

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

    DANGEROUS_PERMISSIONS = [
        "android.permission.ACCEPT_HANDOVER",
        "android.permission.ACCESS_BACKGROUND_LOCATION",
        "android.permission.ACCESS_COARSE_LOCATION",
        "android.permission.ACCESS_FINE_LOCATION",
        "android.permission.ACCESS_MEDIA_LOCATION",
        "android.permission.ACTIVITY_RECOGNITION",
        "android.permission.ADD_VOICEMAIL",
        "android.permission.ANSWER_PHONE_CALLS",
        "android.permission.BODY_SENSORS",
        "android.permission.CALL_PHONE",
        "android.permission.CALL_PRIVILEGED",
        "android.permission.CAMERA",
        "android.permission.GET_ACCOUNTS",
        "android.permission.PROCESS_OUTGOING_CALLS",
        "android.permission.READ_CALENDAR",
        "android.permission.READ_CALL_LOG",
        "android.permission.READ_CONTACTS",
        "android.permission.READ_EXTERNAL_STORAGE",
        "android.permission.READ_PHONE_NUMBERS",
        "android.permission.READ_PHONE_STATE",
        "android.permission.READ_SMS",
        "android.permission.READ_MMS",
        "android.permission.RECEIVE_SMS",
        "android.permission.RECEIVE_WAP_PUSH",
        "android.permission.RECORD_AUDIO",
        "android.permission.SEND_SMS",
        "android.permission.USE_SIP",
        "android.permission.WRITE_CALENDAR",
        "android.permission.WRITE_CALL_LOG",
        "android.permission.WRITE_CONTACTS",
        "android.permission.WRITE_EXTERNAL_STORAGE",
    ]

    WRITE_EXTERNAL_STORAGE_PERMISSION = "android.permission.WRITE_EXTERNAL_STORAGE"
    UNKNOWN_API_LEVEL = -1

    # Find lines that look like this:
    #  * TaskRecord{133fbae #1340 I=com.google.android.apps.nexuslauncher/.NexusLauncherActivity U=0 StackId=0 sz=1}
    # or
    #  * TaskRecord{94c8098 #1791 A=com.android.chrome U=0 StackId=454 sz=1}
    APP_RECORD_PATTERN = re.compile(r'^\* TaskRecord\{[a-f0-9-]* #\d* [AI]=([a-zA-Z].[a-zA-Z0-9.]*)[ /].*')

    class CommandExecutionFailure(Exception):

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
        self._api_level: Optional[int] = None

    def _activity_stack_top(self, filt: Callable[[str], bool] = lambda x: True) -> Optional[str]:
        """
        :return: List of the app packages in the activities stack, with the first item being at the top of the stack
        """
        completed = self.execute_remote_cmd("shell", "dumpsys", "activity", "activities",
                                            stdout=subprocess.PIPE)
        for line in completed.stdout.splitlines():
            matches = self.APP_RECORD_PATTERN.match(line.strip())
            app_package = matches.group(1) if matches else None
            if app_package and filt(app_package):
                return app_package
        return None  # to be explicit

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

    def _verify_install(self, appl_path: str, package: str, verify_screenshot_dir: Optional[str] = None) -> None:
        """
        Verify installation of an app, taking a screenshot on failure

        :param appl_path: For logging which apk failed to install (upon any failure)
        :param package: package name of app
        :param verify_screenshot_dir: if not None, where to capture screenshot on failure

        :raises Exception: if failure to verify
        """
        packages = self.list_installed_packages()
        if package not in packages:
            # some devices (may??) need time for package install to be detected by system
            time.sleep(self.SLEEP_PKG_INSTALL)
            packages = self.list_installed_packages()
        if package not in packages:
            try:
                if verify_screenshot_dir:
                    os.makedirs(verify_screenshot_dir, exist_ok=True)
                    self.take_screenshot(os.path.join(verify_screenshot_dir, f"install_failure-{package}.png"))
            except Exception as e:
                log.warning(f"Unable to take screenshot of installation failure: {e}")
            log.error("Did not find installed package %s;  found: %s" % (package, packages))
            log.error("Device failure to install %s on model %s;  install status succeeds,"
                      "but package not found on device" %
                      (appl_path, self.model))
            raise Exception("Failed to verify installation of app '%s', event though output indicated otherwise" %
                            package)
        log.info("Package %s installed", str(package))

    #################
    # Properties
    #################

    @property
    def api_level(self) -> Optional[int]:
        """
        :return: api level of device, or None if not discoverable
        """
        if self._api_level:
            return self._api_level if self._api_level != Device.UNKNOWN_API_LEVEL else None

        device_api_level = self.get_system_property("ro.build.version.sdk")
        self._api_level = int(device_api_level) if device_api_level else Device.UNKNOWN_API_LEVEL
        return self._api_level

    @property
    def brand(self) -> str:
        """
        :return: the brand of the device as provided in its system properties, or "UNKNOWN" if indeterminable
        """
        if not self._brand:
            self._brand = self._determine_system_property("ro.product.brand")
        return self._brand

    @property
    def device_server_datetime_offset(self) -> datetime.timedelta:
        """
        :return: Returns a datetime.timedelta object that represents the difference between the server/host datetime
            and the datetime of the Android device
        """
        if self._device_server_datetime_offset is not None:
            return self._device_server_datetime_offset

        self._device_server_datetime_offset = datetime.timedelta()
        is_valid = False
        # There is a variable on Android devices that holds the current epoch time of the device. We use that to
        # retrieve the device's datetime so we can easily calculate the difference of the start time from
        # other times during the test.
        with suppress(Exception):
            completed = self.execute_remote_cmd("shell", "echo", "$EPOCHREALTIME", stdout=subprocess.PIPE)
            for msg_ in completed.stdout.splitlines():
                if re.search(r"^\d+\.\d+$", msg_):
                    device_datetime = datetime.datetime.fromtimestamp(float(msg_.strip()))
                    self._device_server_datetime_offset = datetime.datetime.now() - device_datetime
                    is_valid = True
                    break
        if not is_valid:
            log.error("Unable to get datetime from device. No offset will be computed for timestamps")
        return self._device_server_datetime_offset

    @property
    def device_id(self) -> str:
        """
        :return: the unique serial id of this device
        """
        return self._device_id

    @property
    def device_name(self) -> str:
        """
        :return: a name for this device based on model and manufacturer
        """
        if self._name is None:
            self._name = self.manufacturer + " " + self.model
        return self._name

    @property
    def external_storage_location(self) -> str:
        """
        :return: location on remote device of external storage
        """
        if not self._ext_storage:
            completed = self.execute_remote_cmd("shell", "echo", "$EXTERNAL_STORAGE", stdout=subprocess.PIPE)
            for msg in completed.stdout.splitlines():
                if msg:
                    self._ext_storage = msg.strip()
        return self._ext_storage or "/sdcard"

    @property
    def manufacturer(self) -> str:
        """
        :return: the manufacturer of this device, or "UNKNOWN" if indeterminable
        """
        if not self._manufacturer:
            self._manufacturer = self._determine_system_property("ro.product.manufacturer")
        return self._manufacturer

    @property
    def model(self) -> str:
        """
        :return: the model of this device, or "UNKNOWN" if indeterminable
        """
        if not self._model:
            self._model = self._determine_system_property("ro.product.model")
        return self._model

    ###############
    # RAW COMMAND EXECUTION ON DEVICE
    ###############

    # PyCharm detects erroneously that parens below are not required when they are
    # noinspection PyRedundantParentheses
    def _formulate_adb_cmd(self, *args: str) -> Tuple[str, ...]:
        """
        :param args: args to the adb command
        :return: the adb command that executes the given arguments on the remote device from this host
        """
        if self.device_id:
            return (self._adb_path, "-s", self.device_id, *args)
        else:
            return (self._adb_path, *args)

    def execute_remote_cmd(self, *args: str,
                           timeout: Optional[float] = None,
                           stdout: Union[None, int, IO[AnyStr]] = None,
                           stderr: Union[None, int, IO[AnyStr]] = subprocess.PIPE,
                           fail_on_error_code: Callable[[int], bool] = lambda x: x != 0) \
            -> subprocess.CompletedProcess:
        """
        Execute a command on this device (via adb)

        :param args: args to be executed (via adb command)
        :param timeout: raise asyncio.TimeoutError if command fails to execute in specified time (in seconds)
        :param stdout: how to pipe stdout, per subprocess.Popen like argument
        :param stderr: how to pipe stderr, per subprocess.Popen like argument
        :param fail_on_error_code: optional function that takes an error code that returns True if it represents an
            error, False otherwise;  if None, any non-zero error code is treated as a failure
        :return:tuple of stdout, stderr output as requested (None for an output that is not directed as subprocess.PIPE)
        :raises CommandExecutionFailureException: if command fails to execute on remote device
        """
        # protected methjod: OK to access by subclasses
        timeout = timeout or Device.TIMEOUT_ADB_CMD
        log.debug(f"Executing remote command: {self._formulate_adb_cmd(*args)} with timeout {timeout}")
        completed = subprocess.run(self._formulate_adb_cmd(*args),
                                   timeout=timeout,
                                   stderr=stderr or subprocess.DEVNULL,
                                   stdout=stdout or subprocess.DEVNULL,
                                   encoding='utf-8', errors='ignore')
        if fail_on_error_code(completed.returncode):
            raise self.CommandExecutionFailure(
                completed.returncode,
                f"Failed to execute '{' '.join(args)}' on device {self.device_id}:"
                + f"\n{completed.stdout or ''}\n{completed.stderr or ''}")
        return completed

    def execute_remote_cmd_background(self, *args: str, stdout: Union[None, int, IO[AnyStr]] = subprocess.PIPE,
                                      **kwargs: Any) -> subprocess.Popen:  # noqa
        """
        Run the given command args in the background.

        :param args: command + list of args to be executed
        :param stdout: an optional file-like objection to which stdout is to be redirected (piped).
            defaults to subprocess.PIPE. If None, stdout is redirected to /dev/null
        :param kwargs: dict arguments passed to subprocess.Popen
        :return: subprocess.Open
        """
        # protected methjod: OK to access by subclasses
        args = (self._adb_path, "-s", self.device_id, *args)
        log.debug(f"Executing: {' '.join(args)} in background")
        if 'encoding' not in kwargs:
            kwargs['encoding'] = 'utf-8'
            kwargs['errors'] = 'ignore'
        return subprocess.Popen(args,
                                stdout=stdout or subprocess.DEVNULL,
                                stderr=subprocess.PIPE,
                                **kwargs)

    def monitor_remote_cmd(self, *args: str) -> "Device.AsyncProcessContext":
        """
        Coroutine for executing a command on this remote device asynchronously, allowing the client to iterate over
        lines of output.

        :param args: command to execute
        :return: AsyncGenerator iterating over lines of output from command

        >>> device = Device("someid")
        ... async with device.monitor_remote_cmd("some_cmd", "with", "args",
        ...                                      unresponsive_timeout=10) as proc:
        ...     async for line in proc.output(unresponsive_timeout=10):
        ...         print(line)

        """
        cmd = self._formulate_adb_cmd(*args)
        log.debug(f"Executing: {' '.join(cmd)}")
        proc_future = asyncio.subprocess.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            bufsize=0
        )
        return self.AsyncProcessContext(proc_future)

    ###################
    # Device settings/properties
    ###################

    def get_device_datetime(self) -> datetime.datetime:
        """
        :return: Best estimate of device's current datetime.
           If device's original datetime could not be computed, the server's datetime is returned.
        """
        current_device_time = datetime.datetime.utcnow() - self.device_server_datetime_offset
        return current_device_time

    def get_device_setting(self, namespace: str, key: str, verbose: bool = True) -> Optional[str]:
        """
        Get a device setting

        :param namespace: android setting namespace
        :param key: which setting to get
        :param verbose: whether to output error logs if unable to find setting or device
        :return: value of the requested setting as string, or None if setting could not be found
        """
        try:
            completed = self.execute_remote_cmd("shell", "settings", "get", namespace, key,
                                                stdout=subprocess.PIPE)
            if completed.stdout.startswith("Invalid namespace"):  # some devices output a message with no error code
                return None
            stdout: str = completed.stdout
            return stdout.rstrip()
        except Exception as e:
            if verbose:
                log.error(f"Could not get setting for {namespace}:{key} [{str(e)}]")
            return None

    def get_device_properties(self) -> Dict[str, str]:
        """
        :return: full dict of properties
        """
        results: Dict[str, str] = {}
        completed = self.execute_remote_cmd("shell", "getprop", timeout=Device.TIMEOUT_ADB_CMD,
                                            stdout=subprocess.PIPE)
        for line in completed.stdout.splitlines():
            if ':' in line:
                property_name, property_value = line.split(':', 1)
                results[property_name.strip()[1:-1]] = property_value.strip()

        return results

    def get_locale(self) -> Optional[str]:
        """
        :return: device's current locale setting, or None if indeterminant
        """
        # try old way:
        lang = self.get_system_property('persist.sys.language') or ""
        lang = lang.strip()
        country = self.get_system_property('persist.sys.country') or ""
        country = country.strip()

        if lang and country:
            device_locale: Optional[str] = '_'.join([lang.strip(), country.strip()])
        else:
            device_locale = self.get_system_property('persist.sys.locale') or \
                self.get_system_property("ro.product.locale") or None
            device_locale = device_locale.replace('-', '_').strip() if device_locale else None
        return device_locale

    def get_state(self) -> str:
        """
        :return: current state of emulaor ("device", "offline", "non-existent", ...)
        """
        try:
            completed = self.execute_remote_cmd("get-state", timeout=10, stdout=subprocess.PIPE)
            stdout: str = completed.stdout
            return stdout.strip()
        except Exception:
            return "non-existent"

    def get_version(self, package: str) -> Optional[str]:
        """
        Get version of given package

        :param package: package of inquiry
        :return: version of given package or None if no such package
        """
        version = None
        try:
            completed = self.execute_remote_cmd("shell", "dumpsys", "package", package, stdout=subprocess.PIPE)
            for line in completed.stdout.splitlines():
                if line and "versionName" in line and '=' in line:
                    version = line.split('=')[1].strip()
                    break
        except Exception as e:
            log.error(f"Unable to get version for package {package} [{str(e)}]")
        return version

    def get_system_property(self, key: str, verbose: bool = True) -> Optional[str]:
        """
        :param key: the key of the property to be retrieved
        :param verbose: whether to print error messages on command execution problems or not

        :return: the property from the device associated with the given key, or None if no such property exists
        """
        try:
            completed = self.execute_remote_cmd("shell", "getprop", key, stdout=subprocess.PIPE)
            stdout: str = completed.stdout
            return stdout.rstrip()
        except Exception as e:
            if verbose:
                log.error(f"Unable to get system property {key} [{str(e)}]")
            return None

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
                self.execute_remote_cmd("shell", "settings", "put", namespace, key, value)
            except Exception as e:
                log.error(f"Failed to set device setting {namespace}:{key}. Ignoring error [{str(e)}]")
        else:
            log.warning(f"Unable to detect device setting {namespace}:{key}")
        return previous_value

    def set_system_property(self, key: str, value: str) -> Optional[str]:
        """
        Set a system property on this device

        :param key: system property key to be set
        :param value: value to set to
        :return: previous value, in case client wishes to restore at some point
        """
        previous_value = self.get_system_property(key)
        self.execute_remote_cmd("shell", "setprop", key, value)
        return previous_value

    ###################
    # Device listings of installed apps/activities
    ###################

    def foreground_activity(self, ignore_silent_apps: bool = True) -> Optional[str]:
        """
        :param ignore_silent_apps: whether or not to ignore silent-running apps (ignoring those if they are in the
            stack. They show up as the foreground activity, even if the normal activity we care about is behind it and
            running as expected).

        :return: package name of current foreground activity
        """
        ignored = self.SILENT_RUNNING_PACKAGES if ignore_silent_apps else []
        return self._activity_stack_top(filt=lambda x: x.lower() not in ignored)

    def get_activity_stack(self) -> List[str]:
        completed = self.execute_remote_cmd("shell", "dumpsys", "activity", "activities",
                                            stdout=subprocess.PIPE, timeout=10)
        activity_list = []
        for line in completed.stdout.splitlines():
            matches = self.APP_RECORD_PATTERN.match(line.strip())
            if matches:
                app_package = matches.group(1)
                activity_list.append(app_package)
        return activity_list

    def list(self, kind: str) -> List[str]:
        """
        List available items of a given kind on the device
        :param kind: instrumentation or package

        :return: list of available items of given kind on the device
        """
        completed = self.execute_remote_cmd("shell", "pm", "list", kind, stdout=subprocess.PIPE)
        stdout: str = completed.stdout
        return stdout.splitlines()

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

    #################
    # Screenshot
    #################

    def take_screenshot(self, local_screenshot_path: str, timeout: Optional[int] = None) -> None:
        """
        :param local_screenshot_path: path to store screenshot
        :param timeout: timeout after this many seconds of trying to take screenshot, or None to use default timeout
        :raises: TimeoutException if screenshot not captured within timeout (or default timeout) seconds
        :raises: FileExistsError if path to capture screenshot already exists (will not overwrite)
        """
        if os.path.exists(local_screenshot_path):
            raise FileExistsError(f"cannot overwrite screenshot path {local_screenshot_path}")
        with open(local_screenshot_path, 'w+b') as f:
            self.execute_remote_cmd("shell", "screencap", "-p",
                                    stdout=f.fileno(),
                                    timeout=timeout or Device.TIMEOUT_SCREEN_CAPTURE)


class RemoteDeviceBased(object):
    """
    Classes that are based on the context of a remote device

    :param device: which device is associated with this instance
    """

    def __init__(self, device: Device) -> None:
        self._device = device

    @property
    def device(self) -> Device:
        """
        :return: the device associated with this instance
        """
        return self._device


class DeviceInteraction(RemoteDeviceBased):
    """
    Provides API for equvialent of user-navigation along with related device queries

    :param device: which device is associated with this instance
    """

    def go_home(self) -> None:
        """
        Equivalent to hitting home button to go to home screen
        """
        self.input("KEYCODE_HOME")

    def home_screen_active(self) -> bool:
        """
        :return: True if the home screen is currently in the foreground. Note that system pop-ups will result in this
        function returning False.
        :raises Exception: if unable to make determination
        """
        found_potential_stack_match = False
        completed = self._device.execute_remote_cmd("shell", "dumpsys", "activity", "activities",
                                                    timeout=Device.TIMEOUT_ADB_CMD, stdout=subprocess.PIPE)
        # Find lines that look like this:
        #   Stack #0:
        # or
        #   Stack #0: type=home mode=fullscreen
        app_stack_pattern = re.compile(r'^Stack #(\d*):')
        stdout_lines = completed.stdout.splitlines()
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
        foreground_activity = self._device.foreground_activity(ignore_silent_apps=True)
        return bool(foreground_activity and foreground_activity.lower() == "com.sec.android.app.launcher")

    def input(self, subject: str, source: Optional[str] = None) -> None:
        """
        Send event subject through given source

        :param subject: event to send
        :param source: source of event, or None to default to "keyevent"
        """
        self._device.execute_remote_cmd("shell", "input", source or "keyevent", subject, timeout=Device.TIMEOUT_ADB_CMD)

    def is_screen_on(self) -> bool:
        """
        :return: whether device's screen is on
        """
        completed = self._device.execute_remote_cmd("shell", "dumpsys", "activity", "activities",
                                                    stdout=subprocess.PIPE,
                                                    timeout=Device.TIMEOUT_ADB_CMD)
        lines = completed.stdout.splitlines()
        for msg in lines:
            if 'mInteractive=false' in msg or 'mScreenOn=false' in msg or 'isSleeping=true' in msg:
                return False
        return True

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

        foreground_activity = self._device.foreground_activity(ignore_silent_apps=True)

        raise Exception(f"Max number of back button presses ({keycode_back_limit}) to get to Home screen has "
                        f"been reached. Found foreground activity {foreground_activity}. App closure failed.")

    def toggle_screen_on(self) -> None:
        """
        Toggle device's screen on/off
        """
        self._device.execute_remote_cmd("shell", "input", "keyevent", "KEYCODE_POWER", timeout=10)


class DeviceConnectivity(RemoteDeviceBased):
    """
    API for network communications configuration/queries, including host-to-device communications

    :param device: which device
    """

    def check_network_connection(self, domain: str, count: int = 3) -> int:
        """
        Check network connection to domain

        :param domain: domain to ping
        :param count: how many times to ping domain
        :return: 0 on success, number of failed packets otherwise
        """
        try:
            completed = self._device.execute_remote_cmd("shell", "ping", "-c", str(count), domain,
                                                        timeout=Device.TIMEOUT_LONG_ADB_CMD,
                                                        stdout=subprocess.PIPE)
            for msg in completed.stdout.splitlines():
                if "64 bytes" in str(msg):
                    count -= 1
                if count <= 0:
                    break
            if count > 0:
                log.error("Output from ping was: \n%s", completed.stdout)
            return count
        except subprocess.TimeoutExpired:
            log.error("ping is hanging and not yielding any results. Returning error code.")
            return -1
        except Device.CommandExecutionFailure:
            return -1

    def port_forward(self, local_port: int, device_port: int) -> None:
        """
        forward traffic from local port to remote device port

        :param local_port: port to forward from
        :param device_port: port to forward to
        """
        self._device.execute_remote_cmd("forward", f"tcp:{device_port}", f"tcp:{local_port}")

    def remove_port_forward(self, port: Optional[int] = None) -> None:
        """
        Remove reverse port forwarding

        :param port: port to remove or None to remove all reverse forwarded ports
        """
        if port is not None:
            self._device.execute_remote_cmd("forward", "--remove", f"tcp:{port}")
        else:
            self._device.execute_remote_cmd("forward", "--remove-all")

    def remove_reverse_port_forward(self, port: Optional[int] = None) -> None:
        """
        Remove reverse port forwarding

        :param port: port to remove or None to remove all reverse forwarded ports
        """
        if port is not None:
            self._device.execute_remote_cmd("reverse", "--remove", f"tcp:{port}")
        else:
            self._device.execute_remote_cmd("reverse", "--remove-all")

    def reverse_port_forward(self, device_port: int, local_port: int) -> None:
        """
        reverse forward traffic on remote port to local port

        :param device_port: remote device port to forward
        :param local_port: port to forward to
        """
        self._device.execute_remote_cmd("reverse", f"tcp:{device_port}", f"tcp:{local_port}")
