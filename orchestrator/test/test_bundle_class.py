import os
import zipfile
from pathlib import Path

from androidtestorchestrator.tooling.bundle import Bundle


class TestBundle:

    def test_bundle_create(self, temp_dir, support_app, support_test_app):
        temp_dir = Path(str(temp_dir))
        shiv_path = temp_dir.joinpath("test.pyz")
        Bundle.create(shiv_path=shiv_path, test_apk=support_test_app, app_apk=support_app)
        assert os.path.isfile(str(temp_dir.joinpath("test.pyz")))
        with zipfile.ZipFile(shiv_path) as zfile:
            filelist = [item.filename for item in zfile.filelist]
            assert "site-packages/androidtestorchestrator/resources/apks/app-debug.apk" in filelist
            assert "site-packages/androidtestorchestrator/resources/apks/app-debug-androidTest.apk" in filelist
