import os
import logging
import time
from contextlib import suppress

from typing import Dict, List, Optional, Type, Tuple
from types import TracebackType
from androidtestorchestrator import Device, DeviceStorage
from androidtestorchestrator.application import Application, TestApplication

log = logging.getLogger()


class DevicePreparation:
    """
     Class used to prepare a device for test execution, including installing app, configuring settings/properties, etc.

     Typically used as a context manager that will then automatically call cleanup() at exit.  The class provides
     a list of features to setup and configure a device before test execution and teardown afterwards.
     This includes:
     * Ability to configure settings and system properties of the device (restored to original values on exit)
     * Ability to upload test vectors to external storage
     * Ability to verify network connection to a resource
     """

    def __init__(self, device: Device):
        """
        :param device:  device to install and run test app on
        """
        self._device: Device = device
        self._restoration_settings: Dict[Tuple[str, str], Optional[str]] = {}
        self._restoration_properties: Dict[str, Optional[str]] = {}

    def configure_device(self, settings: Optional[Dict[str, str]] = None,
                         properties: Optional[Dict[str, str]] = None) -> None:
        if settings:
            for setting, value in settings.items():
                ns, key = setting.split(':')
                self._restoration_settings[(ns, key)] = self._device.set_device_setting(ns, key, value)
        if properties:
            for property, value in properties.items():
                self._restoration_properties[property] = self._device.set_system_property(property, value)

    def verify_network_connection(self, domain: str, count: int = 3) -> None:
        """
        Verify connection to given domain is active.
        :param domain: address to test connection to
        :param count: number of packets to test
        :raises: IOError on failure to successfully ping given number of packets
        """
        lost_packet_count = self._device.check_network_connection(domain, count)
        if lost_packet_count > 0:
            raise IOError(
                f"Connection to {domain} failed; expected {count} packets but got {count - lost_packet_count}")

    def reverse_port_forward(self, device_port: int, local_port: int) -> None:
        """
        reverse forward traffic on remote port to local port

        :param device_port: remote device port to forward
        :param local_port: port to forward to
        """
        self._device.reverse_port_forward(device_port=device_port, local_port=local_port)

    def port_forward(self, local_port: int, device_port: int) -> None:
        """
        forward traffic from local port to remote device port

        :param local_port: port to forward from
        :param device_port: port to forward to
        """
        self._device.port_forward(local_port=local_port, device_port=device_port)

    def cleanup(self) -> None:
        """
        Remove all pushed files and uninstall all apps installed by this test prep
        """
        for ns, key in self._restoration_settings:
            with suppress(Exception):
                self._device.set_device_setting(ns, key, self._restoration_settings[(ns, key)] or '\"\"')
        for prop in self._restoration_properties:
            with suppress(Exception):
                self._device.set_system_property(prop, self._restoration_properties[prop] or '\"\"')
        self._device.remove_port_forward()
        self._device.remove_reverse_port_forward()

    def __enter__(self) -> "DevicePreparation":
        return self

    def __exit__(self, exc_type: Optional[Type[BaseException]],
                 exc_value: Optional[BaseException],
                 traceback: Optional[TracebackType]) -> None:
        self.cleanup()


class EspressoTestPreparation:
    """
    Class used to prepare a device for test execution, including installing app, configuring settings/properties, etc.

    Typically used as a context manager that will then automatically call cleanup() at exit.  The class provides
    a list of features to setup and configure a device before test execution and teardown afterwards.
    This includes:
    * Installation of a app under test and test app to testit
    * Ability to grant all user permissions (to prevent unwanted pop-ups) upon install
    * Ability to configure settings and system properties of the device (restored to original values on exit)
    * Ability to upload test vectors to external storage
    * Ability to verify network connection to a resource
    """

    def __init__(self, device: Device, path_to_apk: str, path_to_test_apk: str, grant_all_user_permissions: bool = True):
        """

        :param device:  device to install and run test app on
        :param path_to_apk: Path to apk bundle for target app
        :param path_to_test_apk: Path to apk bundle for test app
        :param grant_all_user_permissions: If True, grant all user permissions defined in the manifest of the app and
          test app (prevents pop-ups from occurring on first request for a user permission that can interfere
          with tests)
        """
        self._app = Application.from_apk(path_to_apk, device=device)
        self._test_app: TestApplication = TestApplication.from_apk(path_to_test_apk, device=device)
        self._installed = [self._app, self._test_app]
        self._storage = DeviceStorage(device)
        self._data_files: List[str] = []
        self._device = device
        if grant_all_user_permissions:
            self._test_app.grant_permissions()

    @property
    def test_app(self) -> TestApplication:
        return self._test_app

    @property
    def target_app(self) -> Application:
        return self._app

    def __enter__(self) -> "EspressoTestPreparation":
        return self

    def __exit__(self, exc_type: Optional[Type[BaseException]],
                 exc_value: Optional[BaseException],
                 traceback: Optional[TracebackType]) -> None:
        self.cleanup()

    def upload_test_vectors(self, root_path: str) -> float:
        """
        Upload test vectors to external storage on device for use by tests
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

    def setup_foreign_apps(self, paths_to_foreign_apks: List[str]) -> None:
        """
        Install other apps (outside of test app and app under test) in support of testing
        :param paths_to_foreign_apks: string list of paths to the apks to be installed
        """
        for path in paths_to_foreign_apks:
            self._installed.append(Application.from_apk(path, device=self._device))

    def cleanup(self) -> None:
        """
        Remove all pushed files and uninstall all apps installed by this test prep
        """
        for remote_path in self._data_files:
            try:
                self._storage.remove(remote_path)
            except Exception:
                log.error("Failed to remove remote file %s from device %s", remote_path, self._device.device_id)
        for app in self._installed:
            try:
                app.uninstall()
            except Exception:
                log.error("Failed to uninstall app %s", app.package_name)
