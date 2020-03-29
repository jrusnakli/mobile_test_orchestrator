import datetime
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Union, List, Tuple, Optional

import shiv.builder
import shiv.constants
from shiv.bootstrap.environment import Environment

_root = os.path.dirname(__file__)


class Bundle:

    def __init__(self, shiv_path: Union[Path, str]):
        self._resources = []
        if os.path.exists(shiv_path):
            raise FileExistsError(f"File {shiv_path} already exists")
        self._shiv_path = shiv_path
        self._tmpdir: Optional[str] = None

    def __enter__(self):
        self._tmpdir = Path(tempfile.mkdtemp())
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            env = Environment(built_at=datetime.datetime.strftime(datetime.datetime.utcnow(),
                                                                  shiv.constants.BUILD_AT_TIMESTAMP_FORMAT),
                              shiv_version="0.1.1")
            shiv.builder.create_archive(self._resources, self._shiv_path, main="client.worker:main", env=env,
                                        interpreter=sys.executable)
            shutil.rmtree(str(self._tmpdir))

    @property
    def shiv_path(self):
        return self._shiv_path

    def add_file(self, path: Union[Path, str], relative_path: Union[Path, str]) -> None:
        """
        add given file to bundle
        :param path: path to the file to add
        :param relative_path: relative path within shiv zip app
        """
        if self._tmpdir is None:
            raise Exception("Did not enter context of bundle.")
        path = Path(path) if isinstance(path, str) else path
        relative_path = Path(relative_path) if isinstance(relative_path, str) else relative_path
        if relative_path.is_absolute():
            raise Exception("relative path to add must be relative, not absolute")
        full_path = self._tmpdir.joinpath(relative_path)
        if full_path.exists():
            raise FileExistsError(f"File {relative_path} already exists within bundle")
        if not path.is_file():
            raise FileNotFoundError(f"File {path} does not exist or is not a file")
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        shutil.copy(path, full_path)

    def create(self, app_apk: Union[str, Path], test_apk: Union[str, Path],
               addl_resources: Optional[List[Tuple[Union[str, Path], Union[str, Path]]]] = None) -> None:
        with Bundle(self._shiv_path) as bundle:
            bundle.add_file(os.path.join(_root, "worker.py"), "multiproc/worker.py")
            bundle.add_file(os.path.join(_root, "__init__.py"), "multiproc/__init__.py")
            bundle.add_file(app_apk, os.path.join("multiproc/resources/apps", "target_app.apk"))
            bundle.add_file(test_apk, os.path.join("multiproc/resources/apps", "test_app.apk"))
            my_path = Path(_root).parent.parent
            for file in my_path.glob('./androidtestorchestrator/**/*.py'):
                bundle.add_file(file, file.relative_to(my_path))
            for path, relpath in addl_resources or []:
                bundle.add_file(path, relpath)

