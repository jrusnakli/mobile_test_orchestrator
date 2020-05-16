"""
The package *devicepool" contains *DevicePool* classes for reserving `Device`'s and `Emulator`'s from a queue.
The interface for setting and expiration on reserved Device's/Emulator's is also provided through these classes.
"""
import asyncio
import multiprocessing
import queue
import subprocess
from abc import ABC, abstractmethod
from asyncio import Queue
from contextlib import asynccontextmanager, suppress
from typing import AsyncGenerator, Callable, Optional, Dict, Union, Set, Coroutine, Any, List, TypeVar, Generic

from androidtestorchestrator.device import Device
from androidtestorchestrator.emulators import Emulator, EmulatorBundleConfiguration, log

It = TypeVar('It')


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

    :param queue: underlying non-async queue to draw from/push to
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


AsyncQ = TypeVar('AsyncQ', AbstractAsyncQueue[Any], asyncio.Queue[Any])


class BaseDevicePool(ABC):
    """
    Abstract base class for all device queues.
    """
    def __init__(self, queue: AsyncQ):
        """
        :param queue: queue to server Device's from.
        """
        self._q: AsyncQ = queue  # type: ignore

    @staticmethod
    def _list_devices(filt: Callable[[str], bool]) -> List[str]:
        cmd = [Device.adb_path(), "devices"]
        completed = subprocess.run(" ".join(cmd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True)
        device_ids = []
        for line in completed.stdout.decode('utf-8').splitlines():
            if line.strip().endswith("device"):
                device_id = line.split()[0]
                if filt(device_id):
                    device_ids.append(device_id)
        return device_ids

    @staticmethod
    async def discover(filt: Callable[[str], bool] = lambda x: True) -> "BaseDevicePool":
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
        return BaseDevicePool(q)


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
    async def reserve(self) -> AsyncGenerator[Device, None]:
        """
        :return: a reserved Device
        """
        device = await self._q.get()  # type: ignore
        try:
            yield device
        finally:
            if hasattr(device, "reset_lease"):
                device.reset_lease()
            await self._q.put(device)  # type: ignore

    @asynccontextmanager
    async def reserve_many(self, count: int) -> AsyncGenerator[List[Device], None]:
        """
        :param count: how many to reserve
        :return: a reserved Device
        """
        devices = [await self._q.get() for _ in range(count)]  # type: ignore
        try:
            yield devices
        finally:
            for device in devices:
                if hasattr(device, "reset_lease"):
                    device.reset_lease()
                await self._q.put(device)  # type: ignore

    @staticmethod
    def from_external(queue: multiprocessing.Queue["Device"]) -> "AsyncDevicePool":
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

    def __init__(self, queue: Queue[Emulator], max_lease_time: Optional[int] = None):
        super().__init__(queue)
        self._max_lease_time = max_lease_time

    async def _launch(self, count: int, avd: str, config: EmulatorBundleConfiguration, *args: str) -> AsyncGenerator[Emulator, None]:
        """
        Launch given number of emulators and populate provided queue

        :param count: number of emulators to launch
        :param avd: which avd
        :param config: configuration information for launching emulator
        :param args: additional user args to launch command
        """
        async def launch_next(index: int, port: int) -> Emulator:
            await asyncio.sleep(index * 3)  # space out launches as this can help with avoiding instability
            if self._max_lease_time:
                leased_emulator = await self.LeasedEmulator.launch(port, avd, config, *args)
                leased_emulator.set_timer(expiry=self._max_lease_time)  # type: ignore
            else:
                leased_emulator = await Emulator.launch(port, avd, config, *args)
            return leased_emulator

        ports = Emulator.PORTS[:count]
        failed_port_counts: Dict[int, int] = {}  # port to # of times failed to launch
        emulator_launches: Union[Set[asyncio.Future[Emulator]], Set[Coroutine[Any, Any, Any]]] = set(
            launch_next(index, port) for index, port in enumerate(ports)
        )
        pending = emulator_launches
        emulators: List[Emulator] = []
        while pending or failed_port_counts:
            completed, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for emulator_task in completed:
                result = emulator_task.result()
                if isinstance(result, Emulator):
                    emulator = result
                    emulators.append(emulator)
                    yield emulator
                    failed_port_counts.pop(emulator.port, None)
                elif isinstance(result, Emulator.FailedBootError):
                    exc = result
                    failed_port_counts.setdefault(exc.port, 0)
                    failed_port_counts[exc.port] += 1
                    if failed_port_counts[exc.port] >= AsyncEmulatorPool.MAX_BOOT_RETRIES:
                        log.error(f"Failed to launch emulator on port {exc.port} after " +
                                  f"{AsyncEmulatorPool.MAX_BOOT_RETRIES} attempts")
                else:
                    exc = result
                    for em in emulators:
                        with suppress(Exception):
                            em.kill()
                    log.exception("Unknown exception booting emulator. Aborting: %s", str(exc))

        if len(failed_port_counts) == len(ports):
            raise Exception(">>>>> Failed to boot any emulator! Aborting")

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
        emulator_q = cls(queue, max_lease_time=max_lease_time)

        async def populate_q() -> None:
            async for emulator in emulator_q._launch(count, avd, config, *args):
                emulators.append(emulator)
                await queue.put(emulator)

        task = asyncio.create_task(populate_q())
        if wait_for_startup:
            await task
        try:
            yield emulator_q
        finally:
            if not task.done():
                with suppress(Exception):
                    task.cancel()
            for em in emulators:
                with suppress(Exception):
                    em.kill()

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
            em = Emulator(port, config=default_config)
            leased_emulator: Emulator = AsyncEmulatorPool.LeasedEmulator(em)  # type: ignore
            await queue.put(leased_emulator)
            if max_lease_time is not None:
                leased_emulator.set_timer(expiry=max_lease_time)  # type: ignore
        return AsyncEmulatorPool(queue)