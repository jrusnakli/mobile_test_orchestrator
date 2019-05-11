# flake8: noqay: F811
##########
# Tests the lower level Application class against a running emulator.  These tests may
# be better server in mdl-integration-server directory, but we cannot start up an emulator
# from there
##########
import time

import pytest

from androidtestorchestrator import Device
from androidtestorchestrator.application import Application


# noinspection PyShadowingNames
class TestApplication:

    def test_install_uninstall(self, device: Device, support_app: str):
        app = Application.install(support_app, device)
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
        app = Application.install(support_test_app, device)
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
        # an inconsistency that appears either on older emulators or perhaps our own custom emaulators
        # even if pidof fails due to it not being found, return code is 0, no exception is therefore
        # raised and worse, error is reported on stdout
        # Anpther inconsitency with our emulators: pidof not on the emulator?  And return code shows success :-*

        try:
            #Nomrally get an error code and an exception if package is not running:
            output = app.device.execute_remote_cmd("shell", "pidof", app.package_name)
            # however, LinkedIn-specific(?) or older emulators don't have this, and return no error code
            # so check output
            if not output:
                return False
            if "not found" in output:
                output = app.device.execute_remote_cmd("shell", "ps")
                return app.package_name in output
            return True
        except Exception:
            return False

    def test_start_stop(self, device: Device, support_app: str):  # noqa
        app = Application.install(support_app, device)
        try:
            app.start(".MainActivity")
            time.sleep(3)  # Have to give time to "come up" :-(
            assert self.pidof(app), "No pid found for app; app not started as expected"
            app.stop()
            assert not self.pidof(app)
        finally:
            app.uninstall()

    def test_monkey(self, device, support_app):  # noqa
        app = Application.install(support_app, device)
        try:
            app.monkey()
            time.sleep(3)
            assert self.pidof(app), "Failed to start app"
            app.stop()
            assert not self.pidof(app), "Failed to stop app"
        finally:
            app.uninstall()

    def test_clear_data(self, device, support_app):  # noqa
        app = Application.install(support_app, device)
        app.clear_data()  # should not raise exception

    def test__verify_install(self, device):
        with pytest.raises(Exception) as exc_info:
            Application._verify_install("some.apk", "no.such.package", device)
        assert "Failed to verify installation of app 'no.such.package'" in str(exc_info.value)

    def test_version_invalid_package(self, device):
        with pytest.raises(Exception):
            Application.install("no.such.package", device)
