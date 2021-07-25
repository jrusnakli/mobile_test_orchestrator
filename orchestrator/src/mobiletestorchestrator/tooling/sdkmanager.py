"""
This package contains the elements used to bootstrap the Android SDK's components
"""
import glob
import logging
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Optional

import mobiletestorchestrator

try:
    from importlib.resources import files
except:
    # noinspection PyUnresolvedReferences
    from importlib_resources import files  # type: ignore


log = logging.getLogger(str(Path(__file__).stem))
log.setLevel(logging.ERROR)


class SdkManager:
    """
    SDK Manager interface for installing components of the Android SDK

    :param sdk_dir: Path to where the sdk either exists or is to be bootstrapped (if starting fresh)
    :param bootstrap: If True, bootstrap the sdk manager and avd manager from internal resources
    """

    PROTOCOL_PREFIX = "sdkmanager"

    def __init__(self, sdk_dir: Path, bootstrap: bool = False):
        self._sdk_dir = sdk_dir
        self._env = dict(os.environ)
        self._env.update({'ANDROID_SDK_ROOT': str(self._sdk_dir)})
        self._sdk_manager_path = sdk_dir.joinpath("tools", "bin", "sdkmanager")
        self._avd_manager_path = sdk_dir.joinpath("tools", "bin", "avdmanager")
        if sys.platform.lower() == 'win32':
            self._sdk_manager_path = self._sdk_manager_path.with_suffix(".bat")
            self._avd_manager_path = self._avd_manager_path.with_suffix(".bat")
            self._shell = True
        else:
            self._shell = False
        if bootstrap is True and not self._sdk_manager_path.exists():
            bootstrap_zip = files(mobiletestorchestrator).joinpath(os.path.join("resources", "sdkmanager",
                                                                                "bootstrap.zip"))
            with zipfile.ZipFile(bootstrap_zip) as zfile:
                zfile.extractall(path=self._sdk_dir)
                if self._sdk_dir.joinpath("android_sdk_bootstrap").exists():
                    for file in glob.glob(str(self._sdk_dir.joinpath("android_sdk_bootstrap", "*"))):
                        basename = os.path.basename(file)
                        shutil.move(file, str(self._sdk_dir.joinpath(basename)))
        if not self._sdk_manager_path.exists():
            raise FileNotFoundError(f"Did not locate sdkmanager tool at expected location {self._sdk_manager_path}")
        if not self._avd_manager_path.exists():
            raise FileNotFoundError(f"Did not locate sdkmanager tool at expected location {self._avd_manager_path}")
        os.chmod(str(self._sdk_manager_path), stat.S_IRWXU)
        os.chmod(str(self._avd_manager_path), stat.S_IRWXU)

    @property
    def emulator_path(self) -> Path:
        return self._sdk_dir.joinpath("emulator", "emulator")

    @property
    def adb_path(self) -> Path:
        return self._sdk_dir.joinpath("platform-tools", "adb")

    def bootstrap(self, application: str, version: Optional[str] = None) -> None:
        application = f"{application};{version}" if version else f"{application}"
        if not os.path.exists(self._sdk_manager_path):
            raise SystemError("Failed to properly install sdk manager for bootstrapping")
        log.debug(f"Downloading to {self._sdk_dir}\n  {self._sdk_manager_path} {application}")
        completed = subprocess.Popen([self._sdk_manager_path, application], stdout=subprocess.DEVNULL, bufsize=0,
                                     stderr=subprocess.PIPE, stdin=subprocess.PIPE,
                                     shell=self._shell)
        assert completed.stdin is not None  # make mypy happy
        for _ in range(10):
            completed.stdin.write(b'y\n')
        stdout, stderr = completed.communicate()
        if completed.returncode != 0:
            raise Exception(
                f"Failed to download/update {application}: {stderr.decode('utf-8')}")

    def bootstrap_platform_tools(self) -> None:
        """
        download/update platform tools within the sdk
        """
        self.bootstrap("platform-tools")

    def bootstrap_emulator(self) -> None:
        """
        download/update emulator within the sdk
        """
        self.bootstrap("emulator")

    def download_system_img(self, version: str) -> None:
        """
        download/update system image with version
        :param version: version to download
        """
        self.bootstrap("system-images", version)

    def create_avd(self, avd_dir: Path, avd_name: str, image: str, device_type: str, *args: str) -> None:
        """
        Create an android emulator definition

        :param avd_dir: Where to create the files
        :param avd_name: name to give to emulator definition
        :param image: which system image to use
        :param device_type: device type (as per 'avd_manager list')
        :param args: additional args to pass on create
        """
        log.debug(f">>>> Downloading system image ...{image}")
        self.download_system_img(image)
        create_avd_cmd = [str(self._avd_manager_path), "create", "avd", "-n", avd_name, "-k", f"system-images;{image}",
                          "-d", device_type]
        create_avd_cmd += args
        self._env.update({"ANDROID_AVD_HOME": str(avd_dir)})
        p = subprocess.Popen(create_avd_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.PIPE, shell=self._shell, env=self._env)
        if p.wait() != 0:
            stdout, stderr = p.communicate()
            raise Exception(f"Failed to create avd: {stdout.decode('utf-8')}\n{stderr.decode('utf-8')}")
