# flake8: noqa: F401
##########
# Tests the lower level Devivce class against a running emulator.  These tests may
# be better server in mdl-integration-server directory, but we cannot start up an emulator
# from there
##########
import asyncio
import os
from typing import Callable

import pytest

from testbutlerlive.device import Device
from testbutlerlive.devicestorage import DeviceStorage
from testbutlerlive.application import Application, ServiceApplication

RESOURCE_DIR = os.path.join(os.path.dirname(__file__), "resources")


# noinspection PyShadowingNames
class TestAndroidDevice:

    def test_take_screenshot(self, adb: Callable[[], Device], tmpdir):  # noqa
        device = adb()
        path = os.path.join(str(tmpdir), "test_screenshot.png")
        device.take_screenshot(os.path.join(str(tmpdir), path))
        assert os.path.isfile(path)

    def test_device_name(self, adb: Callable[[], Device]):  # noqa
        adb = adb()
        name = adb.device_name
        assert name and name.lower() != "unknown"

    def test_get_set_device_setting(self, adb: Callable[[], Device]):  # noqa
        adb = adb()
        now = adb.get_device_setting("system", "dim_screen")
        new = {"1": "0", "0": "1"}[now]
        adb.set_device_setting("system", "dim_screen", new)
        assert adb.get_device_setting("system", "dim_screen") == new

    def test_get_set_system_property(self, adb: Callable[[], Device]):  # noqa
        adb = adb()
        adb.set_system_property("service.adb.tcp.port", "5555")
        assert adb.get_system_property("service.adb.tcp.port") == "5555"
        adb.set_system_property("service.adb.tcp.port", "\"\"")

    def test_install_uninstall_app(self, adb: Callable[[], Device], test_butler_app: Callable[[], str]):  # noqa
        device = adb()
        app = Application.install(test_butler_app(), device)
        app.uninstall()
        assert app.package_name not in device.list_installed_packages()

        app = Application.install(test_butler_app(), device)
        assert app.package_name in device.list_installed_packages()
        app.uninstall()
        assert app.package_name not in device.list_installed_packages()

    def test_list_packages(self, adb: Callable[[], Device], test_butler_app):  # noqa
        device = adb()
        app = Application.install(test_butler_app(), device)
        pkgs = device.list_installed_packages()
        assert app.package_name in pkgs

    def test_external_storage_location(self, adb: Callable[[], Device]):  # noqa
        device = adb()
        assert DeviceStorage(device).external_storage_location.startswith("/")

    def test_brand(self, adb: Callable[[], Device]):  # noqa
        device = adb()
        assert "android" in device.brand.lower() or "google" in device.brand.lower()

    def test_model(self, adb: Callable[[], Device]):  # noqa
        device = adb()
        assert device.model.startswith("Android SDK built for x86")

    def test_manufacturer(self, adb: Callable[[], Device]):  # noqa
        device = adb()
        assert device.manufacturer in ["Google", "unknown"]

    def test_get_device_datetime(self, adb: Callable[[], Device]):  # noqa
        import time
        import datetime
        device = adb()
        host_datetime = datetime.datetime.utcnow()
        dtime = device.get_device_datetime()
        host_delta = (host_datetime - dtime).total_seconds()
        time.sleep(1)
        host_datetime_delta = (datetime.datetime.utcnow() - device.get_device_datetime()).total_seconds()
        timediff = device.get_device_datetime() - dtime
        assert timediff.total_seconds() >= 0.99
        assert host_datetime_delta - host_delta < 0.01

    @pytest.mark.skipif(True, reason="Test butler does not currently support system setting of locale")
    def test_get_set_locale(self, adb: Callable[[], Device], local_changer_apk):  # noqa
        device = adb()
        app = Application.install(local_changer_apk, device)
        app.grant_permissions([" android.permission.CHANGE_CONFIGURATION"])
        device.set_locale("en_US")
        assert device.get_locale() == "en_US"
        device.set_locale("fr_FR")
        assert device.get_locale() == "fr_FR"

    def test_grant_permissions(self, adb: Callable[[], Device], test_butler_app: Callable[[], str]):  #noqa
        device = adb()
        app = Application.install(test_butler_app(), device)
        try:

            app.grant_permissions(["android.permission.WRITE_EXTERNAL_STORAGE"])
        finally:
            app.uninstall()

    def test_start_stop_app(self,
                            adb: Callable[[], Device],
                            test_butler_service: Callable[[], str],
                            test_butler_app: Callable[[], str]):  # noqa
        device = adb()
        app = Application.install(test_butler_app(), device)
        butler_app = ServiceApplication.install(test_butler_service(), device)

        try:
            app.start(activity=".MainActivity")
            butler_app.start(activity=".ButlerService")
            app.clear_data()
            app.stop()
            butler_app.stop()
        finally:
            app.uninstall()
            butler_app.uninstall()

    def test_invalid_cmd_execution(self, adb):
        device = adb()
        future = asyncio.get_event_loop().create_future()

        async def execute():
            async for _ in device.execute_remote_cmd_async("some", "bad", "command", future=future):
                pass

        asyncio.get_event_loop().run_until_complete(execute())
        with pytest.raises(Exception):
            try:
                future.result()
            except Exception as e:
                assert "some bad command" in str(e)
                raise

    def test_get_device_properties(self, adb):
        device = adb()
        all_properties = device.get_device_properties()
        assert "ro.product.model" in all_properties
        assert "ro.kernel.clocksource" in all_properties

    def test_get_locale(self, adb):
        device = adb()
        locale = device.get_locale()
        assert locale == "en_US"

    def test_check_network_connect(self, adb):
        device = adb()
        assert device.check_network_connection("linkedin.com", count=3) == 0

    def test_oneshot_cpu_mem(self, adb, test_butler_app):
        device = adb()
        app = Application.install(test_butler_app(), device)
        app.monkey()
        cpu, mem = device.oneshot_cpu_mem(app.package_name)
        app.stop(force=True)
        assert cpu is not None
        assert mem is not None
