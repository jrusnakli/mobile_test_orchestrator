import asyncio
import datetime
import logging
import os
import re
import subprocess
import time

from contextlib import suppress
from typing import List, Union, AsyncGenerator, Iterable, Tuple, IO

log = logging.getLogger(__name__)

# in seconds:
TIMEOUT_ADB_CMD = 10
TIMEOUT_LONG_ADB_CMD = 4 * 60
TIMEOUT_SCREEN_CAPTURE = 2*60


def trace(e: Exception) -> str:
    import traceback
    return "%s\n%s" % (str(e), traceback.format_exc())


# noinspection PyShadowingNames,PyRedundantParentheses
class Device(object):
    """
    Class for interacting directly with a remote device over USB via Google's adb command.
    This is intended to be a direct bridge to the same functionality as adb, with minimized embellishments
    """
    override_ext_storage = {
        "Google Pixel": "/sdcard"
    }

    special_install_instructions = {}

    SLEEP_SET_PROPERTY = 2
    SLEEP_PKG_INSTALL = 5

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

    def __init__(self, device_id: str, adb_path: str):
        """

        :param adb_path: path to the adb command on the host
        :param device_id: serial id of the device as seen by host (e.g. via 'adb devices')
        """
        if not adb_path or not os.path.isfile(adb_path):
            raise Exception("Invalid adb path given: '%s'" % adb_path)
        self._device_id = device_id
        self._adb_path = adb_path
        if not self._adb_path:
            raise Exception("Could not find location of adb executable.  Is ANDROID_HOME set properly?")

        # These will be populated on as-needed basis and cached through the associated @property's
        self._model = None
        self._brand = None
        self._manufacturer = None

        self._name = None
        self._ext_storage = Device.override_ext_storage.get(self.model)

        # compute offset of clocks between host and device (roughly)
        def _device_datetime():
            # There is a variable on Android devices that holds the current epoch time of the device. We use that to
            # retrieve the device's datetime so we can easily calculate the difference of the start time from
            # other times during the test.
            with suppress(Exception):
                output = self.execute_remote_cmd("shell", "echo", "$EPOCHREALTIME", capture_stdout=True)
                for msg_ in output.splitlines():
                    if re.search(r"^\d+\.\d+$", msg_):
                        return datetime.datetime.fromtimestamp(float(msg_.strip()))

            log.error("Unable to get datetime from device.  No offset will be computed for timestamps")
            return None

        device_datetime = _device_datetime()
        self._device_server_datetime_offset = datetime.datetime.now() - \
            device_datetime if device_datetime is not None else None

    @property
    def device_id(self):
        """
        :return: the unique serial id of this device
        """
        return self._device_id

    @property
    def brand(self):
        """
        :return: The brand of the device as provided in it's system properties, or "UNKNOWN" if indeterminable
        """
        if not self._brand:
            self._brand = self.get_system_property("ro.product.brand")
            if not self._brand:
                log.error("Unable to get brand of device from system properties. Setting to UNKNOWN")
                self._brand = "UNKNOWN"
        return self._brand

    @property
    def model(self):
        """
        :return: the model of this device, or "UNKNOWN" if indeterminable
        """
        if not self._model:
            self._model = self.get_system_property("ro.product.model")
            if not self._model:
                log.error("Unable to get brand of device from system properties. Setting to UNKNOWN")
                self._model = "UNKNOWN"
        return self._model

    @property
    def manufacturer(self):
        """
        :return:  The manufacturer of this device, or "UNKNOWN" if indeterminable
        """
        if not self._manufacturer:
            self._manufacturer = self.get_system_property("ro.product.manufacturer")
            if not self._manufacturer:
                log.error("Unable to get brand of device from system properties. Setting to UNKNOWN")
                self._manufacturer = "UNKNOWN"
        return self._manufacturer

    @property
    def device_name(self) -> str:
        """
        :return: A name of this device based on model and manufacturer
        """
        if self._name is None:
            self._name = self.manufacturer + " " + self.model
        return self._name

    def _formulate_adb_cmd(self, *args: str) -> Iterable[str]:
        """
        :param args: args to the adb command

        :return: the adb command that executes the given arguments on the remote device from this host
        """
        if self.device_id:
            return (self._adb_path, "-s", self.device_id, *args)
        else:
            return (self._adb_path, *args)

    def execute_remote_cmd(self, *args: str, timeout=TIMEOUT_ADB_CMD, capture_stdout=True) -> Union[str, None]:
        """
        Execute a command on this device (via adb)

        :param args: args to be executed (via adb command)
        :param timeout: raise asyncio.TimeoutError if command fails to execute in specified time
        :param capture_stdout: whether to capture and return stdout output (otherwise return None)

        :return: None if no stdout output requested, otherwise a string containing the stoudt output of the command

        :raises: Exception if command fails to execute on remote device
        """
        if self.device_id:
            args = [self._adb_path, "-s", self.device_id, *args]
        else:
            args = [self._adb_path, *args]
        log.info("Executing: %s" % (' '.join(args)))
        completed = subprocess.run(args, timeout=timeout,
                                   stderr=subprocess.PIPE,
                                   stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
                                   encoding='utf-8', errors='ignore')
        if completed.returncode != 0:
            raise Exception("Failed to execute '%s' on device %s [%s]" % (' '.join(args), self.device_id,
                                                                          completed.stderr))
        if capture_stdout:
            return completed.stdout

    async def execute_remote_cmd_async(self, *args: str, future: asyncio.Future = None,
                                       allow_process_to_continue: bool = False) -> AsyncGenerator[str, None]:
        """
        coroutine for executing a command on this remote device asynchronously, allowing the client to
        iterate over lines of output

        :param args: command to execute
        :param future: if not None, will be set to proc's returncode or else set as exception if failure to execute
        :param allow_process_to_continue: Whether to return and allow process to continue in background, or
            to wait for process to end once client iterator is done processing output
        """
        proc = await asyncio.subprocess.create_subprocess_exec(*self._formulate_adb_cmd(*args),
                                                               stdout=asyncio.subprocess.PIPE,
                                                               stderr=asyncio.subprocess.PIPE)
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                yield line.decode('utf-8', errors='ignore')
            if not allow_process_to_continue:

                return_code = await proc.wait()
                if return_code != 0:
                    stderr = await proc.stderr.read()
                    stderr = stderr.decode('utf-8', errors='ignore`')
                    error_msg = "Failed to execute remote command '%s' [%s]" % (' '.join(args), stderr)
                    if future is not None:
                        future.set_exception(Exception(error_msg))
                    else:
                        log.error(error_msg)
                elif proc.returncode == 0 and future is not None:
                    future.set_result(proc.returncode)
            elif future:
                future.set_result(proc)
        finally:
            if not allow_process_to_continue and proc.returncode is None:
                with suppress(Exception):
                    proc.kill()
                    proc.stdout.close()
                    proc.stderr.close()

    def execute_remote_cmd_background(self, *args: str, stdout: Union[IO, None] = None, stderr: Union[IO, None] = None
                                      ) -> subprocess.Popen:
        """
        Execute given command args in background, returning the background process
        :param args: arguments to be executed
        :param stdout: output stream to pipe stdout
        :param stderr: output stream to pipe stderr
        """
        return subprocess.Popen(args=list(self._formulate_adb_cmd(*args)),
                                stdout=stdout, stderr=stderr,
                                encoding='utf8', errors='ignore')

    def set_device_setting(self, namespace: str, key: str, value: str) -> str:
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
                log.error("Failed to set device setting %s:%s.  Ignoring error [%s]" % (namespace, key, str(e)))
        else:
            log.warning("Unable to detect device setting %s:%s" % (namespace, key))
        return previous_value

    def get_device_setting(self, namespace: str, key: str) -> Union[None, str]:
        """
        Get a device setting

        :param namespace: android setting namespace
        :param key: which setting to get

        :return: value of the requested setting as string
        """
        try:
            output = self.execute_remote_cmd("shell", "settings", "get", namespace, key)
            return output.rstrip()
        except Exception as e:
            log.error("Could not get setting for %s:%s [%s]" % (namespace, key, str(e)))
            return None

    def set_system_property(self, key: str, value: str) -> str:
        """
        :param key: system property key to be set
        :param value: value to set to
        :return: previous value, in case client wishes to restore at some point
        """
        previous_value = self.get_system_property(key)
        try:
            self.execute_remote_cmd("shell", "setprop", key, value, capture_stdout=False)
        except Exception as e:
            log.error("Unable to set system property %s to %s [%s]" % (key, value, str(e)))
        return previous_value

    def get_system_property(self, key: str) -> Union[None, str]:
        """
        :param key: the key of the property to be retrieved

        :return: the property from the device associated with the given key, or None if no such property exists
        """
        try:
            output = self.execute_remote_cmd("shell", "getprop", key)
            return output.rstrip()
        except Exception as e:
            log.error("Unable to get system property %s [%s]" % (key, str(e)))
            return None

    def get_device_properties(self) -> str:
        """
        :return: full dict of properties
        """

        # a lot of output, so safer to stream it:
        async def get_props():
            proc = await asyncio.subprocess.create_subprocess_exec(*self._formulate_adb_cmd("shell", "getprop"),
                                                                   stdout=subprocess.PIPE,
                                                                   stderr=subprocess.PIPE)
            results = {}
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                line = line.decode('utf-8', errors='ignore')
                if ':' in line:
                    property_name, property_value = line.split(':', 1)
                    results[property_name.strip()[1:-1]] = property_value.strip()
            return results

        async def timed():
            return await asyncio.wait_for(get_props(), TIMEOUT_ADB_CMD)

        return asyncio.get_event_loop().run_until_complete(timed())

    def get_device_datetime(self) -> datetime.datetime:
        """
        :return: Best estimate of device's current datetime. If device's original datetime could not be computed during
            init phase, the server's datetime is returned.
        """
        current_device_time = datetime.datetime.utcnow() - \
            (self._device_server_datetime_offset if self._device_server_datetime_offset else datetime.timedelta())
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
        if not lang or not country:
            # if no dice, try new:
            device_locale = self.get_system_property('persist.sys.locale') or ""
            if not device_locale:
                device_locale = self.get_system_property("ro.product.locale").replace('-', '_')
            device_locale = device_locale.replace('-', '_').strip()
        else:
            device_locale = '_'.join([lang.strip(), country.strip()])
        return device_locale

    def set_locale(self, locale: str):
        """
        Set device's locale to new locale

        :param locale: locale to set on device
        """
        # invoke intent on test butler service to change system locale:
        self.execute_remote_cmd("shell", "am", "startservice", "-n",
                                "com.linkedin.android.testbutler/.ButlerService", "-a",
                                "com.linkedin.android.testbutler.SET_SYSTEM_LOCALE", "--es",
                                "locale", locale.replace("_", "-r"),
                                capture_stdout=False)
        if self.get_locale() != locale:
            # allow time for device and app to catch up
            time.sleep(self.SLEEP_SET_PROPERTY)
        if self.get_locale() != locale:
            raise Exception("System error: failed to set locale to %s" % locale)

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

    def take_screenshot(self, local_screenshot_path: str):
        """
        :param local_screenshot_path: path to store screenshot

        :return: full path to file name pulled to server, or None if failed
        """
        # Remember Android is always unix-like paths (aka do not use os.path.join):
        base_name = os.path.basename(local_screenshot_path)
        device_path = "%s/%s" % (self.external_storage_location, base_name)
        try:
            self.execute_remote_cmd("shell", "screencap", "-p", device_path, capture_stdout=False,
                                    timeout=TIMEOUT_SCREEN_CAPTURE)
            self.execute_remote_cmd("pull", device_path, local_screenshot_path, capture_stdout=False)
        finally:
            with suppress(Exception):
                self.execute_remote_cmd("shell", "rm", device_path)

    def oneshot_cpu_mem(self, package_name: str) -> Tuple[float, float]:
        """
        Get one-time cpu/memory usage data point
        :param package_name: package name to get cpu/mem data for
        :return: cpu, memory  or None, None if not obtainable
        """
        # TODO: Remove this in lieu of a better mechanism
        # first get format of output, then get results for requested package
        async def get_stats(lines: AsyncGenerator):
            async for msg in lines:
                if package_name in msg:
                    cpu, mem, name = msg.strip().split()
                    return cpu, mem
            return None, None

        async def run_stats():
            proc = await asyncio.subprocess.create_subprocess_exec(*self._formulate_adb_cmd("shell", "ps", "-o",
                                                                                            "PCPU,RSS,NAME"),
                                                                   stdout=asyncio.subprocess.PIPE)
            while True:
                line = await proc.stdout.readline()
                line = line.decode('utf-8', errors='ignore')  # encoding seems to be ignored if supplied above
                if not line:
                    break
                yield line

        return asyncio.get_event_loop().run_until_complete(get_stats(run_stats()))

    def input(self, subject):
        self.execute_remote_cmd("shell", "input", subject, capture_stdout=False)

    def check_network_connection(self, domain, count=3):
        """
        check network connection to domain

        :param domain: domain to ping
        :param count: how many times to ping domain

        :return: 0 on success, non-zero otherwise
        """
        async def ping():
            nonlocal count
            proc = await asyncio.subprocess.create_subprocess_exec(
                *self._formulate_adb_cmd("shell", "ping", "-c", str(count), domain),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
            try:
                while True:
                    msg = await proc.stdout.readline()
                    if not msg:
                        break
                    if "64 bytes" in str(msg.decode('utf-8', errors='ignore')):
                        count -= 1
                    if count <= 0:
                        break
            finally:
                return_code = await proc.wait()
                if return_code != 0:
                    return proc.returncode
                with suppress(Exception):
                    proc.kill()
                    proc.stdout.close()
                    proc.stderr.close()
            return count

        async def timed():
            return await asyncio.wait_for(ping(), timeout=count*5)

        try:
            return asyncio.get_event_loop().run_until_complete(timed())
        except asyncio.TimeoutError:
            log.error("ping is hanging and not yielding any results. returning error code")
            return -1

    def get_version(self, package):
        """
        get version of given package

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
            log.error("Unable to get version for package %s [%s]" % (package, trace(e)))
        return version


class RemoteDeviceBased(object):
    """
    Classes that are based on the context of a remote device
    """

    def __init__(self, device: Device):
        """
        :param device: Which device is associated with this instance
        """
        self._device = device
        self._ext_storage = None

    @property
    def device(self):
        """
        :return:  The device associated with this instance
        """
        return self._device
