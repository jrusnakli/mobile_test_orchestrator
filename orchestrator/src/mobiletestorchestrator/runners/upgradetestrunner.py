import inspect
import logging
import os
import sys
import time

from apk_bitminer.parsing import AXMLParser  # type: ignore
from collections import defaultdict
from pipes import quote
from typing import Any, Callable, DefaultDict, List, Optional, Tuple

from mobiletestorchestrator.device import Device
from mobiletestorchestrator.application import Application
from mobiletestorchestrator.reporting import TestRunResult

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)


class UpgradeTestException(Exception):
    """
    Custom exception type to signify an exception during an upgrade test
    """
    pass


class UpgradeTestRunner(object):
    """
    Runner for performing the setup, execution and teardown of an upgrade test. The methods are meant to be run in
    order they are written in the class:

    Class initialization -> setup() -> execute() -> teardown()
    """

    def __init__(self, device: Device, apk_under_test: str, upgrade_apks: List[str], test_listener: TestRunResult):
        self._device = device
        self._upgrade_reporter = test_listener
        self._upgrade_apks = upgrade_apks
        self._upgrade_test = UpgradeTest(device, apk_under_test)

    def setup(self) -> None:
        """
        Setup device and do pre-checks for upgrade test.
        Ensure upgrade APKs list contains unique entries.
        """
        def apk_info(apk_file_name: str) -> Tuple[Optional[Any], Optional[Any]]:
            attrs = {attr.name: attr.value for attr in AXMLParser.parse(apk_file_name).xml_head.attributes}
            return attrs.get('package'), attrs.get('versionName')

        seen_apks: DefaultDict[Any, List[Any]] = defaultdict(list)
        for apk in self._upgrade_apks:
            package, version = apk_info(apk)
            if not package:
                e = UpgradeTestException("APK package was unable to be parsed")
                self._upgrade_reporter.test_assumption_failure("UpgradeTestRunner", "Upgrade setup", str(e))
                raise e
            if not version:
                e = UpgradeTestException("APK version was unable to be parsed")
                self._upgrade_reporter.test_assumption_failure("UpgradeTestRunner", "Upgrade setup", str(e))
                raise e
            if package in seen_apks and version in seen_apks[package]:
                e = UpgradeTestException(f"APK with package: {package} with version: {version} already found in "
                                         f"upgrade apk list.")
                self._upgrade_reporter.test_assumption_failure("UpgradeTestRunner", "Upgrade setup", str(e))
                raise e
            seen_apks[package].append(version)

    def execute(self) -> None:
        """
        Attempt to execute the upgrade test suite for all upgrade_apks
        :return: None
        """
        test_suite: List[Callable[..., Any]] = [self._upgrade_test.test_uninstall_base,
                                                self._upgrade_test.test_install_base,
                                                self._upgrade_test.test_upgrade_to_target,
                                                self._upgrade_test.test_uninstall_upgrade,
                                                self._upgrade_test.test_uninstall_base]
        self._upgrade_reporter.test_run_started(test_run_name="UpgradeTest")
        for upgrade_apk in self._upgrade_apks:
            for i, test in enumerate(test_suite):
                self._upgrade_reporter.test_started(class_name=test.__class__.__name__, test_name=test.__name__)
                try:
                    if "upgrade_apk" in inspect.signature(test).parameters.keys():
                        test(upgrade_apk)
                    else:
                        test()
                except UpgradeTestException as e:
                    self._upgrade_reporter.test_run_failed(str(e))
                finally:
                    # TODO: Look into removing requirement for duration, or add default value to interface
                    # since TestRunResult class explicitly keeps track of this elsewhere
                    self._upgrade_reporter.test_ended(class_name=test.__class__.__name__,
                                                      test_name=test.__name__)
        # TODO: What is the test_count useful for here? Need to look into this more
        self._upgrade_reporter.test_run_ended()

    def teardown(self) -> None:
        """
        Tear down device and restore to pre-test conditions
        :return: None
        """
        pass


class UpgradeTest(object):
    """
    Class representing the individual tests that make up a full upgrade test. This class is not meant to be called
    individually. It is meant to be used by the UpgradeTestRunner specifically.
    """

    TEST_SCREENSHOTS_FOLDER = "screenshots"

    def __init__(self, device: Device, apk_under_test: str):
        self._device = device
        self._apk_under_test = apk_under_test
        self._package_name = AXMLParser.parse(self._apk_under_test).package_name

    def test_uninstall_base(self) -> None:
        if self._package_name not in self._device.list_installed_packages():
            return
        app = Application(self._device, {'package_name': self._package_name})
        app.uninstall()
        if self._package_name in self._device.list_installed_packages():
            raise UpgradeTestException(f"Uninstall base package {self._package_name} failed")

    def test_install_base(self) -> None:
        _name = _get_func_name()
        try:
            app = Application.from_apk(apk_path=self._apk_under_test, device=self._device, as_upgrade=False)
            app.start(activity=".MainActivity")
            if not self._ensure_activity_in_foreground(app.package_name):
                raise UpgradeTestException("Unable to start up package within timeout threshold")
            time.sleep(1)  # Give the application activity an extra second to actually get to foreground completely
            if not self._take_screenshot(test_case=_name):
                log.warning(f"Unable to take screenshot for test: {_name}")
        except Exception as e:
            raise UpgradeTestException(str(e))

    def test_upgrade_to_target(self, upgrade_apk: str, startup_sec_timeout: int = 5) -> None:
        _name = _get_func_name()
        try:
            app = Application.from_apk(apk_path=upgrade_apk, device=self._device, as_upgrade=True)
            if app.package_name != self._package_name:
                raise UpgradeTestException(f"Target APK package does not match base APK package: "
                                           f"{app.package_name}/{self._package_name}")
            app.start(activity=".MainActivity")
            if not self._ensure_activity_in_foreground(package_name=app.package_name, timeout=startup_sec_timeout):
                raise UpgradeTestException(f"Unable to start up package within {startup_sec_timeout}s timeout threshold")
            time.sleep(1)  # Give the application activity an extra second to actually get to foreground completely
            if not self._take_screenshot(test_case=_name):
                log.warning(f"Unable to take screenshot for test: {_name}")
        except Exception as e:
            raise UpgradeTestException(str(e))

    def test_uninstall_upgrade(self, upgrade_apk: str) -> None:
        package = AXMLParser.parse(upgrade_apk).package_name
        if package not in self._device.list_installed_packages():
            return
        app = Application(self._device, {'package_name': package})
        app.stop()
        app.uninstall()
        if package in self._device.list_installed_packages():
            raise UpgradeTestException(f"Uninstall upgrade package {package} failed")

    def _create_screenshots_dir(self) -> None:
        if not os.path.exists(self.TEST_SCREENSHOTS_FOLDER):
            os.makedirs(self.TEST_SCREENSHOTS_FOLDER)

    def _ensure_activity_in_foreground(self, package_name: str, timeout: int = 5) -> bool:
        count = 0
        while self._device.foreground_activity() != package_name and count < timeout:
            time.sleep(1)
            count += 1
        return self._device.foreground_activity() == package_name

    def _take_screenshot(self, test_case: str) -> bool:
        self._create_screenshots_dir()
        screenshot = test_case + ".png"
        path = os.path.join(self.TEST_SCREENSHOTS_FOLDER, quote(screenshot))
        self._device.take_screenshot(path)
        return os.path.isfile(path) and os.stat(path).st_size > 0


def _get_func_name() -> str:
    return sys._getframe(1).f_code.co_name
