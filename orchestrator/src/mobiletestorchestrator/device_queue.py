import subprocess
from abc import ABC
from contextlib import asynccontextmanager
from asyncio.queues import Queue
from typing import Callable, AsyncGenerator

from mobiletestorchestrator import ADB_PATH
from mobiletestorchestrator.device import Device


class BaseDeviceQueue(ABC):

    def __init__(self, queue: Queue):
        """
        :param queue: queue to server Device's from.
        """
        self._q = queue

    def empty(self) -> bool:
        return self._q.empty()

    @staticmethod
    def _list_devices(pkg_filter: Callable[[str], bool]):
        cmd = [str(ADB_PATH), "devices"]
        completed = subprocess.run(" ".join(cmd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True)
        device_ids = []
        for line in completed.stdout.splitlines():
            line = line.decode('utf-8').strip()
            if line.strip().endswith("device"):
                device_id = line.split()[0]
                if pkg_filter(device_id):
                    device_ids.append(device_id)
        return device_ids

    @classmethod
    async def discover(cls, pkg_filter: Callable[[str], bool] = lambda x: True) -> "BaseDeviceQueue":
        """
        Discover all online devices and create a DeviceQueue with them

        :param pkg_filter: only include devices filtered by device id through this given filter, if provided

        :return: Created DeviceQueue instance containing all online devices
        """
        queue = Queue(20)
        device_ids = cls._list_devices(pkg_filter)
        if not device_ids:
            raise Exception("No device were discovered based on any filter criteria. ")
        for device_id in device_ids:
            await queue.put(Device(device_id))
        return cls(queue)


class AsyncDeviceQueue(BaseDeviceQueue):

    def __init__(self, queue: Queue):
        """
        :param queue: queue to server Device's from.
        """
        super().__init__(queue)

    @asynccontextmanager
    async def reserve(self) -> AsyncGenerator[Device, None]:
        """
        :return: a reserved Device
        """
        emulator = await self._q.get()
        try:
            yield emulator
        finally:
            await self._q.put(emulator)

    def empty(self):
        return self._q.empty()
