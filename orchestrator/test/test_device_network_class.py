from androidtestorchestrator.device import Device, DeviceNetwork


class TestDeviceNetwork:

    def test_check_network_connect(self, device: Device):
        assert DeviceNetwork(device).check_network_connection("localhost", count=3) == 0
