import json
import subprocess

import pytest

from typing import Callable, List

from testbutlerlive import ServiceApplication, Application
from testbutlerlive.parsing import TestButlerCommandParser
from testbutlerlive.device import Device

from conftest import adb, emulator, test_butler_test_app, test_butler_service  # noqa


@pytest.fixture()
def test_setup(request, adb, test_butler_service, test_butler_test_app):
    device = adb()
    butler_service = ServiceApplication.install(test_butler_service(), device)
    app = Application.install(test_butler_test_app(), device)

    def fin():
        butler_service.uninstall()
        app.uninstall()

    request.addfinalizer(fin)
    return butler_service, app


# noinspection PyMissingOrEmptyDocstring
class SettingListener(TestButlerCommandParser.DeviceChangeListener):
    """
    For testing device setting-change callback when cmd to change setting is inovked on test butler;
    no property change should occur while listening for events, but known settings change should occur
    """
    
    START_VALUE = "10"
    NEW_VALUE = "6"

    def __init__(self):
        self.setting_change_detected = False

    def device_property_changed(self, key, previous, new):
        assert False, "should not be invoked"

    def device_setting_changed(self, namespace, key, previous, new):
        assert namespace == "system"
        assert key == "volume_music"
        assert previous == SettingListener.START_VALUE
        assert new == SettingListener.NEW_VALUE
        self.setting_change_detected = True


# noinspection PyMissingOrEmptyDocstring
class PropertyListener(TestButlerCommandParser.DeviceChangeListener):
    """
    Likewise for property change with no settings change
    """

    START_VALUE = "10"
    NEW_VALUE = "6"

    def __init__(self):
        self.property_change_detected = False

    def device_property_changed(self, key, previous, new):
        assert key == "debug.mock_key"
        assert previous == "mock_value"
        assert new == "mock_new_value"
        self.property_change_detected = True

    def device_setting_changed(self, namespace, key, previous, new):
        assert False, "should not be invoked"


# noinspection PyMissingOrEmptyDocstring
class SettingAndPropertyListener(TestButlerCommandParser.DeviceChangeListener):
    """
    For testing parser_lines method where both property and settings change against known start/end values
    """
    START_VALUE = "9"
    NEW_VALUE = "1"

    def __init__(self):
        self.property_change_detected = False
        self.setting_change_detected = False

    def device_property_changed(self, key, previous, new):
        assert key == "debug.mock"
        assert previous == "mock_value"
        assert new == "42"
        self.property_change_detected = True

    def device_setting_changed(self, namespace, key, previous, new):
        assert namespace == "system"
        assert key == "volume_music"
        assert previous == SettingAndPropertyListener.START_VALUE
        assert new == SettingAndPropertyListener.NEW_VALUE
        self.setting_change_detected = True


# noinspection PyShadowingNames
class TestTestButlerCommandParser(object):

    def test_parse_line(self, adb: Callable[[], Device], test_butler_service, test_butler_test_app):
        device = adb()
        butler_service = ServiceApplication.install(test_butler_service(), device)
        app = Application.install(test_butler_test_app(), device)
        # start with a known confirmed value
        device.set_device_setting(namespace="system", key="volume_music", value=SettingAndPropertyListener.START_VALUE)
        assert device.get_device_setting(namespace="system", key="volume_music") == \
            SettingAndPropertyListener.START_VALUE
        device.set_system_property(key="debug.mock", value="mock_value")
        assert device.get_system_property("debug.mock") == "mock_value"

        listener = SettingAndPropertyListener()
        parser = TestButlerCommandParser(butler_service, app_under_test=app, listener=listener)

        for line in [
            "I/TestButler( ): 1 TEST_BUTLER_SETTING: system volume_music %s" % SettingAndPropertyListener.NEW_VALUE,
            "I/TestButler( ): 2 TEST_BUTLER_PROPERTY: debug.mock 42"]:
            parser.parse_line(line)

        assert listener.setting_change_detected
        assert device.get_device_setting("system", "volume_music") == "1"
        assert listener.property_change_detected
        assert device.get_system_property("debug.mock") == "42"

    @pytest.mark.parametrize('listener', [None, SettingListener()])
    def test_process_set_device_setting_cmd(self, adb: Callable[[], Device],
                                            listener, test_setup):
        device = adb()
        butler_service, app = test_setup
        # start with a known confirmed value
        device.set_device_setting(namespace="system", key="volume_music", value=SettingListener.START_VALUE)
        assert device.get_device_setting(namespace="system", key="volume_music") == SettingListener.START_VALUE

        # now set it through test butler to known different value
        TestButlerCommandParser(butler_service, app_under_test=app, listener=listener).\
            process_set_device_setting_cmd("system volume_music %s" % SettingListener.NEW_VALUE)
        # Listener will assert on keys and values and will set a flag to test here:
        if listener is not None:
            assert listener.setting_change_detected

        assert device.get_device_setting(namespace="system", key="volume_music") == "6"

    @pytest.mark.parametrize('listener', [None, PropertyListener()])
    def test_process_set_property_cmd(self, adb: Callable[[], Device], listener, test_setup):
        device = adb()
        butler_service, app = test_setup

        device.set_system_property(key="debug.mock_key", value="mock_value")
        assert device.get_system_property("debug.mock_key") == "mock_value"

        TestButlerCommandParser(butler_service, app_under_test=app, listener=listener).\
            process_set_property_cmd("debug.mock_key mock_new_value")
        if listener:
            assert listener.property_change_detected is True
        assert device.get_system_property("debug.mock_key") == "mock_new_value"

    def test_process_grant_permission_cmd(self, adb, test_setup):
        device = adb()
        butler_service, app = test_setup

        grant_permissions_invoked = False
        output = device.execute_remote_cmd("shell", "dumpsys", "package", app.package_name, capture_stdout=True)
        permission_to_grant = "android.permission.WRITE_EXTERNAL_STORAGE"

        # first make sure permission not already granted:
        for line in output.splitlines():
            assert "%s: granted=true" % permission_to_grant not in line

        # process commdand through parser
        parser = TestButlerCommandParser(butler_service, app_under_test=app, listener=None)
        assert parser.process_grant_permission_cmd(cmd=json.dumps({'type': 'permission',
                                                                   'package': app.package_name,
                                                                   'permissions': [permission_to_grant]
                                                                  })) == (0, "Success")

        # ensure permission was indeed granted
        output = device.execute_remote_cmd("shell", "dumpsys", "package", app.package_name, capture_stdout=True)
        for line in output.splitlines():
            if "%s: granted=true" % permission_to_grant in line:
                grant_permissions_invoked = True
        assert grant_permissions_invoked is True
