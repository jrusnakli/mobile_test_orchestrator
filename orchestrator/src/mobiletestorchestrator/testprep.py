import os
import logging
from contextlib import suppress, asynccontextmanager
from dataclasses import dataclass, field

from typing import Dict, List, Optional, Tuple, FrozenSet, AsyncIterator, Any
from mobiletestorchestrator.device import Device
from mobiletestorchestrator.device_networking import AsyncDeviceConnectivity
from mobiletestorchestrator.device_storage import AsyncDeviceStorage
from mobiletestorchestrator.application import TestApplication, AsyncApplication, AsyncTestApplication

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeviceSetup:
    """
    Class used to prepare a device for test execution, including installing app, configuring settings/properties, etc.
    The API provides a way to setup the configuration that will later be applied by the client across one or many
    devices.  The client calls 'apply' as a context manager to apply the configuration to a fiven device, ensuring
    restoration of original settings and properties upon exit

    Preferably, use the Builder inner class to construct this
    """

    """local/remote ports to be configured for reverse-forwarding"""
    reverse_forwarded_ports: FrozenSet[Tuple[int, int]]
    """local/remote ports to be configured for fowrarding"""
    forwarded_ports: FrozenSet[Tuple[int, int]]
    """requested Android settings to be changed on device for duration of testing"""
    requested_settings: FrozenSet[Tuple[str, str]]
    """requested Android properties to be changed on device for duration of testing"""
    requested_properties: FrozenSet[Tuple[str, str]]
    """Any domains to verify network connection to (NOTE: uses ping from the device)"""
    verify_network_domains: FrozenSet[Tuple[str, int, int]]

    class Builder:
        """
        Convenience class used to build a DevicePreparation instance
        """
        def __init__(self):
            self._reverse_forwarded_ports: Dict[int, int] = {}
            self._forwarded_ports: Dict[int, int] = {}
            self._requested_settings: Dict[str, str] = {}
            self._requested_properties: Dict[str, str] = {}
            self._verify_network_domains: List[Tuple[str, int, int]] = []

        def configure_settings(self,
                               settings: Optional[Dict[str, str]] = None,
                               properties: Optional[Dict[str, str]] = None) -> "DeviceSetup.Builder":
            """
            Configure settings and properties of the device

            :param settings: Android device setting key/value
            :param properties: Android property setting of key (in form of namespace:key) and value pairs
            :return: this instance
            """
            self._requested_settings = settings or {}
            self._requested_properties = properties or {}
            return self

        def verify_network_connection(self, domain: str, count: int = 10,
                                      acceptable_loss: int = 3) -> "DeviceSetup.Builder":
            """
            Verify connection to given domain is active.

            :param domain: address to test connection to
            :param count: number of packets to test
            :param acceptable_loss: allowed number of packets to be dropped

            :raises: IOError on failure to successfully ping given number of packets
            """
            self._verify_network_domains.append((domain, count, acceptable_loss))
            return self

        def reverse_port_forward(self, device_port: int, local_port: int) -> "DeviceSetup.Builder":
            """
            reverse forward traffic on remote port to local port

            :param device_port: remote device port to forward
            :param local_port: port to forward to
            """
            if device_port in self._reverse_forwarded_ports:
                raise Exception("Attempt to reverse-forward and already reverse-forwarded port")
            self._reverse_forwarded_ports[device_port] = local_port
            return self

        def port_forward(self, local_port: int, device_port: int) -> "DeviceSetup.Builder":
            """
            forward traffic from local port to remote device port

            :param local_port: port to forward from
            :param device_port: port to forward to
            """
            if device_port in self._forwarded_ports:
                raise Exception("Attempt to forward to same local port")
            self._forwarded_ports[device_port] = local_port
            return self

        def resolve(self) -> "DeviceSetup":
            """
            resolve this Builder into a (frozen) DevicePreparation instance
            :return: (frozen) DevicePreparation instance
            """
            # type checking gets type of items() wrong (as int instead of Tuple[int, int])
            # noinspection PyTypeChecker
            return DeviceSetup(reverse_forwarded_ports=frozenset(self._reverse_forwarded_ports.items()),
                               forwarded_ports=frozenset(self._forwarded_ports.items()),
                               requested_settings=frozenset(self._requested_settings.items()),
                               requested_properties=frozenset(self._requested_properties.items()),
                               verify_network_domains=frozenset(self._verify_network_domains))

    @asynccontextmanager
    async def apply(self, device: Device) -> Any:
        """
        Apply requested settings/configuration to the given device
        :param device: device to apply chnages to
        """
        restoration_settings: Dict[Tuple[str, str], Optional[str]] = {}
        restoration_properties: Dict[str, Optional[str]] = {}
        dev_connectivity = AsyncDeviceConnectivity(device)
        for domain, count, acceptable_loss in self.verify_network_domains:
            lost_packets = await dev_connectivity.check_network_connection(domain, count)
            if lost_packets > acceptable_loss:
                raise IOError(f"Connection to {domain} for device {device.device_id} failed")
        if self.requested_settings:
            for setting, value in self.requested_settings:
                ns, key = setting.split(':')
                result = device.set_device_setting(ns, key, value)
                restoration_settings[(ns, key)] = result
        if self.requested_properties:
            for property_, value in self.requested_properties:
                result = device.set_system_property(property_, value)
                restoration_properties[property_] = result
        for device_port, local_port in self.reverse_forwarded_ports:
            await dev_connectivity.reverse_port_forward(device_port=device_port, local_port=local_port)
        for device_port, local_port in self.forwarded_ports:
            await dev_connectivity.port_forward(local_port=local_port, device_port=device_port)

        yield self

        #####
        # cleanup/restoration:
        #####
        for (ns, key), setting in restoration_settings.items():  # type: ignore
            with suppress(Exception):
                device.set_device_setting(ns, key, setting or '\"\"')
        for prop in restoration_properties:
            with suppress(Exception):
                device.set_system_property(prop, restoration_properties[prop] or '\"\"')
        for device_port, _ in self.reverse_forwarded_ports:
            try:
                await dev_connectivity.remove_reverse_port_forward(device_port)
            except Exception as e:
                log.error(f"Failed to remove reverse port forwarding for device {device.device_id}" +
                          f"on port {device_port}: {str(e)}")
        for device_port, _ in self.forwarded_ports:
            try:
                await dev_connectivity.remove_port_forward(device_port)
            except Exception as e:
                log.error(f"Failed to remove port forwarding for device {device.device_id}:"
                          + f"on port {device_port}: {str(e)}")


@dataclass(frozen=True)
class EspressoTestSetup(DeviceSetup):
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

    """path to target application's apk bundle"""
    path_to_apk: str
    """path to test application's apk bundle"""
    path_to_test_apk: str
    """paths to foreign apk bundles to install in additions to requiest app/test app bundles"""
    paths_to_foreign_apks: FrozenSet[str] = field(default_factory=frozenset)
    """Paths to files to upload, or directories whose contents will be mimicked in upload to external storage"""
    uploadables: FrozenSet[str] = field(default_factory=frozenset)
    """Whether to grant all user permissions on start of tests"""
    grant_all_user_permissions: bool = True

    class Builder(DeviceSetup.Builder):
        """
        Convenience class for building a (frozen) EspressoSetup instance
        """
        def __init__(self, path_to_apk: str, path_to_test_apk: str, grant_all_user_permissions: bool = True):
            """

            :param path_to_apk: Path to apk bundle for target app
            :param path_to_test_apk: Path to apk bundle for test app
            :param grant_all_user_permissions: If True, grant all user permissions defined in the manifest of the app &
              test app (prevents pop-ups from occurring on first request for a user permission that can interfere
              with tests)
            """
            super().__init__()
            self._path_to_apk = path_to_apk
            self._path_to_test_apk = path_to_test_apk
            self._grant_all_user_permissions = grant_all_user_permissions

            self._paths_to_foreign_apks: List[str] = []
            self._uploadables: List[str] = []

        def upload_test_vectors(self, root_path: str) -> "EspressoTestSetup.Builder":
            """
            Upload test vectors to external storage on device for use by tests

            :param root_path: local path that is the root where data files reside;  directory structure will be \
                mimicked below this level
            """
            if not os.path.isdir(root_path):
                raise IOError(f"Given path {root_path} to upload to device does exist or is not a directory")
            self._uploadables.append(root_path)
            return self

        def add_foreign_apks(self, paths_to_apks: List[str]) -> "EspressoTestSetup.Builder":
            """
            :param paths_to_apks:  List of paths to other apks to install
            """
            self._paths_to_foreign_apks = paths_to_apks
            return self

        def resolve(self) -> "EspressoTestSetup":
            # noinspection PyTypeChecker
            return EspressoTestSetup(
                reverse_forwarded_ports=frozenset(self._reverse_forwarded_ports.items()),
                forwarded_ports=frozenset(self._forwarded_ports.items()),
                requested_settings=frozenset(self._requested_settings.items()),
                requested_properties=frozenset(self._requested_properties.items()),
                verify_network_domains=frozenset(self._verify_network_domains),
                path_to_apk=self._path_to_apk,
                path_to_test_apk=self._path_to_test_apk,
                paths_to_foreign_apks=frozenset(self._paths_to_foreign_apks),
                uploadables=frozenset(self._uploadables),
                grant_all_user_permissions=self._grant_all_user_permissions
            )

    @asynccontextmanager
    async def apply(self, device: Device) -> AsyncIterator[TestApplication]:
        installed: List[AsyncApplication] = []
        data_files_paths: Dict[Device, List[str]] = {}
        try:
            for path in self.uploadables:
                data_files_paths[device] = await self._upload(device, path)

            async with super().apply(device):
                installed = await self._install_all(device)
                yield installed[0]  # type: ignore
        except Exception as e:
            log.exception(e)
            raise
        finally:
            # cleanup:
            if data_files_paths:
                for device in data_files_paths:
                    storage = AsyncDeviceStorage(device)
                    with suppress(Exception):
                        for remote_location in data_files_paths[device]:
                            await storage.remove(remote_location)
            for app in installed:
                try:
                    await app.uninstall()
                except Device.CommandExecutionFailure:
                    log.error("Failed to uninstall app %s", app.package_name)

    @staticmethod
    async def _upload(dev: Device, root_path: str) -> List[str]:
        data_files_paths: List[str] = []
        storage = AsyncDeviceStorage(dev)
        ext_storage = dev.external_storage_location
        for root, _, files in os.walk(root_path, topdown=True):
            if not files:
                continue
            basedir = os.path.relpath(root, root_path) + "/"
            with suppress(Exception):
                await storage.make_dir("/".join([ext_storage, basedir]))
            for filename in files:
                remote_location = "/".join([ext_storage, basedir, filename])
                await storage.push(os.path.join(root, filename), remote_location, timeout=5*60)
                data_files_paths.append(remote_location)
        return data_files_paths

    async def _install_all(self, dev: Device) -> List[AsyncApplication]:
        installed: List[AsyncApplication] = []
        foreign_apps: List[AsyncApplication] = []
        try:
            test_app = await AsyncTestApplication.from_apk(self.path_to_test_apk, dev)
            installed.append(test_app)
            target_app = await AsyncApplication.from_apk(self.path_to_apk, dev)
            installed.append(target_app)
            if self.grant_all_user_permissions:
                target_app.grant_permissions()
                test_app.grant_permissions()
            for foreign_app_location in self.paths_to_foreign_apks:
                foreign_apps.append(await AsyncApplication.from_apk(foreign_app_location, dev))
            return installed + foreign_apps
        except Exception:
            for app in installed + foreign_apps:
                with suppress(Exception):
                    await app.uninstall()
            raise
