import os
import logging
import time

from typing import Dict, List, Optional, Type
from types import TracebackType
from androidtestorchestrator import Device, DeviceStorage
from androidtestorchestrator.application import Application, TestApplication

log = logging.getLogger()


class EspressoTestPreparation:
    """
    Class used to prepare a device for test execution, including installing app, configuring settings/properties, etc.
    """

    def __init__(self, device: Device, path_to_apk: str, path_to_test_apk: str, grant_all_user_permissions: bool = True):
        """

        :param device:  device to install and run test app on
        :param path_to_apk: Path to apk bundle for target app under test
        :param path_to_test_apk: Path to apk for test app
        :param grant_all_user_permissions: If True, grant all user permissions defined in the manifest of the app and
          test app (prevents pop-ups from occurring on first request for a user permission that can interfere
          with tests)
        """
        self._device: Device = device
        self._storage = DeviceStorage(self._device)
        app = Application.from_apk(path_to_apk, device=self._device)
        self._test_app: TestApplication = TestApplication.from_apk(path_to_test_apk, device=self._device)
        self._installed = [app, self._test_app]
        self._data_files: List[str] = []
        if grant_all_user_permissions:
            self._test_app.grant_permissions()

    @property
    def test_app(self) -> TestApplication:
        return self._test_app

    def configure_device(self, settings: Optional[Dict[str, str]] = None,
                         properties: Optional[Dict[str, str]] = None) -> None:
        if settings:
            for setting, value in settings.items():
                ns, key = setting.split(':')
                self._device.set_device_setting(ns, key, value)
        if properties:
            for property, value in properties.items():
                self._device.set_system_property(property, value)

    def setup_device(self, paths_to_foreign_apks: List[str]) -> None:
        for path in paths_to_foreign_apks:
            self._installed.append(Application.from_apk(path, device=self._device))

    def upload_test_vectors(self, root_path: str) -> float:
        """
        upload test vectors to external storage on device for use by tests
        :param root_path: local path that is the root where data files reside;  directory structure will be mimiced below
            this level
        :return: time in milliseconds it took to complete
        """
        start = time.time()
        for root, dir_, files in os.walk(root_path, topdown=True):
            if not os.path.isdir(root_path):
                raise IOError(f"Given path {root_path} to upload to device does exist or is not a directory")
            ext_storage = self._device.external_storage_location
            basedir = os.path.relpath(root, root_path)
            for filename in files:
                remote_location = "/".join([ext_storage, basedir])
                self._data_files.append(os.path.join(remote_location, filename))
                self._storage.push(os.path.join(root, filename), remote_location)
        milliseconds = (time.time() - start) * 1000
        return milliseconds

    def verify_network_connection(self, domain: str, count: int = 3) -> None:
        """
        :param domain: address to test connection to
        :param count: number of packets to test
        :return: number of failed packets (aka 0 on success)
        """
        lost_packet_count = self._device.check_network_connection(domain, count)
        if lost_packet_count > 0:
            raise IOError(f"Connection to {domain} failed; expected {count} packets but got {count - lost_packet_count}")

    def cleanup(self) -> None:
        """
        Remove all pushed files and uninstall all apps installed by this test prep
        """
        for app in self._installed:
            try:
                app.uninstall()
            except Exception:
                log.error("Failed to uninstall app %s", app.package_name)
        for remote_path in self._data_files:
            try:
                self._storage.remove(remote_path)
            except Exception:
                log.error(f"Failed to remove remote file {remote_path} from device {self._device.device_id}")

    def __enter__(self) -> "EspressoTestPreparation":
        return self

    def __exit__(self, exc_type: Optional[Type[BaseException]],
                 exc_value: Optional[BaseException],
                 traceback: Optional[TracebackType]) -> None:
        self.cleanup()
