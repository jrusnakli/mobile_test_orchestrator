import asyncio
import subprocess
import time
from typing import Optional

from mobiletestorchestrator.device import Device, RemoteDeviceBased, log


class DeviceConnectivity(RemoteDeviceBased):
    """
    API for network communications configuration/queries, including host-to-device communications
    """

    def check_network_connection(self, domain: str, count: int = 3) -> int:
        """
        Check network connection to domain

        :param domain: domain to ping
        :param count: how many times to ping domain
        :return: 0 on success, number of failed packets otherwise
        """
        try:
            completed = self._device.execute_remote_cmd("exec-out", "ping", "-c", str(count), domain,
                                                        timeout=Device.TIMEOUT_LONG_ADB_CMD,
                                                        stdout=subprocess.PIPE)
            for msg in completed.stdout.splitlines():
                if "64 bytes" in str(msg):
                    count -= 1
                if count <= 0:
                    break
            if count > 0:
                log.error("Output from ping was: \n%s", completed.stdout)
            return count
        except subprocess.TimeoutExpired:
            log.error("ping is hanging and not yielding any results. Returning error code.")
            return -1
        except Device.CommandExecutionFailure:
            return -1

    def port_forward(self, local_port: int, device_port: int) -> None:
        """
        forward traffic from local port to remote device port

        :param local_port: port to forward from
        :param device_port: port to forward to
        """
        self._device.execute_remote_cmd("forward", f"tcp:{device_port}", f"tcp:{local_port}")

    def remove_port_forward(self, port: Optional[int] = None) -> None:
        """
        Remove reverse port forwarding

        :param port: port to remove or None to remove all reverse forwarded ports
        """
        if port is not None:
            self._device.execute_remote_cmd("forward", "--remove", f"tcp:{port}")
        else:
            self._device.execute_remote_cmd("forward", "--remove-all")

    def reverse_port_forward(self, device_port: int, local_port: int):
        """
        reverse forward traffic on remote port to local port

        :param device_port: remote device port to forward
        :param local_port: port to forward to
        """
        self._device.execute_remote_cmd("reverse", f"tcp:{device_port}", f"tcp:{local_port}")

    def remove_reverse_port_forward(self, port: Optional[int] = None) -> None:
        """
        Remove reverse port forwarding

        :param port: port to remove or None to remove all reverse forwarded ports
        """
        if port is not None:
            self._device.execute_remote_cmd("reverse", "--remove", f"tcp:{port}")
        else:
            self.device.execute_remote_cmd("forward", "--remove-all")

    def reboot(self, wait_until_online: bool = True) -> None:
        self.device.execute_remote_cmd("reboot")
        if wait_until_online:
            while self.device.get_state() != Device.State.ONLINE:
                time.sleep(1)


class AsyncDeviceConnectivity(RemoteDeviceBased):
    """
    API for network communications configuration/queries, including host-to-device communications
    """

    async def check_network_connection(self, domain: str, count: int = 3) -> int:
        """
        Check network connection to domain

        :param domain: domain to ping
        :param count: how many times to ping domain
        :return: 0 on success, number of failed packets otherwise
        """
        try:
            stdout = ""
            async with self.device.monitor_remote_cmd("exec-out", "ping", "-c", str(count), domain) as proc:
                async for msg in proc.output(unresponsive_timeout=Device.TIMEOUT_ADB_CMD):
                    stdout += msg + '\n'
                    if "64 bytes" in str(msg):
                        count -= 1
                    if count <= 0:
                        break
            if count > 0:
                log.error("Output from ping was: \n%s", stdout)
            return count
        except asyncio.TimeoutExpired:
            log.error("ping is hanging and not yielding any results. Returning error code.")
            return -1

    async def port_forward(self, local_port: int, device_port: int, timeout=Device.TIMEOUT_ADB_CMD) -> None:
        """
        forward traffic from local port to remote device port

        :param local_port: port to forward from
        :param device_port: port to forward to
        """
        await self.device.execute_remote_cmd_async("forward", f"tcp:{device_port}", f"tcp:{local_port}",
                                                   timeout=timeout)

    async def remove_port_forward(self, port: Optional[int] = None, timeout=Device.TIMEOUT_ADB_CMD) -> None:
        """
        Remove reverse port forwarding

        :param port: port to remove or None to remove all reverse forwarded ports
        """
        if port is not None:
            await self._device.execute_remote_cmd_async("forward", "--remove", f"tcp:{port}", timeout=timeout)
        else:
            await self._device.execute_remote_cmd_async("forward", "--remove-all", timeout=timeout)

    async def reverse_port_forward(self, device_port: int, local_port: int, 
                                  timeout: Optional[float] = Device.TIMEOUT_ADB_CMD):
        """
        reverse forward traffic on remote port to local port

        :param device_port: remote device port to forward
        :param local_port: port to forward to
        :param timeout: if specified, call timesout if taking more than this time to execute
        
        :raises asyncio.TimeoutExpired: if timeout is specified and reached
        """
        await self._device.execute_remote_cmd_async("reverse", f"tcp:{device_port}", f"tcp:{local_port}",
                                                    timeout=timeout)

    async def remove_reverse_port_forward(self, port: Optional[int] = None, timeout=Device.TIMEOUT_ADB_CMD) -> None:
        """
        Remove reverse port forwarding

        :param port: port to remove or None to remove all reverse forwarded ports
        """
        if port is not None:
            await self.device.execute_remote_cmd_async("reverse", "--remove", f"tcp:{port}", timeout=timeout)
        else:
            await self.device.execute_remote_cmd_async("forward", "--remove-all", timeout=timeout)
