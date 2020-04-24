import asyncio
import os
import subprocess
from abc import ABC
from asyncio import Queue
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import AsyncGenerator, Callable, Optional, Dict, Union, Set, Coroutine, Any, List

from androidtestorchestrator.device import Device
from androidtestorchestrator.emulators import Emulator, EmulatorBundleConfiguration, log


class BaseDeviceQueue(ABC):
    """
    Abstract base class for all device queues.
    """

    def __init__(self, queue: Queue):
        """
        :param queue: queue to server Device's from.
        """
        self._q = queue

    def empty(self) -> bool:
        return self._q.empty()

    @staticmethod
    def _list_devices(filt: Callable[[str], bool]):
        cmd = [Device.adb_path(), "devices"]
        completed = subprocess.run(" ".join(cmd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True)
        device_ids = []
        for line in completed.stdout.splitlines():
            line = line.decode('utf-8').strip()
            if line.strip().endswith("device"):
                device_id = line.split()[0]
                if filt(device_id):
                    device_ids.append(device_id)
        return device_ids

    @classmethod
    async def discover(cls, filt: Callable[[str], bool] = lambda x: True) -> "BaseDeviceQueue":
        """
        Discover all online devices and create a DeviceQueue with them

        :param filt: only include devices filtered by device id through this given filter, if provided

        :return: Created DeviceQueue instance containing all online devices
        """
        queue = Queue(20)
        device_ids = cls._list_devices(filt)
        if not device_ids:
            raise Exception("No device were discovered based on any filter critera. " +
                            f"output of 'adb devices' was: {completed.stdout}")
        for device_id in device_ids:
            await queue.put(Device(device_id))
        return cls(queue)


class AsyncDeviceQueue(BaseDeviceQueue):
    """
    Class providing an async interface for a general device queue (aka, agnostic to
    whether devices are Emulator's or Device's).
    """

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
        device = await self._q.get()
        try:
            yield device
        finally:
            await self._q.put(device)


class AsyncEmulatorQueue(AsyncDeviceQueue):
    """
    A class used to by clients wishing to be served emulators.  Clients reserve and emulator, which when
    finished, relinquish it back into the queue.  Emulators can be "leased", in which case the class instance
    of the device will be disabled (the API not the device istelf) after the lease expires, at which point
    the device will be placed back into the queue and available to other clients.

    It is recommended to use one of the class factory methods ("create" or "discover") to create an
    emulator queue instance.
    """

    MAX_BOOT_RETRIES = 2

    class LeaseExpired(Exception):

        def __init__(self, device: Device):
            super().__init__(f"Lease expired for {device.device_id}")

    class LeasedEmulator(Emulator):

        def __init__(self, device_id: str, config: EmulatorBundleConfiguration):
            port = int(device_id.split("emulator-")[1])
            # must come first to avoid issues with __getattribute__ override
            self._timed_out = False
            super().__init__(device_id, port=port, config=config)
            self._task: asyncio.Task = None

        async def set_timer(self, expiry: int):
            """
            set lease expiration

            :param expiry: number of seconds until expiration of lease (from now)
            """
            if self._task is not None:
                raise Exception("Renewal of already existing lease is not allowed")

            async def timeout():
                await asyncio.sleep(expiry)
                self._timed_out = True

            self._task = asyncio.create_task(timeout())

        def __getattribute__(self, item: str):
            # Playing with fire a little here -- make sure you know what you are doing if you update this method
            if item == '_device_id' or item == 'device_id':
                # always allow this one to go through (one is a property reflecting the other)
                return object.__getattribute__(self, item)
            if object.__getattribute__(self, "_timed_out"):
                raise AsyncEmulatorQueue.LeaseExpired(self)
            return object.__getattribute__(self, item)

    def __init__(self, queue: Queue, max_lease_time: Optional[int] = None):
        """
        :param queue: queue used to serve emulators
        :param max_lease_time: optional maximum amount of time client can hold a "lease" on this emulator, with
           any attempts to execute commands against the device raising a "LeaseExpired" exception after this time.
           NOTE: this only applies when create method is used to create the queue.
        """
        super().__init__(queue)
        self._max_lease_time = max_lease_time

    async def _launch(self, count: int, avd: str, config: EmulatorBundleConfiguration, *args: str):
        """
        Launch given number of emulators and populate provided queue

        :param count: number of emulators to launch
        :param avd: which avd
        :param config: configuration information for launching emulator
        :param args: additional user args to launch command

        """
        async def launch_next(index: int, port: int) -> Emulator:
            await asyncio.sleep(index * 3)  # space out launches as this can help with avoiding instability
            leased_emulator = await self.LeasedEmulator.launch(port, avd, config, *args)
            if self._max_lease_time:
                leased_emulator.set_timer(expiry=self._max_lease_time)
            return leased_emulator

        ports = Emulator.PORTS[:count]
        failed_port_counts: Dict[int, int] = {}  # port to # of times failed to launch
        emulator_launches: Union[Set[asyncio.Future], Set[Coroutine[Any, Any, Any]]] = set(
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
                    if failed_port_counts[exc.port] >= AsyncEmulatorQueue.MAX_BOOT_RETRIES:
                        log.error(f"Failed to launch emulator on port {exc.port} after " +
                                  f"{AsyncEmulatorQueue.MAX_BOOT_RETRIES} attempts")
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
                     max_lease_time: Optional[int] = None,
                     wait_for_startup: Optional[int] = None) -> "AsyncEmulatorQueue":
        """
        Create an emulator queue by lanuching them explicitly.  Returns quickly unless specified otherwise,
        launching the emulators in the background

        :param count: how many emulators in queue
        :param avd: name of avd to launch
        :param config: emulator bundle config
        :param args: additional arguments to pass to the emulator launch command
        :param max_lease_time: see constructor
        :param wait_for_startup: if positive non-zero, wait at most this many seconds for emulators to be started
            before returning,

        :return: new EmulatorQueue populated with requested emulators
        :raises: TimeoutError if timeout specified and not started in time
        """
        if count > len(Emulator.PORTS):
            raise Exception(f"Can have at most {count} emulators at one time")
        queue = Queue(count)
        emulators: List[Emulator] = []
        emulator_q = cls(queue, max_lease_time=max_lease_time)

        async def populate_q():
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

    @classmethod
    async def discover(cls, max_lease_time: Optional[int] = None,
                       config: Optional[EmulatorBundleConfiguration] = None) -> "AsyncEmulatorQueue":
        """
        Discover all online devices and create a DeviceQueue with them

        :param max_lease_time: see constructor
        :param config: Definition of emulator configuration (for access to root sdk), or None to use env vars

        :return: Created DeviceQueue instance containing all online devices
        """
        queue = Queue(20)
        emulator_ids = cls._list_devices(filt=lambda x: x.startswith('emulator-'))
        if not emulator_ids:
            raise Exception("No emulators discovered.")
        avd_home = os.environ.get("ANDROID_AVD_HOME")
        default_config = EmulatorBundleConfiguration(avd_dir=Path(avd_home) if avd_home else None,
                                                     sdk=Path(os.environ.get("ANDROID_SDK_ROOT")))
        for emulator_id in emulator_ids:
            leased_emulator = cls.LeasedEmulator(emulator_id, config=config or default_config)
            await queue.put(leased_emulator)
            if max_lease_time is not None:
                leased_emulator.set_timer(expiry=max_lease_time)
        return cls(queue)
