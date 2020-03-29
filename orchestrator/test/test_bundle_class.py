import os
from pathlib import Path

from androidtestorchestrator.multiproc.bundle import Bundle


class TestBundle:

    def test_bundle_create(self, tmpdir, support_app, support_test_app):
        temp_dir = Path(str(tmpdir))
        shiv_path = temp_dir.joinpath("test.pyz")
        with Bundle(shiv_path) as bundle:
            bundle.create(test_apk=support_test_app, app_apk=support_app)
        assert os.path.isfile(str(temp_dir.joinpath("test.pyz")))
