import os
import shutil
import tempfile
from pathlib import Path
from typing import Union, List, Tuple, Optional

import shiv.builder
from shiv.bootstrap.environment import Environment

_root = os.path.dirname(__file__)


class Bundle:

    def __init__(self, shiv_path: Union[Path, str]):
        self._tmpdir = tempfile.mkdtemp()
        self._resources = []
        if os.path.exists(shiv_path):
            raise FileExistsError(f"File {shiv_path} already exists")
        self._shiv_path = shiv_path

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        env = Environment(built_at=None, shiv_version="0.1.1")
        shiv.builder.create_archive(self._resources, self._shiv_path, main="client.worker:main", env=env)

    @property
    def shiv_path(self):
        return self._shiv_path

    def add_file(self, path: Union[Path, str], relative_path: Union[Path, str], mode : Optional[int] = None):
        """
        add given file to bundle
        :param path: path to the file to add
        :param relative_path: relative path within shiv zip app
        """
        if os.path.isabs(relative_path):
            raise Exception("relative path to add must be relative, not absolute")
        full_path = os.path.join(self._tmpdir, str(relative_path))
        if os.path.exists(full_path):
            raise FileExistsError(f"File {relative_path} already exists within bundle")
        if not os.path.isfile():
            raise FileNotFoundError(f"File {path} does not exist or is not a file")
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        shutil.copy(path, full_path)
        if mode is not None:
            os.chmod(full_path, mode)

    @classmethod
    def create(cls, shiv_path: Union[str, Path], app_apk: Union[str, Path], test_apk: Union[str, Path],
               emulator_launch_script: Union[str, Path], addl_resources: List[Tuple[Union[str, Path], Union[str, Path]]]):
        with Bundle(shiv_path) as bundle:
            bundle.add_file(os.path.join(_root, "client/worker.py"), "worker.py")
            bundle.add_file(os.path.join(_root, "client/__init__.py"), "worker.py")
            bundle.add_file(app_apk, os.path.join("client/resources/apps", "target_app.apk"))
            bundle.add_file(test_apk, os.path.join("client/resources/apps","test_app.apk"))
            bundle.add_file(emulator_launch_script, os.path.join("client/resources/scripts", "launch_emulators"), 0x077)
            for path, relpath in addl_resources:
                bundle.add_file(path, relpath)
        return bundle.shiv_path

