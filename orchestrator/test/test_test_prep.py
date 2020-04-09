import os

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
        with EspressoTestPreparation(device=device,
                                     path_to_apk=support_app,
                                     path_to_test_apk=support_test_app,
                                     grant_all_user_permissions=False) as test_prep:
            test_prep.upload_test_vectors(root)
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
