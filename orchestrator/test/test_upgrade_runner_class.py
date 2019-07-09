import os
import logging
import pytest

import support

from androidtestorchestrator import Device
from androidtestorchestrator.reporting import TestListener
from androidtestorchestrator.runners.upgradetestrunner import UpgradeTestRunner

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)


class UpgradeTestListener(TestListener):

    def test_ended(self, test_name: str, test_class: str, test_no: int, duration: float, msg: str = "") -> None:
        log.info(f"Test {test_name} has ended: {msg}")

    def test_suite_started(self, test_suite_name: str) -> None:
        log.info(f"Test suite {test_suite_name} has started")

    def test_suite_ended(self, test_suite_name: str, test_count: int, execution_time: float) -> None:
        log.info(f"Test suite {test_suite_name} has ended")

    def test_suite_errored(self, test_suite_name: str, status_code: int, exc_message: str = "") -> None:
        log.error(f"Test suite errored: {test_suite_name}")

    def test_failed(self, test_name: str, test_class: str, test_no: int, stack: str, msg: str = "") -> None:
        log.error(f"Test {test_name} failed: {msg}")

    def test_assumption_violated(self, test_name: str, test_class: str, test_no: int, reason: str) -> None:
        log.error(f"Test '{test_name}': assumption violated: {reason}")

    def test_ignored(self, test_name: str, test_class: str, test_no: int, msg: str = "") -> None:
        pass


reporter = UpgradeTestListener()


# noinspection PyShadowingNames
class TestUpgradeRunner:

    def test_setup_duplicate_exception(self, device: Device, support_app: str):
        utr = UpgradeTestRunner(device, support_app, [support_app, support_app], reporter)
        assert utr.setup() is False

    def test_setup_success(self, device: Device, support_app: str):
        utr = UpgradeTestRunner(device, support_app, [support_app], reporter)
        assert utr.setup() is True

    def test_execution(self, device: Device, support_app: str):
        utr = UpgradeTestRunner(device, support_app, [support_app], reporter)
        try:
            utr.execute()
        except Exception as e:
            log.error(f"Exception found in execute: {str(e)}")
