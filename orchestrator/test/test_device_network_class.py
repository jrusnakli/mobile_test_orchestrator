import asyncio
import subprocess

import pytest

from mobiletestorchestrator.device import Device
from mobiletestorchestrator.device_networking import DeviceConnectivity, AsyncDeviceConnectivity


class TestDeviceConnectivity:

    def test_check_network_connect(self, device: Device):
        assert DeviceConnectivity(device).check_network_connection("localhost", count=3) == 0

    def test_port_forward(self, device: Device):
        device_network = DeviceConnectivity(device)
        device_network.port_forward(32451, 29323)
        completed= device.execute_remote_cmd("forward", "--list", stdout=subprocess.PIPE)
        assert "32451" in completed.stdout
        device_network.remove_port_forward(29323)
        completed= device.execute_remote_cmd("forward", "--list", stdout=subprocess.PIPE)
        assert "32451" not in completed.stdout
        assert "29323" not in completed.stdout

    def test_reverse_port_forward(self, device: Device):
        device_network = DeviceConnectivity(device)
        device_network.reverse_port_forward(32451, 29323)
        completed= device.execute_remote_cmd("reverse", "--list", stdout=subprocess.PIPE)
        assert "29323" in completed.stdout
        device_network.remove_reverse_port_forward(32451)
        completed= device.execute_remote_cmd("reverse", "--list", stdout=subprocess.PIPE)
        assert "29323" not in completed.stdout
        assert "32451" not in completed.stdout


class TestDeviceConnectivityAsync:

    async def test_check_network_connect(self, device: Device):
        assert await AsyncDeviceConnectivity(device).check_network_connection("localhost", count=3) == 0

    async def test_port_forward(self, device: Device):
        device_network = AsyncDeviceConnectivity(device)
        await device_network.port_forward(32451, 29323)
        _, output, _ = await device.execute_remote_cmd_async("forward", "--list", stdout=subprocess.PIPE)
        assert "32451" in output
        await device_network.remove_port_forward(29323)
        _, output, _ = await device.execute_remote_cmd_async("forward", "--list", stdout=subprocess.PIPE)
        assert "32451" not in output
        assert "29323" not in output

    async def test_reverse_port_forward(self, device: Device):
        device_network = AsyncDeviceConnectivity(device)
        await device_network.reverse_port_forward(32451, 29323)
        _, output, _ = await device.execute_remote_cmd_async("reverse", "--list", stdout=subprocess.PIPE)
        assert "29323" in output
        await device_network.remove_reverse_port_forward(32451)
        _, output, _ = await device.execute_remote_cmd_async("reverse", "--list", stdout=subprocess.PIPE)
        assert "29323" not in output
        assert "32451" not in output

    @pytest.mark.asyncio
    async def test_check_network_connect(self, device: Device):
        device_network = AsyncDeviceConnectivity(device)
        assert await device_network.check_network_connection("localhost", count=3) == 0

    @pytest.mark.asyncio
    async def test_port_forward(self, device: Device):
        device_network = AsyncDeviceConnectivity(device)
        await device_network.port_forward(32451, 29323)
        _, output, _ = await device.execute_remote_cmd_async("forward", "--list", stdout=asyncio.subprocess.PIPE)
        assert "32451" in output
        await device_network.remove_port_forward(29323)
        _, output, _ = await device.execute_remote_cmd_async("forward", "--list", stdout=asyncio.subprocess.PIPE)
        assert "32451" not in output

    @pytest.mark.asyncio
    async def test_reverse_port_forward(self, device: Device):
        device_network = AsyncDeviceConnectivity(device)
        await device_network.reverse_port_forward(32451, 29323)
        _, output, _ = await device.execute_remote_cmd_async("reverse", "--list", stdout=asyncio.subprocess.PIPE)
        assert "29323" in output
        await device_network.remove_reverse_port_forward(32451)
        _, output, _ = await device.execute_remote_cmd_async("reverse", "--list", stdout=asyncio.subprocess.PIPE)
        assert "32451" not in output
