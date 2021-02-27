import datetime
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from types import TracebackType
from typing import Any, Dict, Optional, Type, List, Tuple


class TestStatus(Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    IGNORED = "IGNORED"
    ASSUMPTION_FAILURE = "ASSUMPTION_FAILURE"
    INCOMPLETE = "INCOMPLETE"

    def __repr__(self) -> str:
        return self.value


@dataclass(frozen=True)
class TestSuite:
    """
    A dataclass representing a test suite
    """
    name: str
    "unique name for this test suite"
    arguments: List[str] = field(default_factory=list)
    """optional direct arguments to be passed to the am instrument command, such as
        "am instrument -w -r <<arguments>> <package>/<runner> """
    test_parameters: Dict[str, str] = field(default_factory=dict)
    """optional test parameters to be passed to the am instrument command under the "-e" option, such as
        "am instrument -w -r <<"-e" key value for key, value in arguments>> <package>/<runner> """
    uploadables: List[Tuple[str, str]] = field(default_factory=list)
    "optional list of tuples of (loacl_path, remote_path) of test vector files to be uploaded to remote device"
    clean_data_on_start: bool = field(default_factory=lambda: False)
    "whether to clean user data and re-grant permissions before executing this test"


class TestSuiteListener(ABC):

    @abstractmethod
    def test_suite_started(self, test_run: TestSuite) -> None:
        """
        Signal given test_run (suite) has started
        :param test_run:
        """

    @abstractmethod
    def test_suite_failed(self, test_run: TestSuite, error_message: str) -> None:
        """
        Signal given test_run (suite) has ended
        :param test_run:
        :param error_message: error message from failure output
        """

    @abstractmethod
    def test_suite_ended(self, test_run: TestSuite, duration: float = -1.0) -> None:
        """
        Signal given test_run (suite) has ended
        :param test_run:
        :param duration: how long the test took, or -1.0 if unknown
        """


class TestRunListener(TestSuiteListener):
    """
    Abstraction for reporting test status (coming from InstrumentationOutputParser)

    Clients implement this to receive live test status as they are executed.
    """

    def __init__(self) -> None:
        """
        """
        # having constructor prevents pytest from picking this up ! :-(

    def test_suite_started(self, test_run: TestSuite) -> None:
        # default is to defer to legacy method
        self.test_run_started(test_run.name)

    def test_suite_failed(self, _: TestSuite, error_message: str) -> None:
        # default is to defer to legacy method
        self.test_run_failed(error_message)

    def test_suite_ended(self, test_run: TestSuite, duration: float = -1.0) -> None:
        # default is to defer to legacy method
        self.test_run_ended(duration)

    @abstractmethod
    def test_run_started(self, test_run_name: str, count: int = 0) -> None:
        """
        signals test suite has started
        :param test_run_name: name of test run
        :param count: (optional) number of tests expected to run
        """

    @abstractmethod
    def test_run_ended(self, duration: float = -1.0,
                       **kwargs: Optional[Any]) -> None:
        """
        signals test suite has ended
        :param duration: device-reported elapsed time
        :param kwargs: additional data to store with this test run
        """

    @abstractmethod
    def test_run_failed(self, error_message: str) -> None:
        """
        Reports test run failed to complete due to a fatal error.
        :param error_message: description of reason for run failure
        """

    @abstractmethod
    def test_failed(self, class_name: str, test_name: str, stack_trace: str) -> None:
        """
        signals test failure
        :param class_name: fully qualified class name of the test
        :param test_name: name of the test
        :param stack_trace: a stack trace of the failure cause
        """

    @abstractmethod
    def test_ignored(self, class_name: str, test_name: str) -> None:
        """
        signals test was ignored
        :param class_name: fully qualified class name of the test
        :param test_name: name of the test
        """

    @abstractmethod
    def test_assumption_failure(self, class_name: str, test_name: str, stack_trace: str) -> None:
        """
        signal test assumption was violated and test was skipped since platform did not support it
        :param class_name: fully qualified class name of the test
        :param test_name: name of the test
        :param stack_trace: a stack trace of the assumption failure cause
        """

    @abstractmethod
    def test_started(self, class_name: str, test_name: str) -> None:
        """
        signal test has started
        :param class_name: fully qualified class name of the test
        :param test_name: name of the test
        """

    @abstractmethod
    def test_ended(self, class_name: str, test_name: str, **kwargs: Optional[Any]) -> None:
        """
        signal test has ended, presumably with success
        :param class_name: fully qualified class name of the test
        :param test_name: name of the test
        :param kwargs: additional data to store with this test
        """

    def observing_test(self, class_name: str, test_name: str) -> 'TestResultContextManager':
        """
        Taken from mdl-integration. Creates a context manager to wrap test execution for reporting. By default, exiting
        the context manager marks the test as passed, unless an exception was raised, in which case the test is marked
        as failed with that exception's message. This may be overridden by calling "test_failed" (or other similar
        methods) manually on the context manager.
        :param class_name: fully qualified class name of the test
        :param test_name: name of the test
        :return: A TestResultContextManager
        """
        return TestResultContextManager(self, class_name, test_name)


class TestResult(object):
    """
    Result of an individual test run.
    """

    def __init__(self) -> None:
        self.status: TestStatus = TestStatus.INCOMPLETE
        self.start_time: datetime.datetime = datetime.datetime.utcnow()
        self.end_time: Optional[datetime.datetime] = None
        self.stack_trace: Optional[str] = None
        self.data: Dict[str, Any] = {}

    @property
    def duration(self) -> float:
        if self.end_time is not None and self.start_time is not None:
            return (self.end_time - self.start_time).total_seconds()
        return 0.0

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
        self.status = TestStatus.ASSUMPTION_FAILURE
        self.stack_trace = stack_trace

    def ignored(self) -> None:
        """
        Marks this test as ignored (skipped)
        """
        self.status = TestStatus.IGNORED

    def ended(self, **kwargs: Any) -> None:
        """
        Marks the end of the test. If not failed or ignored, test is marked as passed.
        :param kwargs: Extra data to store with this test result
        """
        if self.status == TestStatus.INCOMPLETE:
            self.status = TestStatus.PASSED
        self.end_time = datetime.datetime.utcnow()
        self.data = kwargs

    def __repr__(self) -> str:
        return self.__class__.__name__ + str(self.__dict__)


class TestResultContextManager(object):
    """
    A context manager for test result reporting. Shortcut for:

        listener.test_started(class_name, test_name)
        try:
            <context body>
        except Exception as e:
            listener.test_failed(class_name, test_name, str(e))
            raise
        finally:
            listener.test_ended(class_name, test_name)
    """

    def __init__(self, listener: TestRunListener, class_name: str, test_name: str):
        self._listener = listener
        self._class_name = class_name
        self._test_name = test_name

    def __enter__(self) -> 'TestResultContextManager':
        self._listener.test_started(self._class_name, self._test_name)
        return self

    def __exit__(self, exc_type: Optional[Type[BaseException]], exc_val: Optional[BaseException],
                 exc_tb: Optional[TracebackType]) -> None:
        if exc_type is not None:
            self.test_failed(str(exc_val))
        self._listener.test_ended(self._class_name, self._test_name)

    def test_failed(self, stack_trace: str) -> None:
        self._listener.test_failed(self._class_name, self._test_name, stack_trace)

    def test_assumption_failure(self, stack_trace: str) -> None:
        self._listener.test_assumption_failure(self._class_name, self._test_name, stack_trace)

    def test_ignored(self) -> None:
        self._listener.test_ignored(self._class_name, self._test_name)


class TestRunResult(TestRunListener):
    """
    Result of a whole test run.

    Base implementation of TestRunListener that collects results into a dictionary, and extracts need for timing and
    result collection operations away from test methods.
    """

    def __init__(self) -> None:
        super().__init__()
        self.test_run_name: str = "not started"
        self.duration: float = 0.0
        self.start_time: Optional[datetime.datetime] = None
        self.end_time: Optional[datetime.datetime] = None
        self.error_message: str = ""
        self.test_results: Dict[TestId, TestResult] = {}
        self.data: Dict[str, Any] = {}

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

    def test_run_started(self, test_run_name: str, count: int = 0) -> None:
        self.test_run_name = test_run_name
        self.duration = 0
        self.start_time = datetime.datetime.utcnow()
        self.end_time = None
        self.error_message = ""

    def test_run_ended(self, duration: float = -1.0, **kwargs: Optional[Any]) -> None:
        if self.start_time is None:
            raise Exception("test_run_ended called before calling test_run_started")
        self.end_time = datetime.datetime.utcnow()
        self.duration += duration if duration != -1.0 \
            else (self.end_time - self.start_time).total_seconds()
        self.data = kwargs

    def test_run_failed(self, error_message: str) -> None:
        self.error_message = error_message

    def test_failed(self, class_name: str, test_name: str, stack_trace: str) -> None:
        self._get_test_result(class_name, test_name).failed(stack_trace)

    def test_ignored(self, class_name: str, test_name: str) -> None:
        self._get_test_result(class_name, test_name).ignored()

    def test_assumption_failure(self, class_name: str, test_name: str, stack_trace: str) -> None:
        self._get_test_result(class_name, test_name).assumption_failure(stack_trace)

    def test_started(self, class_name: str, test_name: str) -> None:
        self.test_results[TestId(class_name, test_name)] = TestResult()

    def test_ended(self, class_name: str, test_name: str, **kwargs: Optional[Any]) -> None:
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
        return self.__class__.__name__+str(self.__dict__)


@dataclass(frozen=True)
class TestId(object):
    """
    A test identifier. Used as a key for test results.
    """
    class_name: Optional[str]
    test_name: str


@dataclass(frozen=True)
class TestRunArtifact(object):
    """
    A generic test run artifact, consisting of the file's relative path and its content in bytes
    """
    path: str
    content: bytes
