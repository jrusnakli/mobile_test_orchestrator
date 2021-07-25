import sys
import time

from contextlib import suppress

import asyncio
import logging
import os
import subprocess

from dataclasses import dataclass
from multiprocessing import Queue, Process
from pathlib import Path
from typing import Optional, List, Dict, Any, Set, Union, Coroutine, Tuple, Mapping

from mobiletestorchestrator.device import Device


__all__ = ["EmulatorBundleConfiguration", "Emulator"]


log = logging.getLogger(str(Path(__file__).stem))


@dataclass
class EmulatorBundleConfiguration:
    """Path to SDK (must contain platform-tools and emulator dirs)"""
    sdk: Path
    """Location of AVDs, or None for default"""
    avd_dir: Optional[Path] = None
    """Location of system image or None for default"""
    system_img: Optional[Path] = None
    """Location of kernal to use or None for default"""
    kernel: Optional[Path] = None
    """location of RAM disk or None for default"""
    ramdisk: Optional[Path] = None
    """which working directory to this before startup (or None to use cwd)"""
    working_dir: Optional[Path] = None
    """timeout if boot does not happen after this many seconds"""
    boot_timeout: int = 5*60

    def adb_path(self) -> Path:
        return self.sdk.joinpath("platform-tools").joinpath("adb")

    def launch_cmd(self, avd: str, port: int, args: Optional[List[str]] = None) -> List[str]:
        emulator_cmd = self.sdk.joinpath("emulator").joinpath("emulator")
        if not emulator_cmd.is_file():
            raise Exception(f"Could not find emulator cmd to launch emulator @ {emulator_cmd}")
        if not self.adb_path().is_file():
            raise Exception(f"Could not find adb cmd @ {self.adb_path()}")
        cmd = [str(emulator_cmd), "-avd", avd, "-port", str(port), "-read-only"]
        if sys.platform.lower() == 'win32':
            cmd[0] += ".bat"
        if self.system_img:
            cmd += ["-system", str(self.system_img)]
        if self.kernel:
            cmd += ["-kernel", str(self.kernel)]
        if self.ramdisk:
            cmd += ["-ramdisk", str(self.ramdisk)]
        if args:
            cmd += args
        return cmd


class Emulator(Device):

    PORTS = list(range(5554, 5585, 2))
    _launches: Dict[str, Tuple[str, int, EmulatorBundleConfiguration, List[str], Mapping[str, str]]] = {}

    class FailedBootError(Exception):

        def __init__(self, port: int, stdout: str):
            super().__init__(f"Failed to start emulator on port {port}:\n{stdout}")
            self._port = port

        @property
        def port(self) -> int:
            return self._port

    def is_alive(self) -> bool:
        return self.get_state(False) == Device.State.ONLINE

    async def restart(self) -> None:
        """
        Restart this emulator and make it available for use again
        """
        if self._device_id not in Emulator._launches:
            raise ValueError(f"Given device {self._device_id} was launched externally; "
                             "only emulators launched through 'Emulator.launch' can be relaunched ")
        avd, port, config,  args, env = Emulator._launches[self._device_id]

        async def wait_for_boot() -> None:
            subprocess.Popen(config.launch_cmd(port=port, avd=avd, args=args),
                             stderr=subprocess.STDOUT,
                             stdout=subprocess.PIPE,
                             env=env)
            booted = False
            while await self.get_state(False) != Device.State.ONLINE:
                await asyncio.sleep(1)

            while not booted:
                booted = self.get_system_property("sys.boot_completed") == "1"
                await asyncio.sleep(1)

        await asyncio.wait_for(wait_for_boot(), config.boot_timeout)

    @classmethod
    async def launch(cls, port: int, avd: str, config: EmulatorBundleConfiguration, *args: str) -> "Emulator":
        """
        Launch an emulator on the given port, with named avd and configuration

        :param port: which port to launch on
        :param avd: name of emulator AVD
        :param config: configuration of emulator
        :param args:  add'l arguments to pass to emulator command
        """
        if port not in cls.PORTS:
            raise ValueError(f"Port must be one of {cls.PORTS}")
        device_id = f"emulator-{port}"
        device = Device(device_id)
        with suppress(Exception):
            device.execute_remote_cmd("emu", "kill")  # attempt to kill any existing emulator at this port
            await asyncio.sleep(2)
        emulator_cmd = config.sdk.joinpath("emulator").joinpath("emulator")
        if not emulator_cmd.is_file():
            raise Exception(f"Could not find emulator cmd to launch emulator @ {emulator_cmd}")
        if not config.adb_path().is_file():
            raise Exception(f"Could not find adb cmd @ {config.adb_path()}")
        cmd = config.launch_cmd(avd=avd, port=port, args=list(args))
        environ = dict(os.environ)
        environ["ANDROID_AVD_HOME"] = str(config.avd_dir)
        environ["ANDROID_SDK_HOME"] = str(config.sdk)
        booted = False
        proc = subprocess.Popen(cmd,
                                stderr=subprocess.STDOUT,
                                stdout=subprocess.PIPE,
                                env=environ)
        try:

            async def wait_for_boot() -> None:
                nonlocal booted
                nonlocal proc
                nonlocal device_id

                while await device.get_state_async(False) != Device.State.ONLINE:
                    await asyncio.sleep(1)
                if proc.poll() is not None:
                    stdout, _ = proc.communicate()
                    raise Emulator.FailedBootError(port, stdout.decode('utf-8'))
                start = time.time()
                while not booted:
                    booted = device.get_system_property("sys.boot_completed", ) == "1"
                    await asyncio.sleep(1)
                    duration = time.time() - start
                    log.debug(f">>> {device.device_id} [{duration}] Booted?: {booted}")

            await asyncio.wait_for(wait_for_boot(), config.boot_timeout)
            Emulator._launches[device_id] = (avd, port, config, list(args), environ)
            return Emulator(device_id)
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
        log.debug(f">>>>> Killing emulator {self.device_id}")
        self.execute_remote_cmd("emu", "kill")


class EmulatorQueue:

    def __init__(self, count: int):
        """
        :param count: how many emulators to launch and put in the queue
        """
        if count > len(Emulator.PORTS):
            raise Exception(f"Can have at most {count} emulators at one time")
        self._count = count
        self._q: Queue["Emulator"] = Queue(count)
        self._restart_q: Queue[Optional["Emulator"]] = Queue()
        self._process: Optional[Process] = None

    async def start_async(self, avd: str, config: EmulatorBundleConfiguration, *args: str) -> None:
        """
        Aynchronous start of an emulator

        :param avd: name of avd to launch
        :param config: emulator bundle config
        :param args: additional arguments to pass to the emulator launch command
        """
        emulators = []

        async def launch_next(index: int, *args: Any, **kargs: Any) -> Emulator:
            await asyncio.sleep(index*3)  # space out launches as this can help with avoiding instability
            return await Emulator.launch(*args, **kargs)

        async def launch(count: int) -> int:
            emulator_launches: Union[Set[asyncio.Future[Emulator]],
                                     Set[Coroutine[Any, Any, Any]]] = set(
                launch_next(index, port, avd, config, *args) for index, port in enumerate(Emulator.PORTS[:count]))
            failed_count = 0
            pending = emulator_launches
            while pending:
                completed, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for emulator_task in completed:
                    emulator = emulator_task.result()
                    if isinstance(emulator, Emulator):
                        self._q.put(emulator)
                        emulators.append(emulator)
                    elif isinstance(emulator, Emulator.FailedBootError):
                        failed_count += 1
                        exc = emulator
                        raise exc
            return failed_count

        failed = await launch(self._count)
        if failed != 0 and emulators:
            # retry the failed count of emulators
            failed = await launch(failed)
        if failed != 0:
            for em in emulators:
                em.kill()
            raise Exception("Failed to boot all emulators")
        while True:
            emulator: Optional[Emulator] = self._restart_q.get()
            if emulator is not None:
                await emulator.restart()
            else:
                break  # None signal end
        for emulator in emulators:
            emulator.kill()
        self._q.close()
        self._restart_q.close()
        log.debug(">>>> Exiting emulator queue task")

    def __enter__(self) -> "EmulatorQueue":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        with suppress(Exception):
            self.stop()

    def stop(self) -> None:
        """
        Stop the background process monitoring emulators and stop each emulator
        """
        if self._process:
            self._restart_q.put(None)  # signals end
            self._process.join(timeout=10)
        with suppress(Exception):
            self._q.close()
            self._restart_q.close()

    def relinquish(self, emulator: Emulator) -> None:
        """
        Relinquish emulator back to the queue
        :param emulator: emulator to relinquish
        """
        self._q.put(emulator)

    def reserve(self, timeout: Optional[float] = None) -> Emulator:
        """
        reserve an emulator, blocking until the next one is available if necessary

        :param timeout: maximum time to wait, in seconds

        :return: the requested emulator
        """
        emulator: Emulator = self._q.get(timeout=timeout)
        while not emulator.is_alive():
            self._restart_q.put(emulator)
            self._q.get(timeout=timeout)
        return emulator

    @classmethod
    def start(cls, count: int, avd: str, config: EmulatorBundleConfiguration, *args: str) -> "EmulatorQueue":
        """
        Start the given number of emulators with the given avd and bundle configuration.
        Launches emulators in the background and returns quickly.  The retrieve command will
        block on a Queue until the first emulator is booted and available from the background
        process launching the emulators.

        :param count: number of emulators to start
        :param avd: name of avd to start
        :param config: emulator configuration bundle
        :param args: Additional arguments that will be passed when launching each emulator

        :return: the queue for retrieving/relinquishing emulators
        """
        def entry_point(avd: str, config: EmulatorBundleConfiguration, queue: EmulatorQueue) -> None:
            asyncio.get_event_loop().run_until_complete(queue.start_async(avd, config, *args))

        queue = EmulatorQueue(count)
        queue._process = Process(target=entry_point, args=(avd, config, queue))
        queue._process.start()
        return queue
