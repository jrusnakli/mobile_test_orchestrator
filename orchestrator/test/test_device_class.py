# flake8: noqa: F401
##########
# Tests the lower level Devivce class against a running emulator.  These tests may
# be better server in mdl-integration-server directory, but we cannot start up an emulator
# from there
##########
import asyncio
import os
import time

import pytest

from androidtestorchestrator.device import Device
from androidtestorchestrator.devicestorage import DeviceStorage
from androidtestorchestrator.application import Application, ServiceApplication

RESOURCE_DIR = os.path.join(os.path.dirname(__file__), "resources")


# noinspection PyShadowingNames
class TestAndroidDevice:

    def test_take_screenshot(self, device: Device, tmpdir):
        path = os.path.join(str(tmpdir), "test_screenshot.png")
        device.take_screenshot(os.path.join(str(tmpdir), path))
        assert os.path.isfile(path)

    def test_device_name(self, device: Device):  # noqa
        name = device.device_name
        assert name and name.lower() != "unknown"

    def test_get_set_device_setting(self, device: Device):
        now = device.get_device_setting("system", "dim_screen")
        new = {"1": "0", "0": "1"}[now]
        device.set_device_setting("system", "dim_screen", new)
        assert device.get_device_setting("system", "dim_screen") == new

    def test_get_invalid_decvice_setting(self, device: Device):
        assert device.get_device_setting("invalid", "nosuchkey") is None

    def test_set_invalid_system_property(self, device: Device):
        with pytest.raises(Exception) as exc_info:
            device.set_system_property("nosuchkey", "value")
        assert "Unable to set system property nosuchkey to value" in str(exc_info.value)

    def test_get_set_system_property(self, device: Device):
        device.set_system_property("debug.mock2", "5555")
        assert device.get_system_property("debug.mock2") == "5555"
        device.set_system_property("debug.mock2", "\"\"\"\"")

    def test_install_uninstall_app(self, device: Device, support_app: str):
        app = Application.install(support_app, device)
        app.uninstall()
        assert app.package_name not in device.list_installed_packages()

        app = Application.install(support_app, device)
        assert app.package_name in device.list_installed_packages()
        app.uninstall()
        assert app.package_name not in device.list_installed_packages()

    def test_list_packages(self, device: Device, support_app: str):
        app = Application.install(support_app, device)
        pkgs = device.list_installed_packages()
        assert app.package_name in pkgs

    def test_external_storage_location(self, device: Device):
        assert DeviceStorage(device).external_storage_location.startswith("/")

    def test_brand(self, device: Device):
        assert "android" in device.brand.lower() or "google" in device.brand.lower()

    def test_model(self, device: Device):
        assert device.model.startswith("Android SDK built for x86")

    def test_manufacturer(self, device: Device):
        assert device.manufacturer in ["Google", "unknown"]

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
        assert host_datetime_delta - host_delta < 0.01

    @pytest.mark.skipif(True, reason="Test butler does not currently support system setting of locale")
    def test_get_set_locale(self, device: Device, local_changer_apk):  # noqa
        app = Application.install(local_changer_apk, device)
        app.grant_permissions([" android.permission.CHANGE_CONFIGURATION"])
        device.set_locale("en_US")
        assert device.get_locale() == "en_US"
        device.set_locale("fr_FR")
        assert device.get_locale() == "fr_FR"

    def test_grant_permissions(self, device: Device, support_app: str):
        app = Application.install(support_app, device)
        try:

            app.grant_permissions(["android.permission.WRITE_EXTERNAL_STORAGE"])
        finally:
            app.uninstall()

    def test_start_stop_app(self,
                            device: Device,
                            test_butler_service: str,
                            support_app: str):  # noqa
        app = Application.install(support_app, device)
        butler_app = ServiceApplication.install(test_butler_service, device)

        try:
            app.start(activity=".MainActivity")
            butler_app.start(activity=".ButlerService", foreground=True)
            app.clear_data()
            app.stop()
            butler_app.stop()
        finally:
            app.uninstall()
            butler_app.uninstall()

    def test_invalid_cmd_execution(self, device: Device):
        async def execute():
            async for _ in await device.execute_remote_cmd_async("some", "bad", "command", wait_timeout=10):
                pass
        with pytest.raises(Exception) as exc_info:
            asyncio.get_event_loop().run_until_complete(execute())
        assert "some bad command" in str(exc_info)

    def test_get_device_properties(self, device: Device):
        all_properties = device.get_system_properties()
        assert "ro.product.model" in all_properties

    def test_get_locale(self, device: Device):
        locale = device.get_locale()
        assert locale == "en_US"

    def test_check_network_connect(self, device: Device):
        assert device.check_network_connection("localhost", count=3) == 0

    def test_oneshot_cpu_mem(self, device: Device, support_app: str):
        app = Application.install(support_app, device)
        app.monkey()
        time.sleep(1)
        cpu, mem = device.oneshot_cpu_mem(app.package_name)
        app.stop(force=True)
        assert cpu is not None
        assert mem is not None
