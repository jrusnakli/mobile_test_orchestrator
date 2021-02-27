import os
import logging
import time
from contextlib import suppress

from typing import Dict, List, Optional, Type, Tuple
from types import TracebackType
from mobiletestorchestrator import Device, DeviceStorage
from mobiletestorchestrator.application import Application, TestApplication
from mobiletestorchestrator.device import DeviceConnectivity

log = logging.getLogger()


class DevicePreparation:
    """
     Class used to prepare a device for test execution, including installing app, configuring settings/properties, etc.

     Typically used as a context manager that will then automatically call cleanup() at exit.  The class provides
     a list of features to setup and configure a device before test execution and teardown afterwards to restore
     original settings/port configurations.
     This includes:
     * Ability to configure settings and system properties of the device (restored to original values on exit)
     * Ability to upload test vectors to external storage
     * Ability to verify network connection to a resource
     """

    def __init__(self, device: Device):
        """
        :param device:  device to install and run test app on
        """
        self._device = device
        self._device_network = DeviceConnectivity(device)
        self._restoration_settings: Dict[Tuple[str, str], Optional[str]] = {}
        self._restoration_properties: Dict[str, Optional[str]] = {}
        self._reverse_forwarded_ports: List[int] = []
        self._forwarded_ports: List[int] = []

    def configure_device(self, settings: Optional[Dict[str, str]] = None,
                         properties: Optional[Dict[str, str]] = None) -> None:
        if settings:
            for setting, value in settings.items():
                ns, key = setting.split(':')
                self._restoration_settings[(ns, key)] = self._device.set_device_setting(ns, key, value)
        if properties:
            for property, value in properties.items():
                self._restoration_properties[property] = self._device.set_system_property(property, value)

    def verify_network_connection(self, domain: str, count: int = 10, acceptable_loss: int = 3) -> None:
        """
        Verify connection to given domain is active.

        :param domain: address to test connection to
        :param count: number of packets to test
        :raises: IOError on failure to successfully ping given number of packets
        """
        lost_packet_count = self._device_network.check_network_connection(domain, count)
        if lost_packet_count > acceptable_loss:
            raise IOError(
                f"Connection to {domain} failed; expected {count} packets but got {count - lost_packet_count}")

    def reverse_port_forward(self, device_port: int, local_port: int) -> None:
        """
        reverse forward traffic on remote port to local port

        :param device_port: remote device port to forward
        :param local_port: port to forward to
        """
        self._device_network.reverse_port_forward(device_port=device_port, local_port=local_port)
        self._reverse_forwarded_ports.append(device_port)

    def port_forward(self, local_port: int, device_port: int) -> None:
        """
        forward traffic from local port to remote device port

        :param local_port: port to forward from
        :param device_port: port to forward to
        """
        self._device_network.port_forward(local_port=local_port, device_port=device_port)
        self._forwarded_ports.append(device_port)

    def cleanup(self) -> None:
        """
        Remove all pushed files and uninstall all apps installed by this test prep
        """
        for ns, key in self._restoration_settings:
            with suppress(Exception):
                self._device.set_device_setting(ns, key, self._restoration_settings[(ns, key)] or '\"\"')
        self._restoration_settings = {}
        for prop in self._restoration_properties:
            with suppress(Exception):
                self._device.set_system_property(prop, self._restoration_properties[prop] or '\"\"')
        self._restoration_properties = {}
        for port in self._forwarded_ports:
            self._device_network.remove_port_forward(port)
        self._forwarded_ports = []
        for port in self._reverse_forwarded_ports:
            self._device_network.remove_reverse_port_forward(port)
        self._reverse_forwarded_ports = []

    def __enter__(self) -> "DevicePreparation":
        return self

    def __exit__(self, exc_type: Optional[Type[BaseException]],
                 exc_value: Optional[BaseException],
                 traceback: Optional[TracebackType]) -> None:
        try:
            self.cleanup()
        except Exception:
            log.exception("Failed to cleanup properly on device restoration")


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

    def __init__(self, device: Device, path_to_apk: str, path_to_test_apk: str,
                 timeout: Optional[int] = Device.TIMEOUT_LONG_ADB_CMD,
                 grant_all_user_permissions: bool = True):
        """

        :param device:  device to install and run test app on
        :param path_to_apk: Path to apk bundle for target app
        :param path_to_test_apk: Path to apk bundle for test app
        :param timeout: if specified, raise TimeoutError if install takes too long
        :param grant_all_user_permissions: If True, grant all user permissions defined in the manifest of the app and
          test app (prevents pop-ups from occurring on first request for a user permission that can interfere
          with tests)
        :raises TimeoutError: if timeout specified and install does not complete wihtin this time
        """
        self._app = Application.from_apk(path_to_apk, device=device, timeout=timeout)
        self._test_app: TestApplication = TestApplication.from_apk(apk_path=path_to_test_apk, device=device,
                                                                   timeout=timeout)
        self._installed = [self._app, self._test_app]
        self._storage = DeviceStorage(device)
        self._data_files: List[str] = []
        self._device = device
        if grant_all_user_permissions:
            self._test_app.grant_permissions()
            self._app.grant_permissions()

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
        if not os.path.isdir(root_path):
            raise IOError(f"Given path {root_path} to upload to device does not exist or is not a directory")
        ext_storage = self._device.external_storage_location
        for root, _, files in os.walk(root_path, topdown=True):
            basedir = os.path.relpath(root, root_path)
            remote_location = "/".join([ext_storage, basedir]) + '/'
            with suppress(Exception):
                self._storage.make_dir(remote_location)
            for filename in files:
                self._data_files.append(os.path.join(remote_location, filename))
                self._storage.push(os.path.join(root, filename), remote_location)
        milliseconds = (time.time() - start) * 1000
        return milliseconds

    def setup_foreign_apps(self, paths_to_foreign_apks: List[str],
                           timeout: Optional[int] = Device.TIMEOUT_LONG_ADB_CMD) -> None:
        """
        Install other apps (outside of test app and app under test) in support of testing
        :param paths_to_foreign_apks: string list of paths to the apks to be installed
        :param timeout: if specified, raise TimeoutError if install takes too long
        :raises TimeoutError: if timeout specified and install takes more than specified time
        """
        for path in paths_to_foreign_apks:
            self._installed.append(Application.from_apk(apk_path=path, device=self._device, timeout=timeout))

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
