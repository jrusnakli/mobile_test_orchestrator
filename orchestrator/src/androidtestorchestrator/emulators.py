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


_ANDROID_AVD_HOME = os.environ.get("ANDROID_AVD_HOME")
_ANDROID_EMULATOR_HOME = os.environ.get("ANDROID_EMULATOR_HOME")
_ANDROID_SDK_ROOT = os.environ.get("ANDROID_SDK_ROOT")
_ANDROID_RESOLVED_AVD_HOME = _ANDROID_AVD_HOME if _ANDROID_AVD_HOME else \
    os.path.join(_ANDROID_EMULATOR_HOME, "avd") if _ANDROID_EMULATOR_HOME else None


@dataclass
class EmulatorBundleConfiguration:
    """Path to SDK (must contain platform-tools and emulator dirs)"""
    sdk: Path = Path(_ANDROID_SDK_ROOT) if _ANDROID_SDK_ROOT else None  # type: ignore
    """Location of AVDs, or None for default"""
    avd_dir: Optional[Path] = Path(_ANDROID_RESOLVED_AVD_HOME) if _ANDROID_RESOLVED_AVD_HOME else None
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
        environ["ANDROID_SDK_HOME"] = str(config.sdk)
        booted = False
        proc: Optional[subprocess.Popen] = None  # type: ignore
        try:
            while retries >= 0:
                print(f">>>>>> Launching emulator with {' '.join(cmd)}")
                proc = subprocess.Popen(cmd,
                                        stderr=subprocess.STDOUT,
                                        stdout=subprocess.PIPE,
                                        bufsize=0,
                                        encoding='utf-8',
                                        env=environ)

                async def wait_for_boot(proc: subprocess.Popen) -> None:  # type: ignore
                    nonlocal booted
                    nonlocal device_id
                    nonlocal retries
                    while proc.poll() is None and device.get_state() != Device.State.ONLINE:
                        await asyncio.sleep(1)

                    if proc.poll() is not None:
                        stdout, _ = proc.communicate()
                        if retries:
                            retries -= 1
                        else:
                            raise Emulator.FailedBootError(port, stdout)
                    start = time.time()
                    while not booted:
                        boot_prop = device.get_system_property("dev.bootcomplete", verbose=False)
                        booted = boot_prop == "1"
                        await asyncio.sleep(5)
                        duration = time.time() - start
                        print(f">>> [{duration}]  {device.device_id} Booted?: {booted} [{boot_prop}]")
                        if proc.poll() is not None:
                            stdout, _ = proc.communicate()
                            if retries:
                                retries -= 1
                            else:
                                raise Emulator.FailedBootError(port, stdout)

                await asyncio.wait_for(wait_for_boot(proc), config.boot_timeout)
                return cls(port, config=config, launch_cmd=cmd, env=environ)
            raise Emulator.FailedBootError(port, "Failed to launch emulator; unknown cause")
        finally:
            if not booted and proc is not None:
                with suppress(Exception):
                    proc.kill()

    def kill(self) -> None:
        """
        Kill this emulator
        """
        log.info(f">>>>> Killing emulator {self.device_id}")
        self.execute_remote_cmd("emu", "kill")
