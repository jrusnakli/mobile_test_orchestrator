import shutil
from contextlib import suppress

import pytest

from mobiletestorchestrator import Device
from mobiletestorchestrator.reporting import TestRunResult
from mobiletestorchestrator.runners.upgradetestrunner import UpgradeTestException, UpgradeTestRunner

reporter = TestRunResult()


class TestUpgradeRunner:

    def test_setup_duplicate_exception(self, device: Device, support_app: str):
        utr = UpgradeTestRunner(device, support_app, [support_app, support_app], reporter)
        with pytest.raises(UpgradeTestException) as excinfo:
            utr.setup()
        assert "already found in upgrade apk list" in str(excinfo.value)

    def test_setup_success(self, device: Device, support_app: str):
        utr = UpgradeTestRunner(device, support_app, [support_app], reporter)
        utr.setup()

    def test_execution(self, device: Device, support_app: str):
        with suppress(Exception):
            shutil.rmtree("screenshots")
        utr = UpgradeTestRunner(device, support_app, [support_app], reporter)
        utr.execute()
        assert reporter.is_complete
        assert not reporter.is_failed
