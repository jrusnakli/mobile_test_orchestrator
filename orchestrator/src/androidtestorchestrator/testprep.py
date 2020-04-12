import asyncio
import os
import logging
import time
from contextlib import suppress

from typing import Dict, List, Optional, Type, Tuple, Union, Sequence
from types import TracebackType
from androidtestorchestrator.device import Device, DeviceSet
from androidtestorchestrator.devicestorage import DeviceStorage
from androidtestorchestrator.application import Application, TestApplication

log = logging.getLogger(__name__)


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

    def __init__(self, devices: Union[Device, DeviceSet]):
        """
        :param device:  device to install and run test app on
        """
        self._devices: DeviceSet = DeviceSet([devices]) if isinstance(devices, Device) else devices
        self._restoration_settings: List[Dict[Tuple[str, str], Optional[str]]] = [{} for _ in self._devices.devices]
        self._restoration_properties: List[Dict[str, Optional[str]]] = [{} for _ in self._devices.devices]
        self._reverse_forwarded_ports: List[int] = []
        self._forwarded_ports: List[int] = []

    def configure_devices(self, settings: Optional[Dict[str, str]] = None,
                          properties: Optional[Dict[str, str]] = None) -> None:
        if settings:
            for setting, value in settings.items():
                ns, key = setting.split(':')
                results = self._devices.apply(Device.set_device_setting, ns, key, value)
                for index, result in enumerate(results):
                    self._restoration_settings[index][(ns, key)] = result
        if properties:
            for property, value in properties.items():
                results = self._devices.apply(Device.set_system_property, property, value)
                for index, result in enumerate(results):
                    self._restoration_properties[index][property] = result

    def verify_network_connection(self, domain: str, count: int = 10, acceptable_loss: int = 3) -> None:
        """
        Verify connection to given domain is active.
        :param domain: address to test connection to
        :param count: number of packets to test
        :raises: IOError on failure to successfully ping given number of packets
        """
        async def run(device: Device) -> Tuple[Device, int]:
            lost_packet_count = device.check_network_connection(domain, count)
            return device, lost_packet_count

        async def gather() -> List[Tuple[Device, int]]:
            return await self._devices.apply_concurrent(run)

        async def timer() -> List[Tuple[Device, int]]:
            return await asyncio.wait_for(gather(), timeout=60)

        results = asyncio.get_event_loop().run_until_complete(timer())
        if all([lost_packets > 0 for (_, lost_packets) in results]):
            raise IOError(
                f"Connection to {domain} failed")
        for device, lost_packets in [(device, lost_packets) for device, lost_packets in results if lost_packets > 0]:
            log.warning(f"Failed connection from device {device.device_id}, with {lost_packets} of {count}" +
                        f" packets lost. Blacklisting device")
            self._devices.blacklist(device)

    def reverse_port_forward(self, device_port: int, local_port: int) -> None:
        """
        reverse forward traffic on remote port to local port

        :param device_port: remote device port to forward
        :param local_port: port to forward to
        """
        for device in self._devices.devices:
            device.reverse_port_forward(device_port=device_port, local_port=local_port)
        self._reverse_forwarded_ports.append(device_port)

    def port_forward(self, local_port: int, device_port: int) -> None:
        """
        forward traffic from local port to remote device port

        :param local_port: port to forward from
        :param device_port: port to forward to
        """
        for device in self._devices.devices:
            device.port_forward(local_port=local_port, device_port=device_port)
        self._forwarded_ports.append(device_port)

    def cleanup(self) -> None:
        """
        Remove all pushed files and uninstall all apps installed by this test prep
        """
        for index, restoration_settings in enumerate(self._restoration_settings):
            for ns, key in restoration_settings:
                with suppress(Exception):
                    self._devices.devices[index].set_device_setting(ns, key, restoration_settings[(ns, key)] or '\"\"')
        for index, restoration_properties in enumerate(self._restoration_properties):
            for prop in restoration_properties:
                with suppress(Exception):
                    self._devices.devices[index].set_system_property(prop, restoration_properties[prop] or '\"\"')
        for port in self._forwarded_ports:
            for device in self._devices.devices:
                try:
                    device.remove_port_forward(port)
                except Exception as e:
                    log.error(f"Failed to remove port forwarding for device {device.device_id} on port {port}: {str(e)}")
        for port in self._reverse_forwarded_ports:
            for device in self._devices.devices:
                try:
                    device.remove_reverse_port_forward(port)
                except Exception as e:
                    log.error(f"Failed to remove reverse port forwarding for device {device.device_id}:"
                              + f"on port {port}: {str(e)}")

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

    def __init__(self, devices: Union[Device, DeviceSet], path_to_apk: str, path_to_test_apk: str,
                 grant_all_user_permissions: bool = True):
        """

        :param devices:  single Device or DeviceSet to install and run test app on
        :param path_to_apk: Path to apk bundle for target app
        :param path_to_test_apk: Path to apk bundle for test app
        :param grant_all_user_permissions: If True, grant all user permissions defined in the manifest of the app and
          test app (prevents pop-ups from occurring on first request for a user permission that can interfere
          with tests)
        """
        self._devices = DeviceSet([devices]) if isinstance(devices, Device) else devices
        self._data_files_paths: List[str] = []

        async def install_apks(device: Device) -> Optional[Tuple[Application, TestApplication]]:
            target_app = None
            test_app = None
            try:
                # install one app to a device at a time, so serial on installs here:
                target_app = await Application.from_apk_async(path_to_apk, device=device)
                test_app = await TestApplication.from_apk_async(path_to_test_apk, device=device)
                return target_app, test_app
            except Exception:
                if target_app:
                    with suppress(Exception):
                        target_app.uninstall()
                if test_app:
                    with suppress(Exception):
                        test_app.uninstall()
                self._devices.blacklist(device)
                return None

        async def gather_apks() -> List[Application]:
            return await asyncio.wait_for(self._devices.apply_concurrent(install_apks, max_concurrent=3),
                                          timeout=5*60)

        app_pairs = asyncio.get_event_loop().run_until_complete(gather_apks())
        self._target_apps = [pair[0] for pair in app_pairs if pair is not None]
        self._test_apps = [pair[1] for pair in app_pairs if pair is not None]
        if not self._test_apps:
            raise Exception("Failed to install test app on all devices.  Giving up")
        if not self._target_apps:
            raise Exception("Failed to install app on all devices.  Giving up")

        self._storage = [DeviceStorage(device) for device in self._devices.devices]
        if grant_all_user_permissions:
            for test_app in self._test_apps:
                test_app.grant_permissions()
            for target_app in self._target_apps:
                target_app.grant_permissions()
        self._installed = []

    @property
    def test_apps(self) -> List[TestApplication]:
        return self._test_apps

    @property
    def target_apps(self) -> List[Application]:
        return self._target_apps

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
            raise IOError(f"Given path {root_path} to upload to device does exist or is not a directory")

        async def upload(device: Device) -> None:
            storage = DeviceStorage(device)
            ext_storage = device.external_storage_location
            for root, _, files in os.walk(root_path, topdown=True):
                if not files:
                    continue
                basedir = os.path.relpath(root, root_path) + "/"
                with suppress(Exception):
                    storage.make_dir("/".join([ext_storage, basedir]))
                for filename in files:
                    remote_location = "/".join([ext_storage, basedir, filename])
                    await storage.push_async(os.path.join(root, filename), remote_location, timeout=5*60)
                    self._data_files_paths.append(remote_location)

        async def gather() -> None:
            await self._devices.apply_concurrent(upload)

        asyncio.get_event_loop().run_until_complete(gather())
        milliseconds = (time.time() - start) * 1000
        return milliseconds

    def setup_foreign_apps(self, paths_to_foreign_apks: List[str]) -> List[Application]:
        """
        Install other apps (outside of test app and app under test) in support of testing.
        A device will be blacklisted and unused in this set of devices if is fails to install

        :param paths_to_foreign_apks: string list of paths to the apks to be installed

        :raises Exception: if any apk fails to install across all devices
        """
        async def install(device: Device, path_to_apk: str) -> Optional[Application]:
            try:
                return await Application.from_apk_async(path_to_apk, device=device)
            except Exception:
                log.exception(f"Failed to install {path_to_apk} on {device.device_id}")
                self._devices.blacklist(device)
                return None

        async def gather() -> List[Application]:
            installed: List[Application] = []
            for path in paths_to_foreign_apks:
                apps = await self._devices.apply_concurrent(install, path)
                if not apps:
                    raise Exception("Failed to install foreign apk on any device.  Giving up")
                installed += [app for app in apps if app is not None]
                self._installed += [app for app in apps if app is not None]
            return installed

        async def timer() -> List[Application]:
            return await asyncio.wait_for(gather(), timeout=5*60*len(paths_to_foreign_apks))

        return asyncio.get_event_loop().run_until_complete(timer())

    def cleanup(self) -> None:
        """
        Remove all pushed files and uninstall all apps installed by this test prep
        """
        if self._data_files_paths:
            for device in self._devices.devices:
                storage = DeviceStorage(device)
                with suppress(Exception):
                    for remote_location in self._data_files_paths:
                        storage.remove(remote_location)

        for app in self._installed + self._target_apps + self._test_apps:
            try:
                app.uninstall()
            except Exception:
                log.error("Failed to uninstall app %s", app.package_name)
