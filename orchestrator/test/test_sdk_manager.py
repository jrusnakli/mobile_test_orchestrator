from pathlib import Path

from androidtestorchestrator.tooling.sdkmanager import SdkManager


class TestSdkManager:

    def test_emulator_path(self, mp_tmp_dir: Path):
        sdk_manager = SdkManager(sdk_dir=mp_tmp_dir)
        assert sdk_manager.emulator_path == mp_tmp_dir.joinpath("emulator", "emulator")

    def test_adb_path(self, mp_tmp_dir):
        sdk_manager = SdkManager(sdk_dir=mp_tmp_dir)
        assert sdk_manager.adb_path == mp_tmp_dir.joinpath("platform-tools", "adb")

    def test_bootstrap(self, mp_tmp_dir):
        sdk_manager = SdkManager(sdk_dir=mp_tmp_dir)
        sdk_manager.bootstrap("platform-tools")
        assert sdk_manager.adb_path.exists()

    def test_bootstrap_platform_tools(self, mp_tmp_dir):
        sdk_manager = SdkManager(sdk_dir=mp_tmp_dir)
        sdk_manager.bootstrap_platform_tools()

    def test_bootstrap_emulator(self, mp_tmp_dir):
        sdk_manager = SdkManager(sdk_dir=mp_tmp_dir)
        sdk_manager.bootstrap_emulator()

    def test_download_system_img(self, mp_tmp_dir):
        sdk_manager = SdkManager(sdk_dir=mp_tmp_dir)
        sdk_manager.download_system_img(version="android-29;default;x86")
