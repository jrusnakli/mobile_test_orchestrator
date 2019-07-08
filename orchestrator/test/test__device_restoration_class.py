import os

from androidtestorchestrator import AndroidTestOrchestrator, Device


class Test_DeviceRestoration:

    def test_device_setting_changed(self, fake_sdk):
        device = Device("fake_serial_id", os.path.join(fake_sdk, "platform-tools", "adb"))
        device_restoration = AndroidTestOrchestrator._DeviceRestoration(device)
        device_restoration.device_setting_changed("ns", "key", "old_value", "new_value")
        assert device_restoration._restoration_settings[("ns", "key")] == "old_value"
        device_restoration.device_setting_changed("ns", "key", "new_value", "newer_value")
        # only first change should stick
        assert device_restoration._restoration_settings[("ns", "key")] == "old_value"
        device_restoration.device_setting_changed("ns", "key", "newer_value", "old_value")
        # back to where we started, so should not longer be in restoration values:
        assert ("ns", "key") not in device_restoration._restoration_settings

    def test_device_property_changed(self, device: Device, fake_sdk):
        device = Device("fake_serial_id", os.path.join(fake_sdk, "platform-tools", "adb"))
        device_restoration = AndroidTestOrchestrator._DeviceRestoration(device)
        device_restoration.device_property_changed("key", "old_value", "new_value")
        assert device_restoration._restoration_properties["key"] == "old_value"
        device_restoration.device_property_changed("key", "new_value", "newer_value")
        assert device_restoration._restoration_properties["key"] == "old_value"
        device_restoration.device_property_changed("key", "newer_value", "old_value")
        assert "key" not in device_restoration._restoration_properties

    def test_restore(self, device: Device, monkeypatch, fake_sdk: str):
        def mock_set_device_setting(self_, *args, **kargs):
            assert args[0] == "ns"
            assert args[1] == "key"
            assert args[2] == "old_value"

        def mock_set_device_property(self_, *args, **kargs):
            assert args[0] == "key2"
            assert args[1] == "old_value2"

        monkeypatch.setattr("androidtestorchestrator.device.Device.set_device_setting", mock_set_device_setting)
        monkeypatch.setattr("androidtestorchestrator.device.Device.set_system_property", mock_set_device_property)
        device = Device("fake_serial_id", os.path.join(fake_sdk, "platform-tools", "adb"))
        device_restoration = AndroidTestOrchestrator._DeviceRestoration(device)
        device_restoration.device_setting_changed("ns", "key", "old_value", "new_value")
        device_restoration.device_property_changed("key2", "old_value2", "new_value")
        device_restoration.restore()
        assert not device_restoration._restoration_properties
        assert not device_restoration._restoration_settings
