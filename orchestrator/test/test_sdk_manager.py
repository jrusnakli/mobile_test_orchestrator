import getpass
import os
from pathlib import Path

from mobiletestorchestrator.tooling.sdkmanager import SdkManager
import pytest

IS_CIRCLECI = getpass.getuser() == 'circleci' or "CIRCLECI" in os.environ


@pytest.mark.skipif(IS_CIRCLECI, reason="Tests have too long a time without output")
class TestSdkManager:

    def _patch(self, mp_tmp_dir, monkeypatch):
        def mock_bootstrap(self, target: str, *args: str):
            assert target == "platform-tools"

        monkeypatch.setattr("mobiletestorchestrator.tooling.sdkmanager.SdkManager.bootstrap", mock_bootstrap)
        os.makedirs(mp_tmp_dir.joinpath("tools").joinpath("bin"), exist_ok=True)
        with open(mp_tmp_dir.joinpath("tools").joinpath("bin").joinpath("sdkmanager"), 'w') as f:
            pass
        with open(mp_tmp_dir.joinpath("tools").joinpath("bin").joinpath("avdmanager"), 'w') as f:
            pass

    def test_emulator_path(self, mp_tmp_dir: Path):
        sdk_manager = SdkManager(sdk_dir=mp_tmp_dir, bootstrap=True)
        assert sdk_manager.emulator_path == mp_tmp_dir.joinpath("emulator", "emulator")

    def test_adb_path(self, mp_tmp_dir):
        sdk_manager = SdkManager(sdk_dir=mp_tmp_dir, bootstrap=True)
        assert sdk_manager.adb_path == mp_tmp_dir.joinpath("platform-tools", "adb")

    def test_bootstrap(self, mp_tmp_dir, monkeypatch):
        asdk = os.environ["ANDROID_SDK_ROOT"]
        try:
            del os.environ["ANDROID_SDK_ROOT"]
            with pytest.raises(FileNotFoundError):
                # sdkmanager nor avdmanaqger exist and this should raise assertion error since we are not bootstrapping
                # from internals
                SdkManager(sdk_dir=mp_tmp_dir, bootstrap=False)
            sdk_manager = SdkManager(sdk_dir=mp_tmp_dir, bootstrap=True)
            sdk_manager.bootstrap("platform-tools")
            assert sdk_manager.adb_path.exists()
        finally:
            os.environ["ANDROID_SDK_ROOT"] = asdk

    def test_bootstrap_platform_tools(self, mp_tmp_dir, monkeypatch):
        self._patch(mp_tmp_dir, monkeypatch)  # contains assertion
        sdk_manager = SdkManager(sdk_dir=mp_tmp_dir, bootstrap=False)
        sdk_manager.bootstrap_platform_tools()

    def test_bootstrap_emulator(self, mp_tmp_dir):
        sdk_manager = SdkManager(sdk_dir=mp_tmp_dir, bootstrap=True)
        sdk_manager.bootstrap_emulator()

    def test_download_system_img(self, mp_tmp_dir):
        sdk_manager = SdkManager(sdk_dir=mp_tmp_dir, bootstrap=True)
        sdk_manager.download_system_img(version="android-29;default;x86")
