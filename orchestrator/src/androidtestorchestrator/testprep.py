import os
import logging
import time
from contextlib import suppress

from typing import Dict, List, Optional, Type, Tuple, Union
from types import TracebackType
from androidtestorchestrator.device import Device, DeviceSet
from androidtestorchestrator.devicestorage import DeviceStorage
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
                for index, results in enumerate(results):
                    self._restoration_properties[index][property] = result

    def verify_network_connection(self, domain: str, count: int = 10, acceptable_loss: int = 3) -> None:
        """
        Verify connection to given domain is active.
        :param domain: address to test connection to
        :param count: number of packets to test
        :raises: IOError on failure to successfully ping given number of packets
        """
        async def run(device: Device):
            lost_packet_count = device.check_network_connection(domain, count)
            return device, lost_packet_count

        async def gather():
            return await self._devices.apply_concurrent(run)

        async def timer():
            return await asyncio.wait_for(gather(), timeout=60)

        results = asyncio.run(timer())
        if all([lost_packets > 0 for (_, lost_packets) in results]):
            raise IOError(
                f"Connection to {domain} failed; expected {count} packets but got {count - lost_packet_count}")
        for device in [device for device, lost_packets in results if lost_packets > 0]:
            self._devices.blacklist(device)

    def reverse_port_forward(self, device_port: int, local_port: int) -> None:
        """
        reverse forward traffic on remote port to local port

        :param device_port: remote device port to forward
        :param local_port: port to forward to
        """
        self._device.reverse_port_forward(device_port=device_port, local_port=local_port)
        self._reverse_forwarded_ports.append(device_port)

    def port_forward(self, local_port: int, device_port: int) -> None:
        """
        forward traffic from local port to remote device port

        :param local_port: port to forward from
        :param device_port: port to forward to
        """
        self._device.port_forward(local_port=local_port, device_port=device_port)
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
                    self._device.devices[index].set_system_property(prop, restoration_properties[prop] or '\"\"')

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
        self._data_files_path: Optional[str] = None

        async def install_apk(device):
            try:
                return await Application.from_apk_async(path_to_apk, device=device)
            except Exception:
                self._devices.blacklist(device)

        async def gather_apk():
            return await self._devices.apply_concurrent(install_apk, max_concurrent=3)

        async def timer_apk():
            return await asyncio.wait_for(gather_apk(), timeout=5*60)

        self._installed = [app for app in asyncio.run(timer_apk()) if app is not None]
        if not self._installed:
            raise Exception("Failed to install app on all devices.  Giving up")

        async def install_test_apk(device):
            try:
                return await TestApplication.from_apk_async(path_to_test_apk, device=device)
            except Exception:
                self._devices.blacklist(device)

        async def gather_test_apk():
            return await self._devices.apply_concurrent(install_test_apk, max_concurrent=3)

        async def timer_test_apk():
            return await asyncio.wait_for(gather_test_apk(), timeout=5*60)

        self._test_apps = [test_app for test_app in asyncio.run(timer_test_apk()) if test_app is not None]
        if not self._test_apps:
            raise Exception("Failed to install test app on all devices.  Giving up")

        self._installed += self._test_apps
        self._storage = [DeviceStorage(device) for device in self._devices.devices]
        if grant_all_user_permissions:
            for test_app in self._test_apps:
                test_app.grant_permissions()

    @property
    def test_apps(self) -> List[TestApplication]:
        return self._test_apps

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

        async def upload(device: Device):
            storage = DeviceStorage(device)
            basedir = os.path.basename(os.path.abspath(root_path))
            ext_storage = device.external_storage_location
            basedir = '/'.join(ext_storage, basedir)
            storage.make_dir(basedir)
            for root, dir_, files in os.walk(root_path, topdown=True):
                if not os.path.isdir(root_path):
                    raise IOError(f"Given path {root_path} to upload to device does exist or is not a directory")
                for filename in files:
                    remote_location = "/".join([basedir, filename])
                    await storage.push_async(os.path.join(root, filename), remote_location, timeout=5*60)
                    self._data_files_path.append(remote_location)

        async def gather():
            await self._devices.apply_concurrent(upload)

        asyncio.run(gather())
        milliseconds = (time.time() - start) * 1000
        return milliseconds

    def setup_foreign_apps(self, paths_to_foreign_apks: List[str]) -> List[Application]:
        """
        Install other apps (outside of test app and app under test) in support of testing.
        A device will be blacklisted and unused in this set of devices if is fails to install

        :param paths_to_foreign_apks: string list of paths to the apks to be installed

        :raises Exception: if any apk fails to install across all devices
        """
        async def install(device: Device, path_to_apk: str) -> None:
            try:
                return await Application.from_apk_async(path_to_apk, device=device)
            except Exception:
                self._devices.blacklist(device)

        async def gather():
            installed = []
            for path in paths_to_foreign_apks:
                apps = await self._devices.apply_concurrent(install, path)
                if not apps:
                    raise Exception("Failed to install foreign apk on any device.  Giving up")
                installed += [app for app in apps if app is not None]
                self._installed += [app for app in apps if app is not None]
            return installed

        async def timer():
            return await asyncio.wait_for(gather(), timeout=5*60*len(paths_to_foreign_apks))

        return asyncio.run(timer())

    def cleanup(self) -> None:
        """
        Remove all pushed files and uninstall all apps installed by this test prep
        """
        if self._data_files_path:
            for device in self._devices.devices:
                storage = DeviceStorage(device)
                with suppress(Exception):
                    for remote_location in self._data_files_path:
                        storage.remove(remote_location)

        for app in self._installed:
            try:
                app.uninstall()
            except Exception:
                log.error("Failed to uninstall app %s", app.package_name)
