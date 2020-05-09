"""
This package contains the core elements for device interaction: to query aspects of the device,
change or get settings and properties, and core API for raw execution of commands on the device (via adb), etc.
"""

import asyncio
import datetime
import logging
import os
import re
import subprocess
import sys
import time

from asyncio import AbstractEventLoop
from contextlib import suppress, asynccontextmanager
from enum import Enum
from types import TracebackType
from typing import (
    Any,
    AnyStr,
    AsyncContextManager,
    AsyncIterator,
    Callable,
    Dict,
    IO,
    List,
    Optional,
    Union,
    Type,
    Tuple, TypeVar)

log = logging.getLogger("MTO")
log.setLevel(logging.WARNING)


__all__ = ["Device", "DeviceBased"]


class DeviceLock:

    locks: Dict[str, asyncio.Semaphore] = {}


@asynccontextmanager
async def _device_lock(device: "Device") -> AsyncIterator["Device"]:
    """
    lock this device while a command is being executed against it
    This is a separate static function to avoid possible pickling issues in parallelized execution

    :param device: device to lock

    :return: device
    """
    pid = os.getpid()
    key = f"{device.device_id}-{pid}"
    DeviceLock.locks.setdefault(key, asyncio.Semaphore())
    await DeviceLock.locks[key].acquire()
    try:
        yield device
    finally:
        DeviceLock.locks[key].release()


class Device:
    """
    Class for interacting with a device via Google's adb command. This is intended to be a direct bridge to the same
    functionality as adb, with minimized embellishments, but providing a clean Python API.
    """

    class State(Enum):
        """
        An enumeration of possible device states
        """

        """Device is detected but offline"""
        OFFLINE: str = "offline"
        """Device is online and active"""
        ONLINE: str = "device"
        """State of device is unknown"""
        UNKNOWN: str = "unknown"
        """Device is not detected"""
        NON_EXISTENT: str = "non-existent"

    class InsufficientStorageError(Exception):
        """
        Raised on insufficient storage on device (e.g. in install)
        """

    class CommandExecutionFailure(Exception):
        """
        raised on error to execute a command on the (remote) device
        """

        def __init__(self, return_code: int, msg: str):
            super().__init__(msg)
            self._return_code = return_code

        @property
        def return_code(self) -> int:
            return self._return_code

    class _Process:
        """
        Provides a basic interface to an underlying asyncio.Subprocess, providing access to an async generator
        for iterating over lines of output asynchronously
        """
        def __init__(self, process: asyncio.subprocess.Process):
            self._proc = process

        async def __aenter__(self) -> "Device._Process":
            return self

        async def __aexit__(self, exc_type: Optional[Type[BaseException]], exc_val: Optional[BaseException],
                            exc_tb: Optional[TracebackType]) -> None:
            if self._proc.returncode is None:
                log.info("Terminating process %d", self._proc.pid)
                try:
                    await self.stop()
                except Exception:
                    with suppress(Exception):
                        await self.stop(force=True)

        async def _next_line(self,  unresponsive_timeout: Optional[float] = None):
            if unresponsive_timeout is not None:
                return await asyncio.wait_for(self._proc.stdout.readline(), timeout=unresponsive_timeout)
            else:
                return await self._proc.stdout.readline()

        async def output(self,  unresponsive_timeout: Optional[float] = None) -> AsyncIterator[str]:
            """
            Async iterator over lines of output from process

            :param unresponsive_timeout: raise TimeoutException if not None and time to receive next line exceeds
               this given time span
            """
            if self._proc.stdout is None:
                raise Exception("Failed to capture output from subprocess")
            line = await self._next_line(unresponsive_timeout)
            while line:
                yield line.decode('utf-8')
                line = await self._next_line(unresponsive_timeout)

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

        @property
        def returncode(self) -> Optional[int]:
            return self._proc.returncode

    ERROR_MSG_INSUFFICIENT_STORAGE = "INSTALL_FAILED_INSUFFICIENT_STORAGE"
    # These packages may appear as running when looking at the activities in a device's activity stack. The running
    # of these packages do not affect interaction with the app under test. With the exception of the Samsung
    # MtpApplication (pop-up we can't get rid of that asks the user to update their device), they are also not visible
    # to the user. We keep a list of them so we know which ones to disregard when trying to retrieve the actual
    # to the user. We keep a list of them so we know which ones to disregard when trying to retrieve the actual
    # foreground application the user is interacting with.
    SILENT_RUNNING_PACKAGES = ["com.samsung.android.mtpapplication", "com.wssyncmldm", "com.bitbar.testdroid.monitor"]


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

    def __init__(self, device_id: str, adb_path: Optional[str] = None):
        """
        :param adb_path: path to the adb command on the host
        :param device_id: serial id of the device as seen by host (e.g. via 'adb devices')
           if None, pulls location from ANDROID_SDK_ROOT envion variable which must be defined

        :raises FileNotFoundError: if adb path is invalid or if not given and ANDROID_SDK_ROOT environ var is not set
        """
        adb_path = adb_path or Device.adb_path()
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

    def __del__(self):
        with suppress(Exception):
            del DeviceLock.locks[f"{self.device_id}-{os.getpid()}"]

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

    def _determine_system_property(self, property_: str) -> str:
        """
        :param property_: property to fetch
        :return: requested property or "UNKNOWN" if not present on device
        """
        prop = self.get_system_property(property_)
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
            output = self.execute_remote_cmd("shell", "echo", "$EXTERNAL_STORAGE")
            for msg in output.splitlines():
                if msg:
                    self._ext_storage = msg.strip()
                    break
        return self._ext_storage or "/sdcard"

    @property
    def is_alive(self) -> bool:
        return self.get_state() == Device.State.ONLINE

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

    ######################
    # command execution on device
    #######################

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
                           capture_stdout: bool = True,
                           stdout_redirect: Union[None, int, IO[AnyStr]] = subprocess.DEVNULL,
                           fail_on_presence_of_stderr: bool = False,
                           fail_on_error_code: Callable[[int], bool] = lambda x: x != 0) -> Optional[str]:
        # TODO: remove capture_stdout argument and clean up this API
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

        :raises: CommandExecutionFailure: if command fails to execute on remote device
        :raises: asyncio.TimeoutError if command fails to execute in time
        """
        timeout = timeout or Device.TIMEOUT_ADB_CMD
        log.debug(f"Executing remote command: {self._formulate_adb_cmd(*args)}")
        completed = subprocess.run(
            self._formulate_adb_cmd(*args), timeout=timeout, bufsize=0,
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE if capture_stdout and stdout_redirect == subprocess.DEVNULL else stdout_redirect,
            encoding='utf-8', errors='ignore'
        )
        if fail_on_error_code(completed.returncode) or (fail_on_presence_of_stderr and completed.stderr):
            raise self.CommandExecutionFailure(
                completed.returncode,
                f"Failed to execute '{' '.join(args)}' on device {self.device_id} [{completed.stderr}]")
        ret: Optional[str] = completed.stdout
        return ret

    def execute_remote_cmd_background(self, *args: str, stdout: Union[None, int, IO[AnyStr]] = subprocess.PIPE,
                                      **kwargs: Any) -> subprocess.Popen:  # noqa
        """
        Run the given command args in the background.

        :param args: command + list of args to be executed
        :param stdout: an optional file-like objection to which stdout is to be redirected (piped).
            defaults to subprocess.PIPE. If None, stdout is redirected to /dev/null

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

    async def monitor_remote_cmd(self, *args: str,
                                 loop: Optional[AbstractEventLoop] = None
                                 ) -> AsyncContextManager[Any]:
        """
        Coroutine for executing a command on this remote device asynchronously, allowing the client to iterate over
        lines of output.

        :param args: command to execute
        :param loop: event loop to asynchronously run under, or None for default event loop

        :return: AsyncGenerator iterating over lines of output from command

        >>> device = Device("some_id", "/path/to/adb")
        ... async with await device.monitor_remote_cmd("some_cmd", "with", "args", unresponsive_timeout=10) as proc:
        ...     async for line in proc.output():
        ...         # process(line)

        """
        # This is a lower level routine that clients of mdl-integration should not directly call. Other classes will
        # provide a cleaner and more direct API (e.g. TestApplication.run and DeviceLog.logcat will call this function
        # to do the heavy lifting, but they provide a clean external-facing interface to perform those functions).
        cmd = self._formulate_adb_cmd(*args)
        log.debug(f"Executing: {' '.join(cmd)}")
        proc = await asyncio.subprocess.create_subprocess_exec(*cmd,
                                                               stdout=asyncio.subprocess.PIPE,
                                                               stderr=asyncio.subprocess.STDOUT,
                                                               loop=loop or asyncio.events.get_running_loop(),
                                                               bufsize=0)  # noqa
        return self._Process(proc)

    ###################
    # Setting and getting device settings/properties
    ##################

    def get_device_setting(self, namespace: str, key: str, verbose: bool = True) -> Optional[str]:
        """
        Get a device setting

        :param namespace: android setting namespace
        :param key: which setting to get
        :param verbose: if False, silence any logging

        :return: value of the requested setting as string, or None if setting could not be found
        """
        try:
            output = self.execute_remote_cmd("shell", "settings", "get", namespace, key)
            if output.startswith("Invalid namespace"):  # some devices output a message with no error return code
                return None
            return output.rstrip()
        except Exception as e:
            if verbose:
                log.error(f"Could not get setting for {namespace}:{key} [{str(e)}]")
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
                self.execute_remote_cmd("shell", "settings", "put", namespace, key, value, capture_stdout=False)
            except Exception as e:
                log.error(f"Failed to set device setting {namespace}:{key}. Ignoring error [{str(e)}]")
        else:
            log.warning(f"Unable to detect device setting {namespace}:{key}")
        return previous_value

    def get_system_property(self, key: str, verbose: bool = True) -> Optional[str]:
        """
        :param key: the key of the property to be retrieved
        :param verbose: whether to print error messages on command execution problems or not

        :return: the property from the device associated with the given key, or None if no such property exists
        """
        try:
            output = self.execute_remote_cmd("shell", "getprop", key)
            return output.rstrip()
        except Exception as e:
            if verbose:
                log.error(f"Unable to get system property {key} [{str(e)}]")
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

    def get_locale(self) -> Optional[str]:
        """
        :return: device's current locale setting, or None if indeterminant
        """
        # try old way:
        lang = self.get_system_property('persist.sys.language').strip() or ""
        country = self.get_system_property('persist.sys.country').strip() or ""

        if lang and country:
            device_locale: Optional[str] = '_'.join([lang.strip(), country.strip()])
        else:
            device_locale = self.get_system_property('persist.sys.locale') \
                or self.get_system_property("ro.product.locale") or None
            if device_locale:
                device_locale = device_locale.replace('-', '_').strip()
        return device_locale

    def get_state(self) -> "Device.State":
        """
        :return: current state of emulaor ("device", "offline", "non-existent", ...)
        """
        try:
            state = self.execute_remote_cmd("get-state", capture_stdout=True, timeout=10).strip()
            mapping = {"device": Device.State.ONLINE,
                       "offline": Device.State.OFFLINE}
            return mapping.get(state, Device.State.UNKNOWN)
        except Exception:
            return Device.State.NON_EXISTENT

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

    ###############
    # Screenshot
    ###############

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
            self.execute_remote_cmd("shell", "screencap", "-p", capture_stdout=False,
                                    stdout_redirect=f.fileno(),
                                    timeout=timeout or Device.TIMEOUT_SCREEN_CAPTURE)

    ####################
    # Device activity
    ####################

    def _activity_stack_top(self, filt: Callable[[str], bool] = lambda x: True) -> Optional[str]:
        """
        :return: List of the app packages in the activities stack, with the first item being at the top of the stack
        """
        stdout = self.execute_remote_cmd("shell", "dumpsys", "activity", "activities", capture_stdout=True)
        # Find lines that look like this:
        #  * TaskRecord{133fbae #1340 I=com.google.android.apps.nexuslauncher/.NexusLauncherActivity U=0 StackId=0 sz=1}
        # or
        #  * TaskRecord{94c8098 #1791 A=com.android.chrome U=0 StackId=454 sz=1}
        app_record_pattern = re.compile(r'^\* TaskRecord\{[a-f0-9-]* #\d* [AI]=([a-zA-Z].[a-zA-Z0-9.]*)[ /].*')
        for line in stdout.splitlines():
            matches = app_record_pattern.match(line.strip())
            app_package = matches.group(1) if matches else None
            if app_package and filt(app_package):
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
        return self._activity_stack_top(filt=lambda x: x.lower() not in ignored)

    def get_activity_stack(self) -> List[str]:
        output = self.execute_remote_cmd("shell", "dumpsys", "activity", "activities", timeout=10)
        activity_list = []
        # Find lines that look like this:
        #  * TaskRecord{133fbae #1340 I=com.google.android.apps.nexuslauncher/.NexusLauncherActivity U=0 StackId=0 sz=1}
        # or
        #  * TaskRecord{94c8098 #1791 A=com.android.chrome U=0 StackId=454 sz=1}
        app_record_pattern = re.compile(r'^\* TaskRecord\{[a-f0-9-]* #\d* [AI]=(com\.[a-zA-Z0-9.]*)[ /].*')
        for line in output.splitlines():
            matches = app_record_pattern.match(line.strip())
            if matches:
                app_package = matches.group(1)
                activity_list.append(app_package)
        return activity_list

    ################
    # device control
    ################

    def reboot(self, wait_until_online: bool = True) -> None:
        self.execute_remote_cmd("reboot")
        if wait_until_online:
            while self.get_state() != Device.State.ONLINE:
                time.sleep(1)

    ##################
    # class properties
    ##################

    @classmethod
    def adb_path(cls):
        if "ANDROID_SDK_ROOT" not in os.environ:
            raise Exception("Unable to find path to 'adb'; ANDROID_SDK_ROOT environment variable must be set")
        adb = 'adb.exe' if sys.platform == 'win32' else 'adb'
        return os.path.join(os.environ.get("ANDROID_SDK_ROOT"), "platform-tools", adb)

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

    ################
    # Leased devices
    ################

    class LeaseExpired(Exception):

        def __init__(self, device: "Device"):
            super().__init__(f"Lease expired for {device.device_id}")
            self._device = device

        @property
        def device(self):
            return self._device

    # class _Leased:
    @classmethod
    def _Leased(cls) -> TypeVar('D', bound="Device", covariant=False):
        """
        This provides a Pythonic way of doing mixins to subclass a Device or a subclass of Device to
        be "Leased"

        :return: subclass of this class that can be set to expire after a prescribed amount of time
        """
        class LeasedDevice(cls):

            def __init__(self, *args: Any, **kargs: Any):
                # must come first to avoid issues with __getattribute__ override
                self._timed_out = False
                super().__init__(*args, **kargs)
                self._task: asyncio.Task = None

            async def set_timer(self, expiry: int):
                """
                set lease expiration

                :param expiry: number of seconds until expiration of lease (from now)
                """
                if self._task is not None:
                    raise Exception("Renewal of already existing lease is not allowed")
                self._task = asyncio.create_task(self._expire(expiry))

            async def _expire(self, expiry: int) -> None:
                """
                set the expiration time

                :param expiry: seconds into the future to expire
                """
                await asyncio.sleep(expiry)
                self._timed_out = True

            def reset(self) -> None:
                """
                Reset to no long have expiration.  Should only be called internally, and probably only by
                the AndroidTestOrchestrator orchestrating test execution
                """
                if self._task and not self._task.done():
                    self._task.cancel()
                self._task = None
                self._timed_out = False

            def __getattribute__(self, item: str) -> Any:
                # Playing with fire a little here -- make sure you know what you are doing if you update this method
                if item == '_device_id' or item == 'device_id':
                    # always allow this one to go through (one is a property reflecting the other)
                    return object.__getattribute__(self, item)
                if object.__getattribute__(self, "_timed_out"):
                    raise Device.LeaseExpired(self)
                return object.__getattribute__(self, item)

        return LeasedDevice


class DeviceBased:
    """
    Convenience base class for subclasses that  are associated
    with an underlying single device.  Prevents duplication by
    providing the association to the device and a property to
    get access to the associated device.

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


class DeviceNetwork(DeviceBased):
    """
    Provides API for checking external network and configuring host-to-device network ports

    :param device: which device is associated with this instance
    """

    async def check_network_connection(self, domain: str, count: int = 3) -> int:
        """
        Check network connection to domain

        :param domain: domain to ping
        :param count: how many times to ping domain

        :return: 0 on success, number of failed packets otherwise
        """
        try:
            async with await self.device.monitor_remote_cmd("shell", "ping", "-c", str(count), domain) as proc:
                async for line in proc.output(unresponsive_timeout=5):
                    if "64 bytes" in str(line):
                        count -= 1
                    if count <= 0:
                        break
            return count
        except subprocess.TimeoutExpired:
            log.error("ping is hanging and not yielding any results. Returning error code.")
            raise

    def port_forward(self, local_port: int, device_port: int) -> None:
        """
        forward traffic from local port to remote device port

        :param local_port: port to forward from
        :param device_port: port to forward to
        """
        self.device.execute_remote_cmd("forward", f"tcp:{device_port}", f"tcp:{local_port}")

    def remove_port_forward(self, port: Optional[int] = None) -> None:
        """
        Remove reverse port forwarding

        :param port: port to remove or None to remove all reverse forwarded ports
        """
        if port is not None:
            self.device.execute_remote_cmd("forward", "--remove", f"tcp:{port}")
        else:
            self.device.execute_remote_cmd("forward", "--remove-all")

    def reverse_port_forward(self, device_port: int, local_port: int) -> None:
        """
        reverse forward traffic on remote port to local port

        :param device_port: remote device port to forward
        :param local_port: port to forward to
        """
        self.device.execute_remote_cmd("reverse", f"tcp:{device_port}", f"tcp:{local_port}")

    def remove_reverse_port_forward(self, port: Optional[int] = None) -> None:
        """
        Remove reverse port forwarding

        :param port: port to remove or None to remove all reverse forwarded ports
        """
        if port is not None:
            self.device.execute_remote_cmd("reverse", "--remove", f"tcp:{port}")
        else:
            self.device.execute_remote_cmd("reverse", "--remove-all")


class DeviceNavigation(DeviceBased):
    """
    Provides API for navigating (going home, querying whether home screen is active, screen is on, ...)

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
        stdout = self.device.execute_remote_cmd("shell", "dumpsys", "activity", "activities", capture_stdout=True,
                                                timeout = Device.TIMEOUT_ADB_CMD)
        # Find lines that look like this:
        # Stack #0:
        # or
        # Stack #0: type=home mode=fullscreen
        app_stack_pattern = re.compile(r'^Stack #(\d*):')
        for line in stdout.splitlines():
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
                f"{stdout}")
        # Format of activities was fine, but detected home screen was not in foreground. But it is possible this is a
        # Samsung device with silent packages in foreground. Need to check if that's the case, and app after them
        # is the launcher/home screen.
        foreground_activity = self.device.foreground_activity(ignore_silent_apps=True)
        return bool(foreground_activity and foreground_activity.lower() == "com.sec.android.app.launcher")

    def input(self, subject: str, source: Optional[str] = None) -> None:
        """
        Send event subject through given source

        :param subject: event to send
        :param source: source of event, or None to default to "keyevent"
        """
        self.device.execute_remote_cmd("shell", "input", source or "keyevent", subject, capture_stdout=False)

    def is_screen_on(self) -> bool:
        """
        :return: whether device's screen is on
        """
        lines = self.device.execute_remote_cmd("shell", "dumpsys", "activity", "activities", timeout=10).splitlines()
        for msg in lines:
            if 'mInteractive=false' in msg or 'mScreenOn=false' in msg or 'isSleeping=true' in msg:
                return False
        return True

    def toggle_screen_on(self) -> None:
        """
        Toggle device's screen on/off
        """
        self.device.execute_remote_cmd("shell", "input", "keyevent", "KEYCODE_POWER", timeout=10)
