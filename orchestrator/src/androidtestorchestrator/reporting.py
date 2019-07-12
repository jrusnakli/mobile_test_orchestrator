import datetime
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional


class TestStatus(Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    ASSUMPTION_VIOLATED = "ASSUMPTION_VIOLATED"
    INCOMPLETE = "INCOMPLETE"

    def __repr__(self) -> str:
        return self.value  # type: ignore


class TestListener(ABC):
    """
    Abstraction for reporting test status (coming from InstrumentationOutputParser)

    Clients implement this to receive live test status as they are executed.
    """

    def __init__(self) -> None:
        """
        """
        # having constructor prevents pytest from picking this up ! :-(

    @abstractmethod
    def test_suite_started(self, test_suite_name: str) -> None:
        """
        signals test suite has started
        :param test_suite_name: name of test suite
        """
    @abstractmethod
    def test_suite_ended(self, test_suite_name: str, test_count: int, execution_time: float) -> None:
        """
        signals test suite has ended
        :param test_suite_name: name of test suite
        :param test_count: number of tests executed within test suite
        :param execution_time: time it took to execute test suite
        """

    @abstractmethod
    def test_suite_errored(self, test_suite_name: str, status_code: int, exc_message: str = "") -> None:
        """
        signal a test suite errored (e.g. timed ou)
        :param test_suite_name: name of test suite
        :param status_code: status code of isntrument command if it errored (or None)
        :param exc_message: message of any exception caught or empty
        """

    @abstractmethod
    def test_failed(self, test_name: str, test_class: str, test_no: int, stack: str, msg: str = "") -> None:
        """
        signals test failure
        :param test_name: name of test that failed
        :param test_class: class name of test that failed
        :param test_no: test # in sequence of execution
        :param stack: stack trace, if available, else empty
        :param msg: general message if any, else empty
        """
    @abstractmethod
    def test_ignored(self, test_name: str, test_class: str, test_no: int, msg: str = "") -> None:
        """
        signels test was skipped
        :param test_name: test that was skipped
        :param test_class: class name of test
        :param test_no: test # in sequence of execution
        :param msg: general message if any, else empty
        """

    @abstractmethod
    def test_assumption_violated(self, test_name: str, test_class: str, test_no: int, reason: str) -> None:
        """
        signal test assumption was violated and test was skipped since platform did not support it
        :param test_name: name of test
        :param test_class: class name of test
        :param test_no: test # in sequence of execution
        :param reason: reason for skipping (which assumption was violated)
        """

    @abstractmethod
    def test_started(self, test_name: str, test_class: str, test_no: int, msg: str = "") -> None:
        """
        signal test has started
        :param test_name: name of test that has started
        :param test_class: class name of test
        :param test_no: test # in sequence of execution
        :param msg: general message if any, else empty
        """

    @abstractmethod
    def test_ended(self, test_name: str, test_class: str, test_no: int, duration: float, msg: str = "") -> None:
        """
        signal test has ended, presumably with success
        :param test_name: name of test that has ended
        :param test_class: class name of test
        :param test_no: test # in sequence of execution
        :param duration: time to execute the test
        :param msg: general message is any, else empty
        """


class TestResult(object):
    """
    Result of an individual test run.
    """

    def __init__(self) -> None:
        self.status: TestStatus = TestStatus.INCOMPLETE
        self.start_time: datetime = datetime.datetime.utcnow()
        self.end_time: Optional[datetime] = None
        self.stack_trace: Optional[str] = None
        self.data: Dict[str, Any] = {}

    @property
    def duration(self) -> Any:
        # Returns timedelta, but flake8 complains about it returning 'Any'
        return (self.end_time - self.start_time).total_seconds()

    def failed(self, stack_trace: str) -> None:
        """
        Marks this test as failed
        :param stack_trace: A stack trace for the failure
        """
        self.status = TestStatus.FAILED
        self.stack_trace = stack_trace

    def assumption_failure(self, stack_trace: str) -> None:
        """
        Marks this test as an assumption failure
        :param stack_trace: A stack trace for the assumption violation
        """
        self.status = TestStatus.ASSUMPTION_VIOLATED
        self.stack_trace = stack_trace

    def ignored(self) -> None:
        """
        Marks this test as ignored (skipped)
        """
        self.status = TestStatus.SKIPPED

    def ended(self) -> None:
        """
        Marks the end of the test. If not failed or ignored, test is marked as passed.
        """
        if self.status == TestStatus.INCOMPLETE:
            self.status = TestStatus.PASSED
        self.end_time = datetime.datetime.utcnow()

    def __repr__(self) -> str:
        return self.__class__.__name__ + str(self.__dict__)


class TestRunResult(TestListener):
    """
    Result of a whole test run.

    Base implementation of TestListener that collects results into a dictionary, and extracts need for timing and
    result collection operations away from test methods.
    """

    def __init__(self) -> None:
        super().__init__()
        self.test_suite_name = "not started"
        self.duration = 0
        self.start_time = None
        self.end_time = None
        self.error_message = None
        self.test_results: Dict[TestId, TestResult] = {}

    @property
    def is_complete(self) -> bool:
        """:return: True iff test_suite_ended has been called"""
        return self.end_time is not None

    @property
    def is_failed(self) -> bool:
        """:return: True iff test_suite_failed has been called"""
        return self.error_message is not None

    def test_count(self, status: Optional[TestStatus] = None) -> int:
        """
        :param status: A TestResult status (PASSED, FAILED, etc). If not specified, returns the total number of tests.
        :return: the number of tests with the given status
        """
        if status is None:
            return len(self.test_results)
        return sum(1 for result in self.test_results.values() if result.status == status)

    def test_suite_started(self, test_suite_name: str) -> None:
        self.test_suite_name = test_suite_name
        self.duration = 0
        self.start_time = datetime.datetime.utcnow()
        self.end_time = None
        self.error_message = None

    def test_suite_ended(self, test_suite_name: str, test_count: int, execution_time: float = None) -> None:
        self.end_time = datetime.datetime.utcnow()
        self.duration += execution_time if execution_time is not None \
            else (self.end_time - self.start_time).total_seconds()

    def test_suite_errored(self, test_suite_name: str, status_code: int, exc_message: str = "") -> None:
        self.test_suite_name = test_suite_name
        self.error_message = exc_message

    def test_failed(self, test_name: str, test_class: str, test_no: int, stack: str, msg: str = "") -> None:
        self._get_test_result(test_class, test_name).failed(stack)

    def test_ignored(self, test_name: str, test_class: str, test_no: int, msg: str = "") -> None:
        self._get_test_result(test_class, test_name).ignored()

    def test_assumption_violated(self, test_name: str, test_class: str, test_no: int, reason: str) -> None:
        self._get_test_result(test_class, test_name).assumption_failure(reason)

    def test_started(self, test_name: str, test_class: str, test_no: int, msg: str = "") -> None:
        self.test_results[TestId(test_class, test_name)] = TestResult()

    def test_ended(self, test_name: str, test_class: str, test_no: int, duration: float, msg: str = "") -> None:
        result = self.test_results.setdefault(TestId(test_class, test_name), TestResult())
        result.ended()

    def _get_test_result(self, class_name: str, test_name: str) -> TestResult:
        test_id = TestId(class_name, test_name)
        result = self.test_results.get(test_id, None)
        if result is None:
            # TODO: Should we add any output here?
            result = TestResult()
            self.test_results[test_id] = result
        return result


@dataclass(frozen=True)
class TestId(object):
    """
    A test identifier. Used as a key for test results.
    """
    class_name: Optional[str]
    test_name: str
