import asyncio
import os
import pytest

from androidtestorchestrator import DeviceStorage
from androidtestorchestrator.device import Device
from androidtestorchestrator.testprep import EspressoTestSetup


class TestEspressoTestPreparation:

    def test_upload_test_vectors(self, device, support_app, support_test_app, temp_dir):
        root = os.path.join(str(temp_dir), "data_files")
        os.makedirs(root)
        tv_dir = os.path.join(root, "test_vectors")
        os.makedirs(tv_dir)
        with open(os.path.join(tv_dir, "tv-1.txt"), 'w'):
            pass
        with open(os.path.join(tv_dir, "tv-2.txt"), 'w'):
            pass

        async def run():
            bundle = EspressoTestSetup(path_to_apk=support_app,
                                       path_to_test_apk=support_test_app,
                                       grant_all_user_permissions=False)
            bundle.upload_test_vectors(root)
            async with bundle.apply(device) as test_app:
                assert test_app
                storage = DeviceStorage(device)
                test_dir = os.path.join(str(temp_dir), "test_download")
                storage.pull(remote_path="/".join([storage.external_storage_location, "test_vectors"]),
                             local_path=os.path.join(test_dir))
                assert os.path.exists(os.path.join(test_dir, "tv-1.txt"))
                assert os.path.exists(os.path.join(test_dir, "tv-2.txt"))

            # cleanup occurred on exit of context manager, so...
            test_dir2 = os.path.join(str(temp_dir), "no_tv_download")
            os.makedirs(test_dir2)
            storage.pull(remote_path="/".join([storage.external_storage_location, "test_vectors"]),
                         local_path=os.path.join(test_dir2))
            assert not os.path.exists(os.path.join(test_dir2, "tv-1.txt"))
            assert not os.path.exists(os.path.join(test_dir2, "tv-2.txt"))

        asyncio.get_event_loop().run_until_complete(run())

    def test_upload_test_vectors_no_such_files(self, device, support_app, support_test_app,):
        with pytest.raises(IOError):
            bundle = EspressoTestSetup(path_to_apk=support_app,
                                       path_to_test_apk=support_test_app,
                                       grant_all_user_permissions=False)

            bundle.upload_test_vectors("/no/such/path")

    @pytest.mark.asyncio
    async def test_foreign_apk_install(self, device: Device, support_app: str, support_test_app: str):
        prep = EspressoTestSetup(path_to_test_apk=support_test_app, path_to_apk=support_app)
        prep.add_foreign_apks([support_test_app])
        device.set_system_property("debug.mock2", "\"\"\"\"")
        now = device.get_device_setting("system", "dim_screen")
        new = {"1": "0", "0": "1"}[now]
        prep.configure_settings(settings={'system:dim_screen': new},
                                properties={"debug.mock2": "5555"})

        async with prep.apply(device) as test_app:
            test_app.uninstall()
            assert test_app.package_name not in device.list_installed_packages()
            assert test_app.target_application.package_name in device.list_installed_packages()
            assert device.get_system_property("debug.mock2") == "5555"
            assert device.get_device_setting("system", "dim_screen") == new
