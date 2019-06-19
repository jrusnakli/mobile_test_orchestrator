# flake8: noqay: F811
##########
# Tests the lower level Application class against a running emulator. These tests may
# be better server in mdl-integration-server directory, but we cannot start up an emulator
# from there
##########

import asyncio
import time
from unittest.mock import patch, PropertyMock

import pytest

from androidtestorchestrator import Device
from androidtestorchestrator.application import Application, TestApplication
from .support import uninstall_apk


# noinspection PyShadowingNames
class TestApplicationClass:

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

    def test_grant_permissions(self, device: Device, install_app, support_test_app):
        test_app = install_app(TestApplication, support_test_app)
        assert test_app.package_name.endswith(".test")
        permission = "android.permission.WRITE_EXTERNAL_STORAGE"
        test_app.grant_permissions([permission])
        output = device.execute_remote_cmd("shell", "dumpsys", "package", test_app.package_name, capture_stdout=True,
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

    def test_start_stop(self, install_app, support_app: str):  # noqa
        app = install_app(Application, support_app)
        app.start(".MainActivity")
        time.sleep(3)  # Have to give time to "come up" :-(
        assert self.pidof(app), "No pid found for app; app not started as expected"
        app.stop(force=True)
        if self.pidof(app):
            time.sleep(3)  # allow slow emulators to catch up
        pidoutput = app.device.execute_remote_cmd("shell", "pidof", "-s", app.package_name, fail_on_error_code=lambda x: x < 0)
        assert not self.pidof(app), f"pidof indicated app is not stopped as expected; output of pidof is: {pidoutput}"

    def test_monkey(self, device: Device, support_app):  # noqa
        app = asyncio.get_event_loop().run_until_complete(Application.from_apk_async(support_app, device))
        app.monkey()
        time.sleep(3)
        assert self.pidof(app), "Failed to start app"
        app.stop(force=True)
        assert not self.pidof(app), "Failed to stop app"

    def test_clear_data(self, install_app, support_app: str):  # noqa
        app = install_app(Application, support_app)
        app.clear_data()  # should not raise exception

    def test_version_invalid_package(self, device: Device):
        with pytest.raises(Exception):
            Application.from_apk("no.such.package", device)

    def test_app_uninstall_logs_error(self, device: Device):
        with patch("androidtestorchestrator.application.log") as mock_logger:
            app = Application(package_name="com.android.providers.calendar", device=device)
            app.uninstall()
            assert mock_logger.error.called

    def test_clean_kill_throws_exception_when_home_screen_not_active(self, install_app, device: Device, support_app: str):
        app = install_app(Application, support_app)
        with patch('androidtestorchestrator.Device.home_screen_active', new_callable=PropertyMock) as mock_home_screen_active:
            # Force home_screen_active to be false to indicate clean_kill failed
            mock_home_screen_active.return_value = False
            app.start(".MainActivity")
            time.sleep(3)   # Give app time to come up
            assert device.foreground_activity() == app.package_name
            with pytest.raises(Exception) as exc_info:
                app.clean_kill()
            assert "Failed to background current foreground app" in str(exc_info)

    def test_clean_kill_throws_exception_when_pid_still_existing(self, install_app, device: Device, support_app: str):
        app = install_app(Application, support_app)
        with patch('androidtestorchestrator.Device.home_screen_active', new_callable=PropertyMock) as mock_home_screen_active:
            with patch('androidtestorchestrator.application.Application.pid', new_callable=PropertyMock) as mock_pid:
                # Force home_screen_active to be True to indicate clean_kill made it to the home screen
                mock_home_screen_active.return_value = True
                # Force pid to return a fake process id to indicate clean_kill failed
                mock_pid.return_value = 1234
                app.start(".MainActivity")
                time.sleep(3)  # Give app time to come up
                assert device.foreground_activity() == app.package_name
                with pytest.raises(Exception) as exc_info:
                    app.clean_kill()
                assert "Detected app process is still running" in str(exc_info)

    def test_clean_kill_succeeds(self, install_app, device: Device, support_app: str):
        app = install_app(Application, support_app)
        with patch('androidtestorchestrator.Device.home_screen_active', new_callable=PropertyMock) as mock_home_screen_active:
            with patch('androidtestorchestrator.application.Application.pid', new_callable=PropertyMock) as mock_pid:
                # Force home_screen_active to be True to indicate clean_kill made it to the home screen
                mock_home_screen_active.return_value = True
                # Force pid to return None to make it seem like the process was actually killed
                mock_pid.return_value = None
                app.start(".MainActivity")
                time.sleep(3)  # Give app time to come up
                assert device.foreground_activity() == app.package_name
                # clean_kill doesn't return anything, so just make sure no exception is raised
                app.clean_kill()
