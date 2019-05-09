from abc import ABC, abstractmethod
from enum import Enum


class TestStatus(Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    ASSUMPTION_VIOLATED = "ASSUMPTION_VIOLATED"

    def __repr__(self):
        return self.value


class TestListener(ABC):
    """
    Absrtaction for reporting test status (coming from InstrumentationOutputParser)

    Clients implement this to receive live test status as they are executed.
    """

    def __init__(self):
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
    def test_ended(self, test_name: str, test_class: str, test_no: int, duration: float, msg: str = "") -> None:
        """
        signal test has ended, presumably with success
        :param test_name: name of test that has ended
        :param test_class: class name of test
        :param test_no: test # in sequence of execution
        :param duration: time to execute the test
        :param msg: general message is any, else empty
        """
