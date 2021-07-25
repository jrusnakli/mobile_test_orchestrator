"""
The package *devicepool" contains *DevicePool* classes for reserving `Device`'s and `Emulator`'s from a queue.
The interface for setting and expiration on reserved Device's/Emulator's is also provided through these classes.
"""
from __future__ import annotations  # to correct mypy stubs vs run-time discrepancies
import asyncio
import logging
import multiprocessing
import os
import queue
import subprocess
from abc import ABC, abstractmethod
from asyncio import Queue
from contextlib import asynccontextmanager, suppress
from typing import Any, AsyncGenerator, Callable, Generic, List, Optional, TypeVar, Type, Union

from mobiletestorchestrator import ADB_PATH
from mobiletestorchestrator.device import Device
from mobiletestorchestrator.emulators import Emulator, EmulatorBundleConfiguration

It = TypeVar('It')

log = logging.getLogger(__name__)


class AbstractAsyncQueue(ABC, Generic[It]):

    @abstractmethod
    async def get(self) -> It:
        """
        :return: item from queue
        """

    @abstractmethod
    async def put(self, item: It) -> None:
        """
        :param item: item to place in the queue
        """


Q = TypeVar('Q', "queue.Queue[Any]", "multiprocessing.Queue[Any]")


class AsyncQueueAdapter(AbstractAsyncQueue[It]):
    """
    Adapt a non-async queue to be asynchronous (via polling)

    :param q: underlying non-async queue to draw from/push to
    :param polling_interval" time interval to asyncio.sleep in between get_nowait calls to underlying non-async queue
    """

    def __init__(self, q: Q, polling_interval: float = 0.5):
        self._polling_interval = polling_interval
        self._queue: Q = q  # type: ignore

    async def get(self) -> It:
        item = None
        while not item:
            try:
                return self._queue.get_nowait()  # type: ignore
            except queue.Empty:
                await asyncio.sleep(self._polling_interval)

    async def put(self, item: Any) -> None:
        while True:
            try:
                self._queue.put_nowait(item)  # type: ignore
                break
            except queue.Full:
                await asyncio.sleep(self._polling_interval)


AsyncQ = TypeVar('AsyncQ', bound=Union[AbstractAsyncQueue, Queue])  # type: ignore
Pool = TypeVar('Pool', bound="BaseDevicePool")


class BaseDevicePool(ABC):
    """
    Abstract base class for all device queues.
    """
    def __init__(self, queue: Union[AbstractAsyncQueue[Device], Queue[Device]]):
        """
        :param queue: queue to server Device's from.
        """
        self._q = queue

    @classmethod
    def _list_devices(cls, filt: Callable[[str], bool]) -> List[str]:
        cmd = [str(ADB_PATH), "devices"]
        completed = subprocess.run(" ".join(cmd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True)
        device_ids = []
        for line in completed.stdout.decode('utf-8').splitlines():
            if line.strip().endswith("device"):
                device_id = line.split()[0]
                if filt(device_id):
                    device_ids.append(device_id)
        return device_ids

    @classmethod
    async def discover(cls: Type[Pool], filt: Callable[[str], bool] = lambda x: True) -> Pool:
        """
        Discover all online devices and create a DeviceQueue with them

        :param filt: only include devices filtered by device id through this given filter, if provided

        :return: Created DeviceQueue instance containing all online devices
        """
        q: Queue[Device] = Queue(20)
        device_ids = BaseDevicePool._list_devices(filt)
        if not device_ids:
            raise queue.Empty("Empty queue. No device were discovered based on any filter critera.")
        for device_id in device_ids:
            await q.put(Device(device_id))
        return cls(q)

    @asynccontextmanager
    @abstractmethod
    async def reserve(self, timeout: Optional[float] = None) -> AsyncGenerator[Optional[Device], None]:
        """
        :param timeout: If specified, will timeout if reservation can not be made in time
        :return: Generator for device reserved from queue, or None if device pool is empty
        :raises: TimeoutError if reservation cannot be made in time
        """
        yield None

    @asynccontextmanager
    @abstractmethod
    async def reserve_many(self, count: int, timeout: Optional[float] = None) -> AsyncGenerator[List[Device], None]:
        """
        Reserve more than one device
        :param count: number of devices to reserve
        :param timeout: If specified, will timeout if reservation can not be made in time
        :return: generator of list of devices that have been reserved, which may be less than requested if
           pool does not contain enough devices/emulators (or if empty, will return an empty list)
        :raises: TimeoutError if reservation cannot be made in time
        """
        yield []


class AsyncDevicePool(BaseDevicePool):
    """
    Class providing an async interface for a general device queue (aka, agnostic to
    whether devices are Emulator's or Device's).
    """

    """Subclass of Device with an ability to set an expiration time"""
    # class LeasedDevice:
    LeasedDevice = Device._Leased()

    def __init__(self, queue: AsyncQ):
        """
        :param queue: queue to server Device's from.
        """
        super().__init__(queue)

    @asynccontextmanager
    async def reserve(self, timeout: Optional[float] = None) -> AsyncGenerator[Optional[Device], None]:
        if timeout:
            device = await asyncio.wait_for(self._q.get(), timeout=timeout)
        else:
            device = await self._q.get()
        try:
            if device is None:
                raise queue.Empty("No emulators found.  Possibly failed to launch any")
            yield device
        finally:
            if hasattr(device, "reset_lease"):
                device.reset_lease()  # type: ignore
            await self._q.put(device)

    @asynccontextmanager
    async def reserve_many(self, count: int, timeout: Optional[float] = None) -> AsyncGenerator[List[Device], None]:
        devices = []
        queue_empty = False
        for _ in range(count):
            if timeout:
                device = await asyncio.wait_for(self._q.get(), timeout=timeout)
            else:
                device = await self._q.get()
            if device is None:
                # other threads may need to know this to:
                await self._q.put(device)
                queue_empty = True
                break
            devices.append(device)
        try:
            if queue_empty:
                raise queue.Empty("No emulators found.  Possibly failed to launch any")
            yield devices
        finally:
            for device in devices:
                if hasattr(device, "reset_lease"):
                    device.reset_lease()  # type: ignore
                await self._q.put(device)

    @staticmethod
    def from_external(queue: multiprocessing.Queue) -> "AsyncDevicePool":  # type: ignore
        """
        Return an AsyncDeviceQueue instance from the given multiprocessing Queue (i.e., with devices provided
        from an external process, which must be running on the same host)

        :param queue: non-async Queue to draw devices from
        :return: an AsynDeviceQueue that draws from the given (non-async) queue
        """
        adapter: AsyncQueueAdapter[Device] = AsyncQueueAdapter(queue)
        return AsyncEmulatorPool(adapter)  # type: ignore


class AsyncEmulatorPool(AsyncDevicePool):
    """
    A class used to by clients wishing to be served emulators.  Clients reserve and emulator, which when
    finished, relinquish it back into the queue.  Emulators can be "leased", in which case the class instance
    of the device will be disabled (the API not the device istelf) after the lease expires, at which point
    the device will be placed back into the queue and available to other clients.

    It is recommended to use one of the class factory methods ("create" or "discover") to create an
    emulator queue instance.

    :param queue: queue used to serve emulators
    :param max_lease_time: optional maximum amount of time client can hold a "lease" on this emulator, with
       any attempts to execute commands against the device raising a "LeaseExpired" exception after this time.
    """

    # class LeasedDevice:
    """Subclass of Emaultor with an ability to set an expiration time"""
    LeasedEmulator: Emulator = Emulator._Leased()

    MAX_BOOT_RETRIES = 2

    def __init__(self, queue: Queue, max_lease_time: Optional[int] = None):  # type: ignore
        super().__init__(queue)
        self._max_lease_time = max_lease_time
        self._closed = False
        self._queue = queue

    @classmethod
    @asynccontextmanager
    async def create(cls, count: int, avd: str, config: EmulatorBundleConfiguration, *args: str,
                     external_queue: Optional[AbstractAsyncQueue["Emulator"]] = None,
                     max_lease_time: Optional[int] = None,
                     wait_for_startup: Optional[int] = None) -> AsyncGenerator["AsyncEmulatorPool", None]:
        """
        Create an emulator queue by lanuching them explicitly.  Returns quickly unless specified otherwise,
        launching the emulators in the background

        :param count: how many emulators in queue
        :param config: emulator bundle config
        :param avd: avd to launch
        :param args: additional arguments to pass to the emulator launch command
        :param external_queue: an external asyncio.Queue to use for queueing devices, or None to create internally
        :param max_lease_time: see constructor
        :param wait_for_startup: opiontal amount of time to wait emulators to be started before a TimeoutError is raised
        :return: new EmulatorQueue populated with requested emulators
        :raises: TimeoutError if *wait_for_startup* is specified and emulaors not started in time
        """
        if count > len(Emulator.PORTS):
            raise Exception(f"Can have at most {count} emulators at one time")
        queue: Queue[Emulator] = external_queue or Queue(count)  # type: ignore
        emulators: List[Emulator] = []
        error_count = 0

        async def launch_one(index: int, avd: str, config: EmulatorBundleConfiguration, *args: str) -> None:
            nonlocal error_count
            nonlocal emulators
            await asyncio.sleep(index * 3)  # space out launches as this can help with avoiding instability
            port = Emulator.PORTS[index]
            try:
                if max_lease_time:
                    leased_emulator: Optional[Emulator] = await cls.LeasedEmulator.launch(
                        port, avd, config, *args, retries=AsyncEmulatorPool.MAX_BOOT_RETRIES)
                    leased_emulator.set_timer(expiry=max_lease_time)  # type: ignore
                else:
                    leased_emulator = await Emulator.launch(port, avd, config, *args)
                if leased_emulator:
                    emulators.append(leased_emulator)
            except Exception:
                log.exception(f"Failed to boot emulator on port {port}")
                error_count += 1
                if error_count == count:
                    # if all emulators failed to boot, signal queue is empty to any clients waiting on queue
                    leased_emulator = None
                else:
                    raise
            if leased_emulator:
                log.debug(f">>>>> Putting {leased_emulator.device_id} in the queue...")
            await queue.put(leased_emulator)  # type: ignore

        futures = [asyncio.create_task(launch_one(index, avd, config, *args)) for index in range(count)]
        try:
            emulator_q = cls(queue, max_lease_time=max_lease_time)
            emulator_q._futures = futures
            if wait_for_startup:
                await emulator_q.wait_for_startup()
            yield emulator_q
        finally:
            if not wait_for_startup:
                for task in futures:
                    if not task.done():
                        with suppress(Exception):
                            task.cancel()
            for em in emulators:
                with suppress(Exception):
                    em.kill()

    async def wait_for_startup(self):
        for future in self._futures:
            device = await future
            if device is not None:
                await self._queue.put(device)

    @staticmethod
    async def discover_emulators(max_lease_time: Optional[int] = None) -> "AsyncEmulatorPool":
        """
        Discover all online devices and create a DeviceQueue with them

        :param max_lease_time: see constructor
        :param config: Definition of emulator configuration (for access to root sdk), or None to use env vars
        :return: Created DeviceQueue instance containing all online devices
        """
        queue: Queue[Emulator] = Queue(20)
        emulator_ids = BaseDevicePool._list_devices(filt=lambda x: x.startswith('emulator-'))
        if not emulator_ids:
            raise Exception("No emulators discovered.")
        default_config = EmulatorBundleConfiguration()  # Use default environ values
        for emulator_id in emulator_ids:
            port = int(emulator_id.strip().rsplit('-', maxsplit=1)[-1])
            em = Emulator(emulator_id)
            if max_lease_time is not None:
                leased_emulator: Emulator = AsyncEmulatorPool.LeasedEmulator(em)  # type: ignore
                leased_emulator.set_timer(expiry=max_lease_time)  # type: ignore
                await queue.put(leased_emulator)
            else:
                await queue.put(em)
        return AsyncEmulatorPool(queue)
