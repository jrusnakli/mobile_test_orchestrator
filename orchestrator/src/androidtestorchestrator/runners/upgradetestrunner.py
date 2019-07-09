import logging
import os
import sys
import time

from apk_bitminer.parsing import AXMLParser  # type: ignore
from collections import defaultdict
from pipes import quote
from typing import List

from androidtestorchestrator.device import Device
from androidtestorchestrator.application import Application
from androidtestorchestrator.reporting import TestListener

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)


class UpgradeTestRunner(object):

    TEST_SCREENSHOTS_FOLDER = "screenshots"

    def __init__(self, device: Device, base_apk: str, upgrade_apks: List[str], test_listener: TestListener):
        if not os.path.exists(self.TEST_SCREENSHOTS_FOLDER):
            os.makedirs(self.TEST_SCREENSHOTS_FOLDER)
        self._device = device
        self._upgrade_reporter = test_listener
        self._upgrade_apks = upgrade_apks
        self._upgrade_test = UpgradeTest(device, base_apk, test_listener)

    def setup(self):
        """
        Setup device and do pre-checks for upgrade test.
        Ensure upgrade APKs list contains unique entries.
        :return: bool (True == setup success, False == setup failure)
        """
        def apk_info(apk_file_name):
            attrs = {attr.name: attr.value for attr in AXMLParser.parse(apk_file_name).xml_head.attributes}
            return attrs.get('package'), attrs.get('versionName')

        seen_apks = defaultdict(list)
        for apk in self._upgrade_apks:
            package, version = apk_info(apk)
            if package in seen_apks and version in seen_apks[package]:
                self._upgrade_reporter.test_assumption_violated("Upgrade setup", "UpgradeTestRunner", 1,
                                                                f"APK with package: {package} with version: {version} "
                                                                f"already found in upgrade apk list.")
                return False
            seen_apks[package].append(version)
        return True

    def execute(self):
        """
        Attempt to execute the upgrade test suite for all upgrade_apks
        :return: None
        """
        for upgrade_apk in self._upgrade_apks:
            try:
                self._upgrade_test.test_uninstall_base()
                self._upgrade_test.test_install_base()
                self._upgrade_test.test_upgrade_to_target(upgrade_apk)
            except Exception as e:
                self._upgrade_reporter.test_suite_errored(test_suite_name=f"UpgradeTest-{upgrade_apk}: {str(e)}",
                                                          status_code=None)
            finally:
                self._upgrade_test.test_uninstall_upgrade(upgrade_apk=upgrade_apk)
                self._upgrade_test.test_uninstall_base()

    def teardown(self):
        """
        Tear down device and restore to pre-test conditions
        :return: None
        """
        pass


class UpgradeTest(object):

    def __init__(self, device: Device, base_apk: str, test_listener: TestListener):
        self._device = device
        self._base_apk = base_apk
        self._reporter = test_listener

    def test_uninstall_base(self):
        _name = _get_func_name()
        start = time.perf_counter()
        package = AXMLParser.parse(self._base_apk).package_name
        if package not in self._device.list_installed_packages():
            self._reporter.test_ended(test_name=_name, test_class=self.__class__.__name__, test_no=1,
                                      duration=time.perf_counter() - start,
                                      msg=f"No un-installation needed. Package {package} does not exist on device")
            return
        app = Application(package, self._device)
        app.uninstall()
        if package in self._device.list_installed_packages():
            self._reporter.test_failed(test_name=_name, test_class=self.__class__.__name__, test_no=1, stack="")
            raise Exception(f"Uninstall base package {package} failed")
        self._reporter.test_ended(test_name=_name, test_class=self.__class__.__name__, test_no=1,
                                  duration=time.perf_counter() - start,
                                  msg=f"Un-installation of package {package} successful")

    def test_install_base(self):
        _name = _get_func_name()
        start = time.perf_counter()
        try:
            app = Application.from_apk(apk_path=self._base_apk, device=self._device, as_upgrade=False)
            app.start(activity=".MainActivity")
            if not self._ensure_activity_in_foreground(app.package_name):
                msg = "Unable to start up package within timeout threshold"
                self._reporter.test_failed(test_name=_name, test_class=self.__class__.__name__, test_no=2, stack="",
                                           msg=msg)
                raise Exception(msg)
            time.sleep(1)  # Give the application activity an extra second to actually get to foreground completely
            if not self._take_screenshot(test_case=_name):
                log.warning(f"Unable to take screenshot for test: {_name}")
        except Exception as e:
            self._reporter.test_failed(test_name=_name, test_class=self.__class__.__name__, test_no=2, stack=str(e))
            raise
        self._reporter.test_ended(test_name=_name, test_class=self.__class__.__name__, test_no=2,
                                  duration=time.perf_counter() - start)

    def test_upgrade_to_target(self, target_apk: str):
        _name = _get_func_name()
        start = time.perf_counter()
        try:
            base_package_name = AXMLParser.parse(self._base_apk).package_name
            app = Application.from_apk(apk_path=target_apk, device=self._device, as_upgrade=True)
            if app.package_name != base_package_name:
                msg = f"Target APK package does not match base APK package: {app.package_name}/{base_package_name}"
                self._reporter.test_failed(test_name=_name, test_class=self.__class__.__name__, test_no=3, stack="",
                                           msg=msg)
                raise Exception(msg)
            app.start(activity=".MainActivity")
            if not self._ensure_activity_in_foreground(app.package_name):
                msg = "Unable to start up package within timeout threshold"
                self._reporter.test_failed(test_name=_name, test_class=self.__class__.__name__, test_no=3, stack="",
                                           msg=msg)
                raise Exception(msg)
            time.sleep(1)  # Give the application activity an extra second to actually get to foreground completely
            if not self._take_screenshot(test_case=_name):
                log.warning(f"Unable to take screenshot for test: {_name}")
        except Exception as e:
            self._reporter.test_failed(test_name=_name, test_class=self.__class__.__name__, test_no=3, stack=str(e))
            raise
        self._reporter.test_ended(test_name=_name, test_class=self.__class__.__name__, test_no=3,
                                  duration=time.perf_counter() - start)

    def test_uninstall_upgrade(self, upgrade_apk: str):
        _name = _get_func_name()
        start = time.perf_counter()
        package = AXMLParser.parse(upgrade_apk).package_name
        if package not in self._device.list_installed_packages():
            self._reporter.test_ended(test_name=_name, test_class=self.__class__.__name__, test_no=4,
                                      duration=time.perf_counter() - start)
        app = Application(package, self._device)
        app.stop()
        app.uninstall()
        if package in self._device.list_installed_packages():
            self._reporter.test_failed(test_name=_name, test_class=self.__class__.__name__, test_no=4, stack="")
            raise Exception(f"Uninstall upgrade package {package} failed")
        self._reporter.test_ended(test_name=_name, test_class=self.__class__.__name__, test_no=4,
                                  duration=time.perf_counter() - start)

    def _ensure_activity_in_foreground(self, package_name: str, timeout: int = 5) -> bool:
        count = 0
        while self._device.foreground_activity() != package_name and count < timeout:
            time.sleep(1)
            count += 1
        return self._device.foreground_activity() == package_name

    def _take_screenshot(self, test_case: str, retries: int = 3) -> bool:
        screenshot = test_case + ".png"
        path = os.path.join(UpgradeTestRunner.TEST_SCREENSHOTS_FOLDER, quote(screenshot))
        count = 0
        self._device.take_screenshot(path)
        while not os.path.isfile(path) and count <= retries:
            time.sleep(1)
            self._device.take_screenshot(path)
            count += 1
        return os.path.isfile(path)


def _get_func_name():
    return sys._getframe(1).f_code.co_name