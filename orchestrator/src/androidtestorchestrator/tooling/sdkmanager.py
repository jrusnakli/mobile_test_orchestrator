"""
This package contains the elements used to bootstrap the Android SDK's components
"""
import glob
import os
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path
from typing import Optional

import androidtestorchestrator
from importlib_resources import files  # type: ignore


class SdkManager:

    PROTOCOL_PREFIX = "sdkmanager"

    def __init__(self, sdk_dir: Path):
        self._sdk_dir = sdk_dir
        self._sdk_manager_path = sdk_dir.joinpath("tools", "bin", "sdkmanager")
        self._env = dict(os.environ).update({'ANDROID_SDK_ROOT': str(self._sdk_dir)})

    @property
    def emulator_path(self) -> Path:
        return self._sdk_dir.joinpath("emulator", "emulator")

    @property
    def adb_path(self) -> Path:
        return self._sdk_dir.joinpath("platform-tools", "adb")

    def bootstrap(self, application: str, version: Optional[str] = None) -> None:
        application = f"{application};{version}" if version else f"{application}"
        if not os.path.exists(self._sdk_manager_path):
            bootstrap_zip = files(androidtestorchestrator).joinpath(os.path.join("resources", "sdkmanager",
                                                                                 "bootstrap.zip"))
            with zipfile.ZipFile(bootstrap_zip) as zfile:
                zfile.extractall(path=self._sdk_dir)
                if self._sdk_dir.joinpath("android_sdk_bootstrap").exists():
                    for file in glob.glob(str(self._sdk_dir.joinpath("android_sdk_bootstrap", "*"))):
                        basename = os.path.basename(file)
                        shutil.move(file, str(self._sdk_dir.joinpath(basename)))
        os.chmod(str(self._sdk_manager_path), stat.S_IRWXU)
        if not os.path.exists(self._sdk_manager_path):
            raise SystemError("Failed to properly install sdk manager for bootstrapping")
        print(f"Downloading to {self._sdk_dir}\n  {self._sdk_manager_path} {application}")
        completed = subprocess.Popen([self._sdk_manager_path, application], stdout=subprocess.PIPE, bufsize=0,
                                     stderr=subprocess.PIPE, stdin=subprocess.PIPE)
        assert completed.stdin is not None  # make mypy happy
        for _ in range(10):
            completed.stdin.write(b'y\n')
        assert completed.stdout is not None
        for line in iter(completed.stdout.readline, b''):
            if line:
                print(line.decode('utf-8'))
            else:
                break
        stdout, stderr = completed.communicate()
        if completed.returncode != 0:
            raise Exception(
                f"Failed to download/update {application}: {stdout.decode('utf-8')} {stderr.decode('utf-8')}")

    def bootstrap_platform_tools(self) -> "SdkManager":
        """
        download/update platform tools within the sdk
        :param version: version to update to or None for latest
        """
        self.bootstrap("platform-tools")
        return self

    def bootstrap_emulator(self) -> "SdkManager":
        """
        download/update emulator within the sdk
        :param version: version to update to or None for latest
        """
        self.bootstrap("emulator")
        return self

    def download_system_img(self, version: str) -> "SdkManager":
        """
        download/update system image with version
        :param version: version to download
        """
        self.bootstrap("system-images", version)
        return self
