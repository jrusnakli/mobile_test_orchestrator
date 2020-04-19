import asyncio
import os
import pytest

from androidtestorchestrator import DeviceStorage
from androidtestorchestrator.testprep import EspressoTestPreparation


class TestEspressoTestPreparation:

    def test_upload_test_vectors(self, device, support_app, support_test_app, tmpdir):
        root = os.path.join(str(tmpdir), "data_files")
        os.makedirs(root)
        tv_dir = os.path.join(root, "test_vectors")
        os.makedirs(tv_dir)
        with open(os.path.join(tv_dir, "tv-1.txt"), 'w'):
            pass
        with open(os.path.join(tv_dir, "tv-2.txt"), 'w'):
            pass

        async def run():
            async with EspressoTestPreparation(devices=device,
                                     path_to_apk=support_app,
                                     path_to_test_apk=support_test_app,
                                     grant_all_user_permissions=False) as test_prep:
                assert test_prep.target_apps
                assert test_prep.test_apps
                await test_prep.upload_test_vectors(root)
                storage = DeviceStorage(device)
                test_dir = os.path.join(str(tmpdir), "test_download")
                storage.pull(remote_path="/".join([storage.external_storage_location, "test_vectors"]),
                             local_path=os.path.join(test_dir))
                assert os.path.exists(os.path.join(test_dir, "tv-1.txt"))
                assert os.path.exists(os.path.join(test_dir, "tv-2.txt"))
            test_dir2 = os.path.join(str(tmpdir), "no_tv_download")
            os.makedirs(test_dir2)
            storage.pull(remote_path="/".join([storage.external_storage_location, "test_vectors"]),
                         local_path=os.path.join(test_dir2))
            assert not os.path.exists(os.path.join(test_dir2, "tv-1.txt"))
            assert not os.path.exists(os.path.join(test_dir2, "tv-2.txt"))

        asyncio.get_event_loop().run_until_complete(run())

    def test_upload_test_vectors_no_such_files(self, device, support_app, support_test_app,):
        with pytest.raises(IOError):
            async def run():
                async with EspressoTestPreparation(devices=device,
                                                   path_to_apk=support_app,
                                                   path_to_test_apk=support_test_app,
                                                   grant_all_user_permissions=False) as test_prep:
                    await test_prep.upload_test_vectors("/no/such/path")

            asyncio.get_event_loop().run_until_complete(run())

    def test_upload_test_ignore_exception_cleanup(self, device, support_app, support_test_app, monkeypatch):
        def mock_uninstall(*args, **kargs):
            raise Exception("For test purposes")

        def mock_log_error1(self, msg: str, *args):

            if self.name == "testprep":
                assert msg.startswith("Failed to remove remote file")

        def mock_log_error2(self, msg: str, *args):
            if self.name == "testprep":
                assert msg.startswith("Failed to uninstall")

        monkeypatch.setattr("androidtestorchestrator.application.Application.uninstall", mock_uninstall)
        monkeypatch.setattr("logging.Logger.log", mock_log_error2)

        async def run():
            async with EspressoTestPreparation(devices=device,
                                               path_to_apk=support_app,
                                               path_to_test_apk=support_test_app,
                                               grant_all_user_permissions=False) as test_prep:
                test_prep.cleanup()  # exception should be swallowed

            monkeypatch.setattr("logging.Logger.log", mock_log_error1)
            async with EspressoTestPreparation(devices=device,
                                               path_to_apk=support_app,
                                               path_to_test_apk=support_test_app,
                                               grant_all_user_permissions=False) as test_prep:
                test_prep._data_files = ["/some/file"]
                test_prep._storage = None  # to force exception path
                # should not raise an error:
                test_prep.cleanup()

        asyncio.get_event_loop().run_until_complete(run())
