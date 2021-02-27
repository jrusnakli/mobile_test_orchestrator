import time

import pytest
from unittest.mock import patch, Mock

from mobiletestorchestrator import Device
from mobiletestorchestrator.application import Application
from mobiletestorchestrator.device import DeviceInteraction


class TestDeviceInteraction:

    def test_is_screen_on(self, device: Device):
        device_nav = DeviceInteraction(device)
        is_screen_on = device_nav.is_screen_on()
        DeviceInteraction(device).toggle_screen_on()
        retries = 3
        new_is_screen_on = is_screen_on
        while retries > 0 and new_is_screen_on == is_screen_on:
            time.sleep(3)
            new_is_screen_on = device_nav.is_screen_on()
            retries -= 1
        assert is_screen_on != new_is_screen_on

    def test_return_home_succeeds(self, install_app, device: Device, support_app: str):
        app = install_app(Application, support_app)
        with patch('mobiletestorchestrator.device.DeviceInteraction.home_screen_active',
                   new_callable=Mock) as mock_home_screen_active:
            # Have to mock out call since inputting the KEYCODE_BACK event doesn't work for all devices/emulators
            mock_home_screen_active.return_value = True
            app.start(activity=".MainActivity")
            assert device.foreground_activity() == app.package_name
            device_nav = DeviceInteraction(device)
            device_nav.return_home()
            assert device_nav.home_screen_active()

    def test_return_home_fails(self, install_app, device: Device, support_app: str):
        app = install_app(Application, support_app)
        app.start(activity=".MainActivity")
        assert device.foreground_activity() == app.package_name
        with pytest.raises(expected_exception=Exception) as excinfo:
            # Nobody would ever really pass a negative number, but our test app has only one activity screen. So
            # need to pass -1 to force the function to reach its back button key-press limit
            DeviceInteraction(device).return_home(keycode_back_limit=-1)
        assert "Max number of back button presses" in str(excinfo.value)
