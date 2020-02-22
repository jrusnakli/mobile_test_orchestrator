import sys
import time

from contextlib import suppress

import asyncio
import os
import subprocess

from dataclasses import dataclass
from multiprocessing import Queue, Process
from pathlib import Path
from typing import Optional, List, Dict, Any, Set

from androidtestorchestrator.device import Device


__all__ = ["EmulatorBundleConfiguration", "Emulator"]


log = logging.getLogger(__name__)
log.setLevel(logging.WARNING)


_ANDROID_AVD_HOME = os.environ.get("ANDROID_AVD_HOME")
_ANDROID_EMULATOR_HOME = os.environ.get("ANDROID_EMULATOR_HOME")
_ANDROID_SDK_ROOT = os.environ.get("ANDROID_SDK_ROOT")
_ANDROID_RESOLVED_AVD_HOME = _ANDROID_AVD_HOME if _ANDROID_AVD_HOME else \
    os.path.join(_ANDROID_EMULATOR_HOME, "avd") if _ANDROID_EMULATOR_HOME else None


@dataclass
class EmulatorBundleConfiguration:
    """Path to SDK (must contain platform-tools and emulator dirs)"""
    sdk: Path
    """Location of AVDs, or None for default"""
    avd_dir: Optional[Path] = None
    """Location of system image or None for default"""
    system_img: Optional[str] = None
    """Location of kernal to use or None for default"""
    kernel: Optional[str] = None
    """location of RAM disk or None for default"""
    ramdisk: Optional[str] = None
    """which working directory to ro run from (or None to use cwd)"""
    working_dir: Optional[Path] = Path(os.getcwd())
    """timeout if boot does not happen after this many seconds"""
    boot_timeout: int = 5*60

    def adb_path(self) -> Path:
        return self.sdk.joinpath("platform-tools").joinpath("adb")

    def emulator_path(self) -> Path:
        return self.sdk.joinpath("emulator").joinpath("emulator")


class Emulator(Device):
    """
    Subclass of Device that is specifically an emulator

    :param port: which port the emulator is started on
    :param config: config under which emulator was launched
    :param launch_cmd: command used to launch the emulator (for attempting restarts if necessary)
    :param env: copy of os.environ plus any user defined modifications, used at time emlator was launched
    """

    """Allowed ports for Android emulators"""
    PORTS = list(range(5554, 5585, 2))

    class FailedBootError(Exception):
        """
        Raised when an emulator fails to boot
        """

        def __init__(self, port: int, stdout: str):
            super().__init__(f"Failed to start emulator on port {port}:\n{stdout}")
            self._port = port

        @property
        def port(self) -> int:
            return self._port

    def __init__(self,
                 port: int,
                 config: EmulatorBundleConfiguration,
                 launch_cmd: Optional[List[str]] = None,
                 env: Optional[Dict[str, str]] = None):
        super().__init__(f"emulator-{port}", str(config.adb_path()))
        self._launch_cmd = launch_cmd
        self._env = env
        self._config = config
        self._port = port

    @property
    def port(self) -> int:
        """
        :return: port associated with this `Emulator`
        """
        return self._port

    def restart(self) -> None:
        """
        Restart this `Emulator` and make it available for use again
        """
        if self._launch_cmd is None:
            raise Exception("This emulator was started externally; cannot restart")
        subprocess.Popen(self._launch_cmd,
                         stderr=subprocess.STDOUT,
                         stdout=subprocess.PIPE,
                         env=self._env)
        booted = False
        seconds = 0
        # wait to come online
        while self.get_state() != Device.State.ONLINE:
            time.sleep(1)
            seconds += 1
            if seconds > self._config.boot_timeout:
                raise TimeoutError("Timeout waiting for emulator to come online")
        # wait for coplete boot once online
        while not booted:
            booted = self.get_system_property("sys.boot_completed") == "1"
            time.sleep(1)
            seconds += 1
            if seconds > self._config.boot_timeout:
                raise TimeoutError("Timeout waiting for emulator to boot")

    async def restart_async(self) -> None:
        """
        Restart this emulator and make it available for use again
        """
        if self._launch_cmd is None:
            raise Exception("This emulator was started externally; cannot restart")

        async def wait_for_boot() -> None:
            proc = await asyncio.subprocess.create_subprocess_shell(" ".join(self._launch_cmd),
                                                                    stderr=subprocess.STDOUT,
                                                                    stdout=subprocess.PIPE,
                                                                    env=self._env)
            booted = False
            # wait to come online
            while self.get_state() != Device.State.ONLINE:
                time.sleep(1)

            # wait for complete boot once online
            while not booted:
                booted = self.get_system_property("sys.boot_completed") == "1"
                await asyncio.sleep(1)

        await asyncio.wait_for(wait_for_boot(), self._config.boot_timeout)

    @classmethod
    async def launch(cls, port: int, avd: str, config: EmulatorBundleConfiguration, *args: str,
                     retries: int = 0) -> "Emulator":
        """
        Launch an emulator on the given port, with named avd and configuration

        :param port: port on which emulator should be launched
        :param avd: which avd
        :param config: configuration for launching emulator
        :param args:  add'l arguments to pass to emulator command
        :param retries: allowed number of retries to launch emulator before raising EmulatorFailedBoot exception
        :returns: the newly launched emulator, fully booted
        """
        if port not in cls.PORTS:
            raise ValueError(f"Port must be one of {cls.PORTS}")
        device_id = f"emulator-{port}"
        device = Device(device_id, str(config.adb_path()))
        with suppress(Exception):
            device.execute_remote_cmd("emu", "kill")  # attempt to kill any existing emulator at this port
            await asyncio.sleep(2)
        emulator_cmd = Path(config.sdk).joinpath("emulator").joinpath("emulator")
        if not emulator_cmd.is_file():
            raise FileNotFoundError(f"Could not find emulator cmd to launch emulator @ {emulator_cmd}")
        if not config.adb_path().is_file():
            raise FileNotFoundError(f"Could not find adb cmd @ {config.adb_path()}")
        cmd = [str(emulator_cmd), "-avd", avd, "-port", str(port), "-read-only"]
        if sys.platform.lower() == 'win32':
            cmd[0] += ".bat"
        if config.system_img:
            cmd += ["-system", str(config.system_img)]
        if config.kernel:
            cmd += ["-kernel", str(config.kernel)]
        if config.ramdisk:
            cmd += ["-ramdisk", str(config.ramdisk)]
        cmd += args
        environ = dict(os.environ)
        if config.avd_dir:
            environ["ANDROID_AVD_HOME"] = str(config.avd_dir)
            assert os.path.exists(os.path.join(environ["ANDROID_AVD_HOME"], "MTO_emulator2.ini"))
            assert os.path.exists(os.path.join(environ["ANDROID_AVD_HOME"], "MTO_emulator2.avd"))
        environ["ANDROID_SDK_HOME"] = str(config.sdk)
        booted = False
        proc = await asyncio.subprocess.create_subprocess_shell(" ".join(cmd),
                                                                stderr=subprocess.STDOUT,
                                                                stdout=subprocess.PIPE,
                                                                env=environ)
        try:
            async def wait_for_boot() -> None:
                nonlocal booted
                nonlocal proc
                nonlocal device_id

                while proc.poll() is None and device.get_state() != Device.State.ONLINE:
                    print(f">>> STATE IS {device.get_state()}")
                    await asyncio.sleep(1)
                if proc.poll() is not None:
                    stdout, _ = proc.communicate()
                    raise Emulator.FailedBootError(port, stdout.decode('utf-8'))
                start = time.time()
                while not booted:
                    booted = device.get_system_property("sys.boot_completed", verbose=False) == "1"
                    await asyncio.sleep(1)
                    duration = time.time() - start
                    print(f">>> {device.device_id} [{duration}] Booted?: {booted}")

            await asyncio.wait_for(wait_for_boot(), config.boot_timeout)
            return cls(port, config=config, launch_cmd=cmd, env=environ)
        except Exception as e:
            raise Emulator.FailedBootError(port, str(e)) from e
        finally:
            if not booted:
                with suppress(Exception):
                    proc.kill()

    def kill(self) -> None:
        """
        Kill this emulator (underlying Process)
        """
        log.info(f">>>>> Killing emulator {self.device_id}")
        self.execute_remote_cmd("emu", "kill")


class AsyncEmulatorQueue(AsyncDeviceQueue):
    """
    A class used to by clients wishing to be served emulators.  Clients reserve and emulator, and when
    finished, relinquish it back into the queue.

    It is recommended to use one of the class factory methods ("create" or "discover") to create an
    emulator queue.
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
