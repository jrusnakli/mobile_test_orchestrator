import datetime
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Union, List, Tuple, Optional, Any

import shiv.builder  # type: ignore
import shiv.constants  # type: ignore
from shiv import pip
from shiv.bootstrap.environment import Environment  # type: ignore

_root = os.path.dirname(__file__)


class Bundle:

    def __init__(self, shiv_path: Union[Path, str]):
        self._sources: List[Path] = []
        if os.path.exists(shiv_path):
            raise FileExistsError(f"File {shiv_path} already exists")
        self._shiv_path = shiv_path if isinstance(shiv_path, Path) else Path(shiv_path)
        self._tmpdir: Optional[tempfile.TemporaryDirectory] = None  # type: ignore

    def __enter__(self) -> "Bundle":
        self._tmpdir = tempfile.TemporaryDirectory()
        self._site_pkgs = tempfile.TemporaryDirectory()
        self._site_pkgs.__enter__()
        self._sources.append(Path(self._tmpdir.name))
        self._sources.append(Path(self._site_pkgs.name))
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        try:
            if exc_type is None:
                env = Environment(built_at=datetime.datetime.strftime(datetime.datetime.utcnow(),
                                                                      shiv.constants.BUILD_AT_TIMESTAMP_FORMAT),
                                  shiv_version="0.1.1")
                shiv.builder.create_archive(sources=self._sources,
                                            target=self._shiv_path,
                                            main="androidtestorchestrator.remote.client:main",
                                            env=env,
                                            compressed=True,
                                            interpreter=sys.executable)
        finally:
            self._site_pkgs.__exit__(exc_type, exc_val, exc_tb)
            if self._tmpdir is not None:
                self._tmpdir.__exit__(exc_type, exc_val, exc_tb)

    @property
    def shiv_path(self) -> Path:
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
        full_path = Path(self._tmpdir.name).joinpath(relative_path)
        if full_path.exists():
            raise FileExistsError(f"File {relative_path} already exists within bundle")
        if not path.is_file():
            raise FileNotFoundError(f"File {path} does not exist or is not a file")
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        shutil.copy(path, full_path)

    @classmethod
    def create(cls, shiv_path: Union[str, Path], app_apk: Union[str, Path], test_apk: Union[str, Path],
               addl_resources: Optional[List[Tuple[Union[str, Path], Union[str, Path]]]] = None) -> None:
        with Bundle(shiv_path) as bundle:
            my_path = Path(_root).parent.parent
            for file in my_path.glob('./mobiletestorchestrator/**/*.py'):
                bundle.add_file(file, file.relative_to(my_path))
            for file in my_path.glob('./mobiletestorchestrator/resources/**/*.zip'):
                bundle.add_file(file, file.relative_to(my_path))
            for path, relpath in addl_resources or []:
                bundle.add_file(path, relpath)
            if isinstance(app_apk, str):
                app_apk = Path(app_apk)
            if isinstance(test_apk, str):
                test_apk = Path(test_apk)
            bundle.add_file(app_apk, f"./mobiletestorchestrator/resources/apks/{app_apk.name}")
            bundle.add_file(test_apk, f"./mobiletestorchestrator/resources/apks/{test_apk.name}")
            pip.install(["--target", bundle._site_pkgs.name, "apk-bitminer>=1.1.0", "importlib_resources", "pathlib"])
