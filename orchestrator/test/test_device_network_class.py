from androidtestorchestrator.device import Device, DeviceConnectivity


class TestDeviceNetwork:

    def test_check_network_connect(self, device: Device):
        assert DeviceConnectivity(device).check_network_connection("localhost", count=3) == 0

    def test_port_forward(self, device: Device):
        device_network = DeviceConnectivity(device)
        device_network.port_forward(32451, 29323)
        output = device.execute_remote_cmd("forward", "--list")
        assert "32451" in output
        device_network.remove_port_forward(29323)
        output = device.execute_remote_cmd("forward", "--list")
        assert "32451" not in output
        assert "29323" not in output

    def test_reverse_port_forward(self, device: Device):
        device_network = DeviceConnectivity(device)
        device_network.reverse_port_forward(32451, 29323)
        output = device.execute_remote_cmd("reverse", "--list")
        assert "29323" in output
        device_network.remove_reverse_port_forward(32451)
        output = device.execute_remote_cmd("reverse", "--list")
        assert "29323" not in output
        assert "32451" not in output
