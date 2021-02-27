import os
import pytest

from mobiletestorchestrator import DeviceStorage
from mobiletestorchestrator.testprep import EspressoTestPreparation


class TestEspressoTestPreparation:

    def test_upload_test_vectors(self, device, support_app, support_test_app, mp_tmp_dir):
        root = os.path.join(str(mp_tmp_dir), "data_files")
        os.makedirs(root)
        tv_dir = os.path.join(root, "test_vectors")
        os.makedirs(tv_dir)
        with open(os.path.join(tv_dir, "tv-1.txt"), 'w'):
            pass
        with open(os.path.join(tv_dir, "tv-2.txt"), 'w'):
            pass
        with EspressoTestPreparation(device=device,
                                     path_to_apk=support_app,
                                     path_to_test_apk=support_test_app,
                                     grant_all_user_permissions=False) as test_prep:
            assert test_prep.target_app is not None
            assert test_prep.test_app is not None
            test_prep.upload_test_vectors(root)
            storage = DeviceStorage(device)
            test_dir = os.path.join(str(mp_tmp_dir), "test_download")
            storage.pull(remote_path="/".join([storage.external_storage_location, "test_vectors"]),
                         local_path=os.path.join(test_dir))
            assert os.path.exists(os.path.join(test_dir, "tv-1.txt"))
            assert os.path.exists(os.path.join(test_dir, "tv-2.txt"))
        test_dir2 = os.path.join(str(mp_tmp_dir), "no_tv_download")
        os.makedirs(test_dir2)
        storage.pull(remote_path="/".join([storage.external_storage_location, "test_vectors"]),
                     local_path=os.path.join(test_dir2))
        assert not os.path.exists(os.path.join(test_dir2, "tv-1.txt"))
        assert not os.path.exists(os.path.join(test_dir2, "tv-2.txt"))

    def test_upload_test_vectors_no_such_files(self, device, support_app, support_test_app,):
        with pytest.raises(IOError):
            with EspressoTestPreparation(device=device,
                                         path_to_apk=support_app,
                                         path_to_test_apk=support_test_app,
                                         grant_all_user_permissions=False) as test_prep:
                test_prep.upload_test_vectors("/no/such/path")

    def test_upload_test_ignore_exception_cleanup(self, device, support_app, support_test_app, monkeypatch):
        def mock_uninstall(*args, **kargs):
            raise Exception("For test purposes")

        def mock_log_error1(self, msg: str):
            assert msg.startswith("Failed to remove remote file")

        def mock_log_error2(self, msg: str):
            assert msg.startswith("Failed to uninstall")

        monkeypatch.setattr("androidtestorchestrator.application.Application.uninstall", mock_uninstall)
        monkeypatch.setattr("logging.Logger.log", mock_log_error2)
        with EspressoTestPreparation(device=device,
                                     path_to_apk=support_app,
                                     path_to_test_apk=support_test_app,
                                     grant_all_user_permissions=False) as test_prep:
            test_prep.cleanup()  # exception should be swallowed

        monkeypatch.setattr("logging.Logger.log", mock_log_error1)
        with EspressoTestPreparation(device=device,
                                     path_to_apk=support_app,
                                     path_to_test_apk=support_test_app,
                                     grant_all_user_permissions=False) as test_prep:
            test_prep._data_files = ["/some/file"]
            test_prep._storage = None  # to force exception path
            # should not raise an error:
            test_prep.cleanup()
