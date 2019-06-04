# flake8: noqay: F811
##########
# Tests the lower level Application class against a running emulator. These tests may
# be better server in mdl-integration-server directory, but we cannot start up an emulator
# from there
##########

import time

import asyncio
import pytest

from androidtestorchestrator import Device
from androidtestorchestrator.application import Application

from support import uninstall_apk

from unittest.mock import patch, PropertyMock

# noinspection PyShadowingNames
class TestApplication:

    def test_install_uninstall(self, device: Device, support_app: str):
        uninstall_apk(support_app, device)
        app = Application.from_apk(support_app, device)
        try:
            assert app.package_name == "com.linkedin.mdctest"
            output = device.execute_remote_cmd("shell", "dumpsys", "package", app.package_name, capture_stdout=True,
                                               timeout=10)
            for line in output.splitlines():
                if "versionName" in line:
                    assert app.version == line.strip().split('=', 1)[1]
        finally:
            app.uninstall()
            assert app.package_name not in device.list_installed_packages()

    def test_grant_permissions(self, device: Device, support_test_app: str):
        uninstall_apk(support_test_app, device)
        app = Application.from_apk(support_test_app, device)
        assert app.package_name.endswith(".test")
        try:
            permission = "android.permission.WRITE_EXTERNAL_STORAGE"
            app.grant_permissions([permission])
            output = device.execute_remote_cmd("shell", "dumpsys", "package", app.package_name, capture_stdout=True,
                                               timeout=10)
            perms = []
            look_for_perms = False
            for line in output.splitlines():
                if "granted=true" in line:
                    perms.append(line.strip().split(':', 1)[0])
                if "grantedPermissions" in line:
                    # older reporting style .  Ugh.  Yeah for inconsistencies
                    look_for_perms = True
                if look_for_perms:
                    if "permission" in line:
                        perms.append(line.strip())
            assert permission in perms
        finally:
            app.uninstall()

    # noinspection PyBroadException
    @staticmethod
    def pidof(app):
        # An inconsistency that appears either on older emulators or perhaps our own custom emulators even if pidof
        # fails due to it not being found, return code is 0, no exception is therefore raised and worse, error is
        # reported on stdout. Another inconsistency with our emulators: pidof not on the emulator? And return code
        # shows success :-*
        if app.device.api_level >= 26:
            try:
                # Normally get an error code and an exception if package is not running:
                output = app.device.execute_remote_cmd("shell", "pidof", "-s", app.package_name, fail_on_error_code=lambda x: x < 0)
                # however, LinkedIn-specific(?) or older emulators don't have this, and return no error code
                # so check output
                if not output:
                    return False
                if "not found" in output:
                    output = app.device.execute_remote_cmd("shell", "ps")
                    return app.package_name in output
                # on some device 1 is an indication of not present (some with return code of 0!), so if pid is one return false
                if output == "1":
                    return False
                return True
            except Exception:
                return False
        else:
            try:
                output = app.device.execute_remote_cmd("shell", "ps" )
                return app.package_name in output
            except:
                return False

    def test_start_stop(self, device: Device, support_app: str):  # noqa
        uninstall_apk(support_app, device)
        app = Application.from_apk(support_app, device)
        try:
            app.start(".MainActivity")
            time.sleep(3)  # Have to give time to "come up" :-(
            assert self.pidof(app), "No pid found for app; app not started as expected"
            app.stop(force=True)
            if self.pidof(app):
                time.sleep(3)  # allow slow emulators to catch up
            pidoutput = app.device.execute_remote_cmd("shell", "pidof", "-s", app.package_name, fail_on_error_code=lambda x: x < 0)
            assert not self.pidof(app), f"pidof indicated app is not stopped as expected; output of pidof is: {pidoutput}"
        finally:
            app.uninstall()

    def test_monkey(self, device: Device, support_app: str):  # noqa
        uninstall_apk(support_app, device)
        app = asyncio.get_event_loop().run_until_complete(Application.from_apk_async(support_app, device))
        try:
            app.monkey()
            time.sleep(3)
            assert self.pidof(app), "Failed to start app"
            app.stop(force=True)
            assert not self.pidof(app), "Failed to stop app"
        finally:
            app.uninstall()

    def test_clear_data(self, device: Device, support_app: str):  # noqa
        uninstall_apk(support_app, device)
        app = Application.from_apk(support_app, device)
        try:
            app.clear_data()  # should not raise exception
        finally:
            app.uninstall()

    def test_version_invalid_package(self, device: Device):
        with pytest.raises(Exception):
            Application.from_apk("no.such.package", device)

    def test_app_uninstall_logs_error(self, device: Device):
        with patch("androidtestorchestrator.application.log") as mock_logger:
            app = Application(package_name="com.android.providers.calendar", device=device)
            app.uninstall()
            assert mock_logger.error.called

    def test_clean_kill(self, device: Device, support_app: str):
        app = Application.from_apk(support_app, device)
        try:
            with patch('androidtestorchestrator.application.Application.pid', new_callable=PropertyMock) as mock_pid:
                # Force pid property to return None to make clean_kill method pass. Then we can use more reliable
                # method of checking app has been killed cleanly, than the method in the clean_kill() itself
                mock_pid.return_value = None
                app.start(".MainActivity")
                time.sleep(3)   # Give app time to come up
                assert device.foreground_activity() == app.package_name
                app.clean_kill()
                assert device.home_screen_active
                assert not self.pidof(app)
        finally:
            app.uninstall()
