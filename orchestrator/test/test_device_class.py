# flake8: noqa: F401
##########
# Tests the lower level Device class against a running emulator. These tests may
# be better server in mdl-integration-server directory, but we cannot start up an emulator
# from there
##########
import asyncio
import os
from pathlib import Path

import pytest

from mobiletestorchestrator.application import Application, TestApplication
from mobiletestorchestrator.device import Device, DeviceInteraction
from mobiletestorchestrator.devicestorage import DeviceStorage
from . import support
from .conftest import TAG_MTO_DEVICE_ID

RESOURCE_DIR = os.path.join(os.path.dirname(__file__), "resources")

if TAG_MTO_DEVICE_ID not in os.environ:
    expected_device_info = {
        "model": "Android SDK built for x86_64",
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

    def test_take_screenshot(self, device: Device, mp_tmp_dir):
        path = os.path.join(str(mp_tmp_dir), "test_screenshot.png")
        device.take_screenshot(os.path.join(str(mp_tmp_dir), path))
        assert os.path.isfile(path)
        assert os.stat(path).st_size != 0

    def test_take_screenshot_file_already_exists(self, device: Device, mp_tmp_dir):
        path = os.path.join(str(mp_tmp_dir), "created_test_screenshot.png")
        open(path, 'w+b')  # create the file
        with pytest.raises(FileExistsError):
            device.take_screenshot(os.path.join(str(mp_tmp_dir), path))

    def test_device_name(self, device: Device):  # noqa
        name = device.device_name
        assert name and name.lower() != "unknown"

    def test_get_set_device_setting(self, device: Device):
        now = device.get_device_setting("system", "dim_screen")
        new = {"1": "0", "0": "1"}[now]
        device.set_device_setting("system", "dim_screen", new)
        assert device.get_device_setting("system", "dim_screen") == new

    def test_get_invalid_device_setting(self, device: Device):
        try:
            if int(device.get_system_property("ro.product.first_api_level")) < 26:
                assert device.get_device_setting("invalid", "nosuchkey") is ''
            else:
                assert device.get_device_setting("invalid", "nosuchkey") is None
        except:
            assert device.get_device_setting("invalid", "nosuchkey") is None

    def test_set_invalid_system_property(self, device: Device):
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
            assert f"setprop nosuchkey value' on device {device.device_id}" in str(exc_info.value)

    def test_get_set_system_property(self, device: Device):
        device.set_system_property("debug.mock2", "5555")
        assert device.get_system_property("debug.mock2") == "5555"
        device.set_system_property("debug.mock2", "\"\"\"\"")

    def test_list_packages(self, install_app, device: Device, support_app: str):
        app = install_app(Application, support_app)
        pkgs = device.list_installed_packages()
        assert app.package_name in pkgs

    def test_external_storage_location(self, device: Device):
        assert DeviceStorage(device).external_storage_location.startswith("/")

    def test_brand(self, device: Device):
        assert device.brand == expected_device_info["brand"]

    def test_model(self, device: Device):
        assert device.model in expected_device_info["model"]

    def test_manufacturer(self, device: Device):
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

    def test_get_device_datetime(self, device: Device):
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

    def test_grant_permissions(self, install_app, support_test_app: str):
        test_app = install_app(TestApplication, support_test_app)
        test_app.grant_permissions(["android.permission.WRITE_EXTERNAL_STORAGE"])

    def test_start_stop_app(self, install_app, support_app):  # noqa
        app = install_app(Application, support_app)

        app.start(activity=".MainActivity")
        app.clear_data()
        app.stop()

    def test_invalid_cmd_execution(self, device: Device):
        async def execute():
            async with device.monitor_remote_cmd("some", "bad", "command") as proc:
                async for _ in proc.output(unresponsive_timeout=10):
                    pass
            assert proc.returncode is not None
            assert proc.returncode != 0
        asyncio.get_event_loop().run_until_complete(execute())

    def test_get_locale(self, device: Device):
        locale = device.get_locale()
        assert locale == "en_US"

    def test_get_device_properties(self, device: Device):
        device_properties = device.get_device_properties()
        assert device_properties.get("ro.build.product", None) is not None
        assert device_properties.get("ro.build.user", None) is not None
        assert device_properties.get("ro.build.version.sdk", None) is not None

    def test_foreground_and_activity_detection(self, install_app, device: Device, support_app: str):
        app = install_app(Application, support_app)
        device_nav = DeviceInteraction(device)
        # By default, emulators should always start into the home screen
        assert device_nav.home_screen_active()
        # Start up an app and test home screen is no longer active, and foreground app is correct
        app.start(activity=".MainActivity")
        assert not device_nav.home_screen_active()
        assert device.foreground_activity() == app.package_name

    def test_verify_install_on_non_installed_app(self, device: Device, in_tmp_dir: Path):
        with pytest.raises(expected_exception=Exception) as excinfo:
            device._verify_install("fake/app/path", "com.linkedin.fake.app", "test_screenshots")
        assert "Failed to verify installation of app 'com.linkedin.fake.app'" in str(excinfo.value)
        assert (in_tmp_dir / "test_screenshots" / "install_failure-com.linkedin.fake.app.png").is_file()


