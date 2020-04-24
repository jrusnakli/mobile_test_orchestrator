import asyncio
import logging
import os
import subprocess
import sys
import time

from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Dict

from .device import Device

__all__ = ["EmulatorBundleConfiguration", "Emulator"]


log = logging.getLogger(__name__)
log.setLevel(logging.WARNING)


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
    """which working directory to ro run from (or None to use cwd)"""
    working_dir: Optional[Path] = None
    """timeout if boot does not happen after this many seconds"""
    boot_timeout: int = 5*60

    def adb_path(self) -> Path:
        return self.sdk.joinpath("platform-tools").joinpath("adb")


class Emulator(Device):
    """
    Class of Device that is specifically an emulator
    """

    PORTS = list(range(5554, 5585, 2))

    class FailedBootError(Exception):

        def __init__(self, port: int, stdout: str):
            super().__init__(f"Failed to start emulator on port {port}:\n{stdout}")
            self._port = port

        @property
        def port(self):
            return self._port
        
    def __init__(self,
                 device_id: str,
                 port: int,
                 config: EmulatorBundleConfiguration,
                 launch_cmd: Optional[List[str]] = None,
                 env: Optional[Dict[str, str]] = None):
        """
        Launch an emulator and create this Device instance

        :param device_id: str id of the device
        :param port: port of the emulator
        :param config: config under which emulator was launched
        :param launch_cmd: command used to launch the emulator (for attempting restarts if necessary)
        :param env: copy of os.environ plus any user defined modifications, used at time emlator was launched
        """
        super().__init__(device_id, str(config.adb_path()))
        self._launch_cmd = launch_cmd
        self._env = env
        self._config = config
        self._port = port

    @property
    def port(self) -> int:
        return self._port

    def restart(self) -> None:
        """
        Restart this emulator and make it available for use again
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
            subprocess.Popen(self._launch_cmd,
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
    async def launch(cls, port: int, avd: str, config: EmulatorBundleConfiguration, *args: str) -> "Emulator":
        """
        Launch an emulator on the given port, with named avd and configuration

        :param port: port on which emulator should be launched
        :param avd: which avd
        :param config: configuration for launching emulator
        :param args:  add'l arguments to pass to emulator command
        """
        if port not in cls.PORTS:
            raise ValueError(f"Port must be one of {cls.PORTS}")
        device_id = f"emulator-{port}"
        device = Device(device_id, str(config.adb_path()))
        with suppress(Exception):
            device.execute_remote_cmd("emu", "kill")  # attempt to kill any existing emulator at this port
            await asyncio.sleep(2)
        emulator_cmd = config.sdk.joinpath("emulator").joinpath("emulator")
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

                while device.get_state() != Device.State.ONLINE:
                    await asyncio.sleep(1)
                if proc.poll() is not None:
                    stdout, _ = proc.communicate()
                    raise Emulator.FailedBootError(port, stdout.decode('utf-8'))
                start = time.time()
                while not booted:
                    booted = device.get_system_property("sys.boot_completed", ) == "1"
                    await asyncio.sleep(1)
                    duration = time.time() - start
                    print(f">>> [{duration}]  {device.device_id} Booted?: {booted}")

            await asyncio.wait_for(wait_for_boot(), config.boot_timeout)
            return cls(device_id, port=port, config=config, launch_cmd=cmd, env=environ)
        except Exception as e:
            raise Emulator.FailedBootError(port, str(e)) from e
        finally:
            if not booted:
                with suppress(Exception):
                    proc.kill()

    def kill(self) -> None:
        """
        Kill this emulator
        """
        log.info(f">>>>> Killing emulator {self.device_id}")
        self.execute_remote_cmd("emu", "kill")


