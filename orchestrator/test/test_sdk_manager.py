import getpass
import os
from pathlib import Path

import sys

from mobiletestorchestrator.tooling.sdkmanager import SdkManager
import pytest

try:
    IS_CIRCLECI = getpass.getuser() == 'circleci' or "CIRCLECI" in os.environ
except ModuleNotFoundError:
    IS_CIRCLECI = False  # on Windows, pwd is missing and getpass import failes

@pytest.mark.skipif(IS_CIRCLECI, reason="Tests have too long a time without output")
class TestSdkManager:

    def _patch(self, tmp_dir, monkeypatch):
        def mock_bootstrap(self, target: str, *args: str):
            assert target == "platform-tools"

        monkeypatch.setattr("mobiletestorchestrator.tooling.sdkmanager.SdkManager.bootstrap", mock_bootstrap)
        os.makedirs(tmp_dir.joinpath("tools").joinpath("bin"), exist_ok=True)
        with open(tmp_dir.joinpath("tools").joinpath("bin").joinpath("sdkmanager"), 'w') as f:
            pass
        with open(tmp_dir.joinpath("tools").joinpath("bin").joinpath("avdmanager"), 'w') as f:
            pass

    def test_emulator_path(self, tmp_path: Path):
        tmp_dir = tmp_path / "sdk"
        tmp_dir.mkdir(exist_ok=True)
        sdk_manager = SdkManager(sdk_dir=tmp_dir, bootstrap=True)
        if sys.platform.lower() == 'win32':
            assert sdk_manager.emulator_path == tmp_dir.joinpath("emulator", "emulator.exe")
        else:
            assert sdk_manager.emulator_path == tmp_dir.joinpath("emulator", "emulator")

    def test_adb_path(self, tmp_path):
        tmp_dir = tmp_path / "adb"
        tmp_dir.mkdir(exist_ok=True)
        sdk_manager = SdkManager(sdk_dir=tmp_dir, bootstrap=True)
        if sys.platform.lower() == 'win32':
            assert sdk_manager.adb_path == tmp_dir.joinpath("platform-tools", "adb.exe")
        else:
            assert sdk_manager.adb_path == tmp_dir.joinpath("platform-tools", "adb")

    def test_bootstrap(self, tmp_path, monkeypatch):
        asdk = os.environ["ANDROID_SDK_ROOT"]
        tmp_dir = tmp_path / "bootstrap"
        tmp_dir.mkdir(exist_ok=False)
        try:
            del os.environ["ANDROID_SDK_ROOT"]
            with pytest.raises(FileNotFoundError):
                # sdkmanager nor avdmanaqger exist and this should raise assertion error since we are not bootstrapping
                # from internals
                SdkManager(sdk_dir=tmp_dir, bootstrap=False)
            sdk_manager = SdkManager(sdk_dir=tmp_dir, bootstrap=True)
            sdk_manager.bootstrap("platform-tools")
            assert sdk_manager.adb_path.exists()
        finally:
            os.environ["ANDROID_SDK_ROOT"] = asdk

    def test_bootstrap_platform_tools(self, tmp_path: Path):
        tmp_dir= tmp_path / "bootstrap"
        tmp_dir.mkdir(exist_ok=True)
        sdk_manager = SdkManager(sdk_dir=tmp_dir, bootstrap=True)
        sdk_manager.bootstrap_platform_tools()
        assert sdk_manager.adb_path.exists()

    def test_bootstrap_emulator(self, tmp_path: Path):
        tmp_dir = tmp_path / "emul"
        tmp_dir.mkdir(exist_ok=True)
        sdk_manager = SdkManager(sdk_dir=tmp_dir, bootstrap=True)
        sdk_manager.bootstrap_emulator()
        assert sdk_manager.emulator_path.exists()

    def test_download_system_img(self, tmp_path: Path):
        tmp_dir = tmp_path / "system_img"
        tmp_dir.mkdir(exist_ok=True)
        sdk_manager = SdkManager(sdk_dir=tmp_dir, bootstrap=True)
        sdk_manager.download_system_img(version="android-29;default;x86")
        assert (tmp_dir / "system-images" / "android-29" / "default" / "x86" / "system.img").exists()
