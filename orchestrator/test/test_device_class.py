# flake8: noqa: F401
##########
# Tests the lower level Device class against a running emulator. These tests may
# be better server in mdl-integration-server directory, but we cannot start up an emulator
# from there
##########
import asyncio
import datetime
import os
import subprocess
from pathlib import Path
from unittest.mock import patch, PropertyMock

import pytest
import time

from androidtestorchestrator.application import Application, TestApplication, ServiceApplication
from androidtestorchestrator.device import Device, DeviceInteraction, DeviceNetwork
from androidtestorchestrator.devicestorage import DeviceStorage
from . import support
from .conftest import TAG_MTO_DEVICE_ID
from .support import uninstall_apk

RESOURCE_DIR = os.path.join(os.path.dirname(__file__), "resources")

if TAG_MTO_DEVICE_ID not in os.environ:
    expected_device_info = {
        "model": [
            "Android SDK built for x86_64",
            "Android SDK built for x86",],
        "manufacturer": "unknown",
        "brand": "Android",
    }
else:
    # for debugging against local attached real device or user invoked emulator
    # This is not the typical test flow, so we use the Device class code to get
    # some attributes to compare against in test, which is not kosher for
    # a true test flow, but this is only run under specific user-based conditions
    android_sdk = support.find_sdk()
    adb_path = os.path.join(android_sdk, "platform-tools", support.add_ext("adb"))
    device = Device(os.environ[TAG_MTO_DEVICE_ID], adb_path=adb_path)
    expected_device_info = {
        "model": device.get_system_property("ro.product.model"),
        "manufacturer": device.get_system_property("ro.product.manufacturer"),
        "brand": device.get_system_property("ro.product.brand"),
    }


# noinspection PyShadowingNames
class TestAndroidDevice:

    @pytest.mark.asyncio
    async def test_take_screenshot(self, device: Device, temp_dir):
        path = os.path.join(str(temp_dir), "test_screenshot.png")
        device.take_screenshot(os.path.join(str(temp_dir), path))
        assert os.path.isfile(path)
        assert os.stat(path).st_size != 0

    @pytest.mark.asyncio
    async def test_take_screenshot_file_already_exists(self, device: Device, temp_dir):
        path = os.path.join(str(temp_dir), "created_test_screenshot.png")
        open(path, 'w+b')  # create the file
        with pytest.raises(FileExistsError):
            device.take_screenshot(os.path.join(str(temp_dir), path))

    @pytest.mark.asyncio
    async def test_device_name(self, device: Device):  # noqa
        name = device.device_name
        assert name and name.lower() != "unknown"

    @pytest.mark.asyncio
    async def test_get_set_device_setting(self, device: Device):
        now = device.get_device_setting("system", "dim_screen")
        new = {"1": "0", "0": "1"}[now]
        device.set_device_setting("system", "dim_screen", new)
        assert device.get_device_setting("system", "dim_screen") == new

    @pytest.mark.asyncio
    async def test_get_invalid_device_setting(self, device: Device):
        try:
            if int(device.get_system_property("ro.product.first_api_level")) < 26:
                assert device.get_device_setting("invalid", "nosuchkey") is ''
            else:
                assert device.get_device_setting("invalid", "nosuchkey") is None
        except:
            assert device.get_device_setting("invalid", "nosuchkey") is None

    @pytest.mark.asyncio
    async def test_set_invalid_system_property(self, device: Device):
        try:
            api_is_old =int(device.get_system_property("ro.build.version.sdk")) < 26
        except:
            api_is_old = False
        if api_is_old:
            device.set_system_property("nosuchkey", "value")
            assert device.get_system_property("nosuchkey") is ""
        else:
            with pytest.raises(Exception) as exc_info:
                device.set_system_property("nosuchkey", "value")
            assert "setprop: failed to set property 'nosuchkey' to 'value'" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_set_system_property(self, device: Device):
        device.set_system_property("debug.mock2", "5555")
        assert device.get_system_property("debug.mock2") == "5555"
        device.set_system_property("debug.mock2", "\"\"\"\"")

    @pytest.mark.asyncio
    async def test_install_uninstall_app(self, device: Device, support_app: str):
        uninstall_apk(support_app, device)
        app = Application.from_apk(support_app, device)
        app.uninstall()
        assert app.package_name not in device.list_installed_packages()

        app = Application.from_apk(support_app, device)
        assert app.package_name in device.list_installed_packages()
        app.uninstall()
        assert app.package_name not in device.list_installed_packages()

    @pytest.mark.asyncio
    async def test_list_packages(self, install_app, device: Device, support_app: str):
        app = install_app(Application, support_app)
        pkgs = device.list_installed_packages()
        assert app.package_name in pkgs

    @pytest.mark.asyncio
    async def test_external_storage_location(self, device: Device):
        assert DeviceStorage(device).external_storage_location.startswith("/")

    @pytest.mark.asyncio
    async def test_brand(self, device: Device):
        assert device.brand == expected_device_info["brand"]

    @pytest.mark.asyncio
    async def test_model(self, device: Device):
        assert device.model in expected_device_info["model"]

    @pytest.mark.asyncio
    async def test_manufacturer(self, device: Device):
        # the emulator used in test has no manufacturer
        """
        The emulator used in test has following properties
        [ro.product.vendor.brand]: [Android]
        [ro.product.vendor.device]: [generic_x86_64]
        [ro.product.vendor.manufacturer]: [unknown]
        [ro.product.vendor.model]: [Android SDK built for x86_64]
        [ro.product.vendor.name]: [sdk_phone_x86_64]
        """
        assert device.manufacturer == expected_device_info["manufacturer"]

    @pytest.mark.asyncio
    async def test_get_device_datetime(self, device: Device):
        import time
        import datetime
        host_datetime = datetime.datetime.utcnow()
        dtime = device.get_device_datetime()
        host_delta = (host_datetime - dtime).total_seconds()
        time.sleep(1)
        host_datetime_delta = (datetime.datetime.utcnow() - device.get_device_datetime()).total_seconds()
        timediff = device.get_device_datetime() - dtime
        assert timediff.total_seconds() >= 0.99
        assert host_datetime_delta - host_delta < 0.05

    @pytest.mark.asyncio
    async def test_invalid_cmd_execution(self, device: Device):
        async with await device.monitor_remote_cmd("some", "bad", "command") as proc:
            async for _ in proc.output(unresponsive_timeout=10):
                pass
        assert proc.returncode is not None
        assert proc.returncode != 0

    @pytest.mark.asyncio
    async def test_get_locale(self, device: Device):
        locale = device.get_locale()
        assert locale == "en_US"

    @pytest.mark.asyncio
    async def test_get_device_properties(self, device: Device):
        device_properties = device.get_device_properties()
        assert device_properties.get("ro.build.product", None) is not None
        assert device_properties.get("ro.build.user", None) is not None
        assert device_properties.get("ro.build.version.sdk", None) is not None

    @pytest.mark.asyncio
    async def test_foreground_and_activity_detection(self, install_app, device: Device, support_app: str):
        app = install_app(Application, support_app)
        nav = DeviceInteraction(device)
        # By default, emulators should always start into the home screen
        assert nav.home_screen_active()
        # Start up an app and test home screen is no longer active, and foreground app is correct
        app.start(activity=".MainActivity")
        assert not nav.home_screen_active()
        assert device.foreground_activity() == app.package_name

    @pytest.mark.asyncio
    async def test_raise_on_invalid_adb_path(self):
        with pytest.raises(FileNotFoundError):
            Device("some_serial_id", "/no/such/path")

    @pytest.mark.asyncio
    async def test_none_return_on_no_device_datetime(self, device: Device, monkeypatch):
        def mock_execute_cmd(*args, **kargs):
            return ""

        monkeypatch.setattr("androidtestorchestrator.device.Device.execute_remote_cmd", mock_execute_cmd)
        device._device_server_datetime_offset = None
        assert device.device_server_datetime_offset.total_seconds() == 0

    @pytest.mark.asyncio
    async def test_invalid_cmd_execution_unresponsive(self, device: Device, support_app: str):
        with pytest.raises(asyncio.TimeoutError):
            async with await device.monitor_remote_cmd("install", support_app) as proc:
                async for _ in proc.output(unresponsive_timeout=0.01):
                    pass


class TestDeviceNetwork:

    @pytest.mark.asyncio
    async def test_check_network_connect(self, device: Device):
        device_network = DeviceNetwork(device)
        assert await device_network.check_network_connection("localhost", count=3) == 0

    @pytest.mark.asyncio
    async def test_port_forward(self, device: Device):
        device_network = DeviceNetwork(device)
        device_network.port_forward(32451, 29323)
        completed = device.execute_remote_cmd("forward", "--list", stdout=subprocess.PIPE)
        output: str = completed.stdout
        assert "32451" in output
        device_network.remove_port_forward(29323)
        completed = device.execute_remote_cmd("forward", "--list", stdout=subprocess.PIPE)
        output: str = completed.stdout
        assert "32451" not in output

    @pytest.mark.asyncio
    async def test_reverse_port_forward(self, device: Device):
        device_network = DeviceNetwork(device)
        device_network.reverse_port_forward(32451, 29323)
        completed = device.execute_remote_cmd("reverse", "--list", stdout=subprocess.PIPE)
        output: str = completed.stdout
        assert "29323" in output
        device_network.remove_reverse_port_forward(32451)
        completed = device.execute_remote_cmd("reverse", "--list", stdout=subprocess.PIPE)
        output: str = completed.stdout
        assert "32451" not in output


class TestDeviceInteraction:

    @pytest.mark.asyncio
    async def test_is_screen_on(self, device: Device):
        navigator = DeviceInteraction(device)
        is_screen_on = navigator.is_screen_on()
        navigator.toggle_screen_on()
        retries = 3
        new_is_screen_on = is_screen_on
        while retries > 0 and new_is_screen_on == is_screen_on:
            time.sleep(3)
            new_is_screen_on = navigator.is_screen_on()
            retries -= 1
        assert is_screen_on != new_is_screen_on

    @pytest.mark.asyncio
    async def test_go_home(self, device: Device, android_app: Application):
        navigator = DeviceInteraction(device)
        await android_app.launch("MainActivity")
        if navigator.home_screen_active():
           time.sleep(2)
        assert not navigator.home_screen_active()
        navigator.go_home()
        if not navigator.home_screen_active():
            time.sleep(2)
        assert navigator.home_screen_active()
