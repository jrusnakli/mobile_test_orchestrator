"""
This package contains the core elements for device interaction: to query aspects of the device,
change or get settings and properties, and core API for raw execution of commands on the device (via adb), etc.
"""
from __future__ import annotations  # to correct mypy stubs vs run-time discrepancies

import asyncio
import datetime
import logging
import os
import re
import subprocess
import time

from contextlib import suppress, asynccontextmanager
from enum import Enum
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
    Type, TypeVar, Iterable,
)

from mobiletestorchestrator import ADB_PATH

log = logging.getLogger(__name__)


__all__ = ["Device", "RemoteDeviceBased", "_device_lock"]

# noinspection PyTypeChecker
D = TypeVar('D', bound="Device")


class DeviceLock:

    locks: Dict[str, asyncio.Semaphore] = {}


@asynccontextmanager
async def _device_lock(device: "Device") -> AsyncIterator["Device"]:
    """
    lock this device while a command is being executed against it

    Is static to avoid possible pickling issues in parallelized execution
    :param device: device to lock
    :return: device
    """
    # noinspection PyAsyncCall
    DeviceLock.locks.setdefault(device.device_id, asyncio.Semaphore())
    await DeviceLock.locks[device.device_id].acquire()
    yield device
    DeviceLock.locks[device.device_id].release()


class Device:
    """
    Class for interacting with a device via Google's adb command. This is intended to be a direct bridge to the same
    functionality as adb, with minimized embellishments

    :param device_id: serial id of the device as seen by host (e.g. via 'adb devices')
    :raises FileNotFoundError: if adb path is invalid
    """
    APP_RECORD_PATTERN = re.compile(r'^\* TaskRecord{[a-f0-9-]* #\d* [AI]=([a-zA-Z].[a-zA-Z0-9.]*)[ /].*')
    UNKNOWN_API_LEVEL = -1

    class State(Enum):
        """
        An enumeration of possible device states
        """

        """Device is detected but offline"""
        OFFLINE = "offline"
        """Device is online and active"""
        ONLINE = "device"
        """State of device is unknown"""
        UNKNOWN = "unknown"
        """Device is not detected"""
        NON_EXISTENT = "non-existent"

    # These packages may appear as running when looking at the activities in a device's activity stack. The running
    # of these packages do not affect interaction with the app under test. With the exception of the Samsung
    # MtpApplication (pop-up we can't get rid of that asks the user to update their device), they are also not visible
    # to the user. We keep a list of them so we know which ones to disregard when trying to retrieve the actual
    # to the user. We keep a list of them so we know which ones to disregard when trying to retrieve the actual
    # foreground application the user is interacting with.
    # noinspection SpellCheckingInspection
    SILENT_RUNNING_PACKAGES = ["com.samsung.android.mtpapplication", "com.wssyncmldm", "com.bitbar.testdroid.monitor"]

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

        >>> some_proc = asyncio.subprocess.create_subprocess_exec("cmd")
        ... async with Device.AsyncProcessContext(some_proc) as process:
        ...    async for line in process.output():
        ...        yield line
        """

        def __init__(self, proc_future: Awaitable[asyncio.subprocess.Process]):
            # we pass in a future mostly to avoid having to do something quirky like
            # async with await Process(...)
            self._proc_future = proc_future

        async def __aenter__(self) -> "Device.AsyncProcessContext":
            self._proc = Device.Process(await self._proc_future)
            return self

        @property
        def proc(self):
            return self._proc

        async def __aexit__(self, exc_type: Optional[Type[BaseException]], exc_val: Optional[BaseException],
                            exc_tb: Optional[TracebackType]) -> None:
            if self.proc.returncode and self.proc.returncode != 0 and exc_type is not None:
                raise Device.CommandExecutionFailure(
                    self.proc.returncode,
                    f"Remote command failed on device\n {await self.proc.communicate()}")
            if self.proc.returncode is None:
                log.info("Terminating process %d", self.proc.pid)
                with suppress(Exception):
                    await self.proc.stop(timeout=3)
            if self._proc.returncode is None:
                # Second try, force-stop
                try:
                    await self.proc.stop(timeout=3, force=True)
                except TimeoutError:
                    log.error("Failed to kill subprocess while exiting its context")

        async def wait(self, timeout: Optional[float] = None):
            """Wait until the process exit and return the process return code."""
            return await self._proc.wait(timeout=timeout)

        async def output(self,  unresponsive_timeout: Optional[float] = None) -> AsyncIterator[str]:
            async for line in self._proc.output(unresponsive_timeout=unresponsive_timeout):
                yield line

        async def stop(self, force: bool = False, timeout: Optional[float] = None) -> None:
            await self._proc.stop(force=force, timeout=timeout)

        # noinspection SpellCheckingInspection
        @property
        def returncode(self):
            return self._proc.returncode

    class Process:
        """
        Basic process interface that provides a means of monitoring line-by-line output
        of an `asyncio.subprocess.Process`.
        """

        def __init__(self, proc: asyncio.subprocess.Process):
            self._proc = proc

        # noinspection SpellCheckingInspection
        @property
        def returncode(self) -> Optional[int]:
            return self._proc.returncode

        @property
        def pid(self):
            return self._proc.pid

        async def communicate(self):
            stdout, _ = await self._proc.communicate()
            return stdout

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
                if line[-1] == '\n':
                    line = line[:-1]
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

    def __init__(self, device_id: str):
        """
        :param device_id: serial id of the device as seen by host (e.g. via 'adb devices')

        :raises FileNotFoundError: if adb path is invalid
        """
        self._device_id = device_id

        # These will be populated on as-needed basis and cached through the associated @property's
        self._model: Optional[str] = None
        self._brand: Optional[str] = None
        self._manufacturer: Optional[str] = None

        self._name: Optional[str] = None
        self._ext_storage = Device.override_ext_storage.get(self.model)
        self._device_server_datetime_offset: Optional[datetime.timedelta] = None
        self._api_level: Optional[int] = None

    def _activity_stack_top(self, pkg_filter: Callable[[str], bool] = lambda x: True) -> Optional[str]:
        """
        :return: List of the app packages in the activities stack, with the first item being at the top of the stack
        """
        completed = self.execute_remote_cmd("shell", "dumpsys", "activity", "activities",
                                            stdout=subprocess.PIPE)
        for line in completed.stdout.splitlines():
            matches = self.APP_RECORD_PATTERN.match(line.strip())
            app_package = matches.group(1) if matches else None
            if app_package and pkg_filter(app_package):
                return app_package
        return None  # to be explicit

    def _determine_system_property(self, prop_name: str) -> str:
        """
        :param prop_name: property to fetch
        :return: requested property or "UNKNOWN" if not present on device
        """
        prop = self.get_system_property(prop_name)
        if not prop:
            log.error("Unable to get brand of device from system properties. Setting to \"UNKNOWN\".")
            prop = "UNKNOWN"
        return prop

    def _verify_install(self, application_path: str, package: str, verify_screenshot_dir: Optional[str] = None) -> None:
        """
        Verify installation of an app, taking a screenshot on failure

        :param application_path: For logging which apk failed to install (upon any failure)
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
                      (application_path, self.model))
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
            # noinspection SpellCheckingInspection
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
    def is_alive(self) -> bool:
        return self.get_state() == Device.State.ONLINE

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
            return (str(ADB_PATH), "-s", self.device_id, *args)
        else:
            return (str(ADB_PATH), *args)

    async def execute_remote_cmd_async(self, *args: str,
                                       timeout: Optional[float] = None,
                                       stdout: Union[None, int, IO[AnyStr]] = None,
                                       stderr: Union[None, int, IO[AnyStr]] = asyncio.subprocess.PIPE,
                                       fail_on_error_code: Callable[[int], bool] = lambda x: x != 0
                                       ) -> Tuple[int, str, str]:
        """
        Execute a remote command on device asynchronously
        """
        proc = await asyncio.subprocess.create_subprocess_exec(*self._formulate_adb_cmd(*args),
                                                               stdout=stdout, stderr=stderr)
        if timeout:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        else:
            await proc.wait()
        stdout, stderr = await proc.communicate()
        if isinstance(stdout, bytes):
            stdout = stdout.decode('utf-8')
        if isinstance(stderr, bytes):
            stderr = stderr.decode('utf-8')
        if fail_on_error_code(proc.returncode):
            msg = '\n'.join([stdout or "", stderr or ""])
            with suppress(Exception):
                proc.kill()
            raise self.CommandExecutionFailure(proc.returncode, f"Failed to execute cmd: {msg or 'no message'}")
        return proc.returncode, stdout, stderr

    def execute_remote_cmd(self, *args: str,
                           timeout: Optional[float] = None,
                           stdout: Union[None, int, IO[AnyStr]] = None,
                           stderr: Union[None, int, IO[AnyStr]] = subprocess.PIPE,
                           fail_on_error_code: Callable[[int], bool] = lambda x: x != 0) \
            -> subprocess.CompletedProcess:
        # noinspection SpellCheckingInspection
        """
                Execute a command on this device (via adb)

                :param args: args to be executed (via adb command)
                :param timeout: raise asyncio.TimeoutError if command fails to execute in specified time (in seconds)
                :param stdout: how to pipe stdout, per subprocess.Popen like argument
                :param stderr: how to pipe stderr, per subprocess.Popen like argument
                :param fail_on_error_code: optional function that takes an error code that returns True if it represents
                    an error, False otherwise;  if None, any non-zero error code is treated as a failure
                :return:tuple of stdout, stderr output as requested (None for an output that is not directed
                    as subprocess.PIPE)
                :raises CommandExecutionFailureException: if command fails to execute on remote device
                """
        # protected method: OK to access by subclasses
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
        # noinspection SpellCheckingInspection
        """
        Run the given command args in the background.

        :param args: command + list of args to be executed
        :param stdout: an optional file-like objection to which stdout is to be redirected (piped).
            defaults to subprocess.PIPE. If None, stdout is redirected to /dev/null
        :param kwargs: dict arguments passed to subprocess.Popen
        :return: subprocess.Open
        """
        # protected method: OK to access by subclasses
        args = (str(ADB_PATH), "-s", self.device_id, *args)
        log.debug(f"Executing: {' '.join(args)} in background")
        if 'encoding' not in kwargs:
            kwargs['encoding'] = 'utf-8'
            kwargs['errors'] = 'ignore'
        return subprocess.Popen(args,
                                stdout=stdout or subprocess.DEVNULL,
                                stderr=subprocess.PIPE,
                                **kwargs)

    def monitor_remote_cmd(self, *args: str, include_stderr: bool = True) -> "Device.AsyncProcessContext":
        """
        Coroutine for executing a command on this remote device asynchronously, allowing the client to iterate over
        lines of output.

        :param args: command to execute
        :param include_stderr: if True, stderr output will be interleaved with stdout output
        :return: AsyncGenerator iterating over lines of output from command

        >>> device = Device('device1')
        ... async with await device.execute_remote_cmd_async("cmd", "with", "args", unresponsive_timeout=10) as stdout:
        ...     async for line in stdout:
        ...         # process(line)
        ...         pass

        """
        cmd = self._formulate_adb_cmd(*args)
        log.debug(f"Executing: {' '.join(cmd)}")
        proc_future = asyncio.subprocess.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT if include_stderr else None,
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
        :return: device's current locale setting, or None if undetermined
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

    def get_state(self, fail_if_not_present: bool = True) -> "Device.State":
        """
        :param fail_if_not_present: raise CommandExecutionFailure if device is not present and this is True,
           otherwise return Device.State.NON_EXISTENT
        :return: current state of emulator ("device", "offline", "non-existent", ...)
        """
        try:
            completed = self.execute_remote_cmd("get-state", timeout=10, stdout=subprocess.PIPE)
            stdout: str = completed.stdout
            return Device.State(stdout.strip())
        except ValueError:
            return Device.State.UNKNOWN
        except self.CommandExecutionFailure:
            if fail_if_not_present:
                raise
            return Device.State.NON_EXISTENT

    async def get_state_async(self, fail_if_not_present: bool = True) -> "Device.State":
        """
        get state of device emulator

        :param fail_if_not_present: if True, will raise CommendExecutionFailure if device is not , otherwise
           return Device.State.NON_EXISTENT
        """
        try:
            _, stdout, _ = await self.execute_remote_cmd_async("get-state", timeout=10, stdout=subprocess.PIPE)
            return Device.State(stdout.strip())
        except ValueError:
            return Device.State.UNKNOWN
        except self.CommandExecutionFailure:
            if fail_if_not_present:
                raise
            return Device.State.NON_EXISTENT

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
        # noinspection SpellCheckingInspection
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
        return self._activity_stack_top(pkg_filter=lambda x: x.lower() not in ignored)

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

    async def iterate_activity_stack(self) -> AsyncIterator[str]:
        async with self.monitor_remote_cmd("shell", "dumpsys", "activity", "activities") as proc:
            async for item in proc.output(unresponsive_timeout=Device.TIMEOUT_ADB_CMD):
                matches = self.APP_RECORD_PATTERN.match(item.strip())
                if matches:
                    app_package = matches.group(1)
                    yield app_package

    def list(self, kind: str) -> List[str]:
        """
        List available items of a given kind on the device
        :param kind: instrumentation or package

        :return: list of available items of given kind on the device
        """
        completed = self.execute_remote_cmd("shell", "pm", "list", kind, stdout=subprocess.PIPE)
        stdout: str = completed.stdout
        return stdout.splitlines()

    def list_async(self, kind: str) -> Device.AsyncProcessContext[str]:
        """
        List available items of a given kind on the device
        :param kind: instrumentation or package

        :return: list of available items of given kind on the device
        """
        return self.monitor_remote_cmd("shell", "pm", "list", kind)

    def list_installed_packages(self) -> List[str]:
        """
        :return: list of all packages installed on device
        """
        items = []
        for item in self.list("package"):
            if "package" in item:
                items.append(item.replace("package:", '').strip())
        return items

    async def iterate_installed_packages(self, timeout=2.0) -> AsyncIterator[str]:
        """
        :return: list of all packages installed on device
        """
        async with self.list_async("package") as proc:
            async for item in proc.output(unresponsive_timeout=timeout):
                if item.startswith('package:'):
                    item = item[8:]
                    yield item.strip()

    def list_instrumentation(self) -> List[str]:
        """
        :return: list of instrumentation(s) for a (test) app
        """
        return self.list("instrumentation")

    async def iterate_instrumentation(self, timeout: Optional[float] = 2.0) -> AsyncIterator[str]:
        """
        iterator over list of instrumentation(s) for a (test) app
        """
        async with self.list_async("instrumentation") as proc:
            async for line in proc.output(unresponsive_timeout=timeout):
                yield line

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
            # noinspection SpellCheckingInspection
            self.execute_remote_cmd("exec-out", "screencap", "-p",
                                    stdout=f.fileno(),
                                    timeout=timeout or Device.TIMEOUT_SCREEN_CAPTURE)

    async def reboot(self, wait_until_online: bool = True, timeout: Optional[float] = 5.0) \
            -> AsyncIterator[Device.State]:
        """
        reboot device

        :param wait_until_online: if True, will yield device state every second until ONLINE state is reached
        :param timeout: timeout for the actual reboot command being issued (NOT time it takes device to reboot)

        :raises asyncio.TimeoutExpired: if the command to the device takes to long to initiate the reboot
        """
        if wait_until_online:
            await self.execute_remote_cmd_async("reboot", timeout=timeout)
            state = await self.get_state_async()
            while state != Device.State.ONLINE:
                yield state
                state = await self.get_state_async()
                await asyncio.sleep(1)
            yield state
        else:
            await self.execute_remote_cmd_async("reboot", timeout=timeout)

    ################
    # Leased devices
    ################

    class LeaseExpired(Exception):

        def __init__(self, device: "Device"):
            super().__init__(f"Lease expired for {device.device_id}")
            self._device = device

        @property
        def device(self) -> "Device":
            return self._device

    # noinspection PyPep8Naming,PyTypeChecker
    @classmethod
    def _Leased(cls: Type[D]) -> D:
        """
        This provides a Python-ic way of doing mixins to subclass a Device or a subclass of Device to
        be "Leased"

        :return: subclass of this class that can be set to expire after a prescribed amount of time
        """
        class LeasedDevice(cls):

            def __init__(self, device_id: str):
                # must come first to avoid issues with __getattribute__ override
                self._timed_out = False
                super().__init__(device_id)
                self._task: Optional[asyncio.Task[Any]] = None

            async def set_timer(self, expiry: float) -> None:
                """
                set lease expiration

                :param expiry: number of seconds until expiration of lease (from now)
                """
                if self._task is not None:
                    raise Exception("Renewal of already existing lease is not allowed")
                self._task = asyncio.create_task(self._expire(expiry))

            async def _expire(self, expiry: float) -> None:
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

            # noinspection PyCallByClass
            def __getattribute__(self, item: str) -> Any:
                # Playing with fire a little here -- make sure you know what you are doing if you update this method
                if item == '_device_id' or item == 'device_id':
                    # always allow this one to go through (one is a property reflecting the other)
                    return object.__getattribute__(self, item)
                if object.__getattribute__(self, "_timed_out"):
                    raise Device.LeaseExpired(self)
                return object.__getattribute__(self, item)

        return LeasedDevice


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


class DeviceSet:

    class DeviceError(Exception):

        def __init__(self, device: Device, root: Exception):
            self._root_exception = root
            self._device = device

    def __init__(self, devices: Iterable[Device]):
        self._devices = list(devices)
        self._blacklisted: List[Device] = []

    @property
    def devices(self) -> List[Device]:
        return self._devices

    def blacklist(self, device: Device) -> None:
        if device in self._devices:
            self._devices.remove(device)
            self._blacklisted.append(device)

    def apply(self, method: Callable[..., Any], *args: Any, **kwargs: Any) -> List[Any]:
        results = []
        for device in self._devices:
            try:
                results.append(method(device, *args, **kwargs))
            except Exception as e:
                results.append(DeviceSet.DeviceError(device, e))
        return results

    async def apply_concurrent(self,
                               async_method: Callable[..., Awaitable[Any]],
                               *args: Any,
                               max_concurrent: Optional[int] = None,
                               **kwargs: Any
                               ) -> List[Any]:
        semaphore = asyncio.Semaphore(value=max_concurrent or len(self._devices))

        async def limited_async_method(device: Device) -> Any:
            await semaphore.acquire()
            try:
                return await async_method(device, *args, **kwargs)
            finally:
                semaphore.release()

        result: List[Any] = await asyncio.gather(*[limited_async_method(device) for device in self._devices],
                                                 return_exceptions=True)
        return result
