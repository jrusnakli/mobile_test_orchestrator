import datetime
import shutil
from contextlib import suppress
from typing import Optional, Dict, Any

import pytest

from androidtestorchestrator.device import Device
from androidtestorchestrator.reporting import TestExecutionListener, TestStatus, TestId, TestResult
from androidtestorchestrator.runners.upgradetestrunner import UpgradeTestException, UpgradeTestRunner


class TestPlanExecutionReport(TestExecutionListener):
    """
    Report containing test results of a whole test plan execution

    Hierarchy is:

    TestPlanExecutionReport (report on a collection of test runs)
    |
    ---> TestRunResult  (collection of test results)
         |
         ---> TestResult  (single test result)
    """

    class TestRunResult:
        """
        Result of a single test run (within a larger plan)
        """

        def __init__(self, test_run_name: str):
            self.test_run_name: str = test_run_name
            self.duration: float = 0.0
            self.start_time: Optional[datetime.datetime] = None
            self.end_time: Optional[datetime.datetime] = None
            self.error_message: str = ""
            self.test_results: Dict[TestId, TestResult] = {}
            self.data: Dict[str, Any] = {}
            self._test_run_results: Dict[str, TestResult] = {}

        @property
        def is_complete(self) -> bool:
            """:return: True iff test_run_ended has been called"""
            return self.end_time is not None

        @property
        def is_failed(self) -> bool:
            """:return: True iff test_run_failed has been called"""
            return self.error_message != ""

        def test_count(self, status: Optional[TestStatus] = None) -> int:
            """
            :param status: A TestResult status (PASSED, FAILED, etc). If not specified, returns the total number of tests.
            :return: the number of tests with the given status
            """
            if status is None:
                return len(self.test_results)
            return sum(1 for result in self.test_results.values() if result.status == status)

        def test_run_started(self,  test_run_name: str, count: int = 0) -> None:
            self.duration = 0
            self.start_time = datetime.datetime.utcnow()
            self.end_time = None
            self.error_message = ""

        def test_run_ended(self, test_run_name: str, duration: float = -1.0, **kwargs: Optional[Any]) -> None:
            if self.start_time is None:
                raise Exception("test_run_ended called before calling test_run_started")
            self.end_time = datetime.datetime.utcnow()
            self.duration += duration if duration != -1.0 \
                else (self.end_time - self.start_time).total_seconds()
            self.data = kwargs

        def test_run_failed(self, test_run_name: str, error_message: str) -> None:
            self.error_message = error_message

        def test_failed(self, test_run_name: str, class_name: str, test_name: str, stack_trace: str) -> None:
            self._get_test_result(class_name, test_name).failed(stack_trace)

        def test_ignored(self, test_run_name: str, class_name: str, test_name: str) -> None:
            self._get_test_result(class_name, test_name).ignored()

        def test_assumption_failure(self, test_run_name: str, class_name: str, test_name: str, stack_trace: str) -> None:
            self._get_test_result(class_name, test_name).assumption_failure(stack_trace)

        def test_started(self, test_run_name: str, class_name: str, test_name: str) -> None:
            self.test_results[TestId(class_name, test_name)] = TestResult()

        def test_ended(self, test_run_name: str, class_name: str, test_name: str, **kwargs: Optional[Any]) -> None:
            result = self.test_results.setdefault(TestId(class_name, test_name), TestResult())
            result.ended(**kwargs)

        def _get_test_result(self, class_name: str, test_name: str) -> TestResult:
            test_id = TestId(class_name, test_name)
            result = self.test_results.get(test_id, None)
            if result is None:
                # TODO: Should we add any output here?
                result = TestResult()
                self.test_results[test_id] = result
            return result

        def __repr__(self) -> str:
            return self.__class__.__name__ + str(self.__dict__)

    def __init__(self) -> None:
        super().__init__()
        self._test_run_results: Dict[str, TestPlanExecutionReport.TestRunResult] = {}

    def test_count(self, status: Optional[TestStatus] = None) -> int:
        """
        :param status: A TestResult status (PASSED, FAILED, etc). If not specified, returns the total number of tests.
        :return: the number of tests with the given status
        """
        if status is None:
            return sum([len(run.test_results) for run in self._test_run_results.values()])
        return sum([sum(1 for result in run.test_results.values() if result.status == status) for
                    run in self._test_run_results.values()])

    def _test_run_result(self, test_run_name: str) -> "TestPlanExecutionReport.TestRunResult":
        """
        :param test_run_name: which test run

        :return: TestRunResult for the given test_run_name
        """
        return self._test_run_results.setdefault(test_run_name, self.TestRunResult(test_run_name))

    def test_suite_started(self, test_run_name: str, count: int = 0) -> None:
        self._test_run_result(test_run_name).test_run_started(count)

    def test_suite_ended(self, test_run_name: str, duration: float = -1.0, **kwargs: Optional[Any]) -> None:
        self._test_run_result(test_run_name).test_run_ended(duration, **kwargs)

    def test_suite_failed(self, test_run_name: str, error_message: str) -> None:
        self._test_run_result(test_run_name).error_message = error_message

    def test_failed(self, test_run_name: str, class_name: str, test_name: str, stack_trace: str) -> None:
        self._test_run_result(test_run_name)._get_test_result(class_name, test_name).failed(stack_trace)

    def test_ignored(self, test_run_name: str, class_name: str, test_name: str) -> None:
        self._test_run_result(test_run_name)._get_test_result(class_name, test_name).ignored()

    def test_assumption_failure(self, test_run_name: str, class_name: str, test_name: str, stack_trace: str) -> None:
        self._test_run_result(test_run_name)._get_test_result(class_name, test_name).assumption_failure(stack_trace)

    def test_started(self, test_run_name: str, class_name: str, test_name: str) -> None:
        self._test_run_result(test_run_name).test_results[TestId(class_name, test_name)] = TestResult()

    def test_ended(self, test_run_name: str, class_name: str, test_name: str, **kwargs: Optional[Any]) -> None:
        result = self._test_run_result(test_run_name).test_results.setdefault(TestId(class_name, test_name), TestResult())
        result.ended(**kwargs)

    def test_run_result(self, test_run_name: str) -> Optional["TestPlanExecutionReport.TestRunResult"]:
        return self._test_run_results.get(test_run_name)

    @property
    def is_complete(self) -> bool:
        """
        :return: whether all test runs completed properly
        """
        return all([test_run_result.is_complete for test_run_result in self._test_run_results.values()])

    @property
    def is_failed(self) -> bool:
        """
        :return: whether any test run failed
        """
        return any([test_run_result.is_failed for test_run_result in self._test_run_results.values()])

    def __repr__(self) -> str:
        return self.__class__.__name__ + str(self.__dict__)


reporter = TestPlanExecutionReport()


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
