import os
import logging
from contextlib import suppress, asynccontextmanager

from typing import Dict, List, Optional, Tuple, AsyncContextManager, AsyncGenerator
from androidtestorchestrator.device import Device
from androidtestorchestrator.devicestorage import DeviceStorage
from androidtestorchestrator.application import Application, TestApplication

log = logging.getLogger(__name__)


class DevicePreparation:
    """
     Class used to prepare a device for test execution, including installing app, configuring settings/properties, etc.
     The API provides a way to setup the configuration that will later be applied by the client across one or many
     devices.  The client calls 'apply' as a context manager to apply the configuration to a fiven device, ensuring
     restoration of original settings and properties upon exit
     """

    def __init__(self):
        self._reverse_forwarded_ports: Dict[int, int] = {}
        self._forwarded_ports: Dict[int, int] = {}
        self._requested_settings: Dict[str, str] = {}
        self._requested_properties: Dict[str, str] = {}
        self._verify_network_domains: List[Tuple[str, int, int]] = []

    def configure_settings(self,
                           settings: Optional[Dict[str, str]] = None,
                           properties: Optional[Dict[str, str]] = None) -> None:
        self._requested_settings = settings
        self._requested_properties = properties

    def verify_network_connection(self, domain: str, count: int = 10, acceptable_loss: int = 3) -> None:
        """
        Verify connection to given domain is active.

        :param domain: address to test connection to
        :param count: number of packets to test
        :param acceptable_loss: allowed number of packets to be dropped

        :raises: IOError on failure to successfully ping given number of packets
        """
        self._verify_network_domains.append((domain, count, acceptable_loss))

    def reverse_port_forward(self, device_port: int, local_port: int) -> None:
        """
        reverse forward traffic on remote port to local port

        :param device_port: remote device port to forward
        :param local_port: port to forward to
        """
        if device_port in self._reverse_forwarded_ports:
            raise Exception("Attempt to reverse-forward and already reverse-forwarded port")
        self._reverse_forwarded_ports[device_port] = local_port

    def port_forward(self, local_port: int, device_port: int) -> None:
        """
        forward traffic from local port to remote device port

        :param local_port: port to forward from
        :param device_port: port to forward to
        """
        if device_port in self._forwarded_ports:
            raise Exception("Attempt to forward to same local port")
        self._forwarded_ports[device_port] = local_port

    @asynccontextmanager
    async def apply(self, device: Device) -> AsyncContextManager["DevicePreparation"]:
        """
        Apply requested settings/configuration to the given device
        :param device: device to apply chnages to
        """
        restoration_settings: Dict[Tuple[str, str], Optional[str]] = {}
        restoration_properties: Dict[str, Optional[str]] = {}

        for domain, count, acceptable_loss in self._verify_network_domains:
            lost_packets = await device.check_network_connection(domain, count)
            if lost_packets > acceptable_loss:
                raise IOError(f"Connection to {domain} for device {device.device_id} failed")
        if self._requested_settings:
            for setting, value in self._requested_settings.items():
                ns, key = setting.split(':')
                result = device.set_device_setting(ns, key, value)
                restoration_settings[(ns, key)] = result
        if self._requested_properties:
            for property_, value in self._requested_properties.items():
                result = device.set_system_property(property_, value)
                restoration_properties[property_] = result
        for device_port, local_port in self._reverse_forwarded_ports.items():
            device.reverse_port_forward(device_port=device_port, local_port=local_port)
        for device_port, local_port in self._forwarded_ports.items():
            device.port_forward(local_port=local_port, device_port=device_port)

        yield self

        #####
        # cleanup/restoration:
        #####

        for (ns, key), setting in restoration_settings.items():
            with suppress(Exception):
                device.set_device_setting(ns, key, setting or '\"\"')
        for prop in restoration_properties:
            with suppress(Exception):
                device.set_system_property(prop, restoration_properties[prop] or '\"\"')
        for device_port in self._reverse_forwarded_ports:
            try:
                device.remove_reverse_port_forward(device_port)
            except Exception as e:
                log.error(f"Failed to remove reverse port forwarding for device {device.device_id}" +
                          f"on port {device_port}: {str(e)}")
        for device_port in self._forwarded_ports:
            try:
                device.remove_port_forward(device_port)
            except Exception as e:
                log.error(f"Failed to remove port forwarding for device {device.device_id}:"
                          + f"on port {device_port}: {str(e)}")


class EspressoTestSetup(DevicePreparation):
    """
    Class used to prepare a device for test execution, including installing app, configuring settings/properties, etc.

    Typically used as a context manager that will then automatically call cleanup() at exit.  The class provides
    a list of features to setup and configure a device before test execution and teardown afterwards.
    This includes:
    * Installation of a app under test and test app to testit
    * Ability to grant all user permissions (to prevent unwanted pop-ups) upon install
    * Ability to configure settings and system properties of the device (restored to original values on exit)
    * Ability to upload test vectors to external storage
    """

    def __init__(self, path_to_apk: str, path_to_test_apk: str, grant_all_user_permissions: bool = True):
        """

        :param path_to_apk: Path to apk bundle for target app
        :param path_to_test_apk: Path to apk bundle for test app
        :param grant_all_user_permissions: If True, grant all user permissions defined in the manifest of the app and
          test app (prevents pop-ups from occurring on first request for a user permission that can interfere
          with tests)
        """
        super().__init__()
        self._path_to_apk = path_to_apk
        self._path_to_test_apk = path_to_test_apk
        self._grant_all_user_permissions = grant_all_user_permissions

        self._paths_to_foreign_apks: List[str] = []
        self._uploadables: List[str] = []

    def upload_test_vectors(self, root_path: str) -> None:
        """
        Upload test vectors to external storage on device for use by tests

        :param root_path: local path that is the root where data files reside;  directory structure will be \
            mimicked below this level
        """
        if not os.path.isdir(root_path):
            raise IOError(f"Given path {root_path} to upload to device does exist or is not a directory")
        self._uploadables.append(root_path)

    def add_foreign_apks(self, paths_to_apks: List[str]) -> None:
        """
        :param paths_to_apks:  List of paths to other apks to install
        """
        self._paths_to_foreign_apks = paths_to_apks

    @asynccontextmanager
    async def apply(self, device: Device) -> AsyncGenerator[TestApplication, None]:
        installed: List[Application] = []
        data_files_paths: Dict[Device, List[str]] = {}
        try:
            for path in self._uploadables:
                data_files_paths[device] = await self._upload(device, path)

            async with super().apply(device):
                installed = await self.install_base(device)
                yield installed[0]  # = test_app
        except Exception as e:
            log.exception(e)
            raise
        finally:
            # cleanup:
            if data_files_paths:
                for device, data_files_paths in data_files_paths.items():
                    storage = DeviceStorage(device)
                    with suppress(Exception):
                        for remote_location in data_files_paths[device]:
                            storage.remove(remote_location)

            for app in installed:
                try:
                    app.uninstall()
                except Exception:
                    log.error("Failed to uninstall app %s", app.package_name)

    @staticmethod
    async def _upload(dev: Device, root_path: str) -> List[str]:
        data_files_paths: List[str] = []
        storage = DeviceStorage(dev)
        ext_storage = dev.external_storage_location
        for root, _, files in os.walk(root_path, topdown=True):
            if not files:
                continue
            basedir = os.path.relpath(root, root_path) + "/"
            with suppress(Exception):
                storage.make_dir("/".join([ext_storage, basedir]))
            for filename in files:
                remote_location = "/".join([ext_storage, basedir, filename])
                await storage.push_async(os.path.join(root, filename), remote_location, timeout=5*60)
                data_files_paths.append(remote_location)
        return data_files_paths

    async def install_base(self, dev: Device):
        test_app = await TestApplication.from_apk_async(self._path_to_test_apk, dev)
        target_app = await Application.from_apk_async(self._path_to_apk, dev)
        if self._grant_all_user_permissions:
            target_app.grant_permissions()
            test_app.grant_permissions()
        foreign_apps: List[Application] = []
        for foreign_app_location in self._paths_to_foreign_apks:
            foreign_apps.append(await Application.from_apk_async(foreign_app_location, dev))
        return [test_app, target_app] + foreign_apps
