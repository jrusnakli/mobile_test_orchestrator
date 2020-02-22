"""
Package that provides the constructs and the interface for reporting test status back to client
"""
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
    # TODO: this belongs with the execution interface
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


class TestExecutionListener(ABC):
    """
    Abstraction for reporting test status (coming from InstrumentationOutputParser)

    Clients implement this to receive live test status as they are executed.

    NOTE: the interface considers parallel test execution, so every call explicitly calls
    out test run name, test name, etc. where needed.  This makes the API a little heavier,
    but free of entanglements of temporal cohesion in the face of parallel test execution
    """

    def __init__(self) -> None:
        """
        """
        # having constructor prevents pytest from picking this up ! :-(

    @abstractmethod
    def test_suite_started(self, test_run_name: str, count: int = 0) -> None:
        """
        signals test suite has started

        :param test_run_name: name of test run
        :param count: (optional) number of tests expected to run
        """

    @abstractmethod
    def test_suite_ended(self, test_run_name: str, duration: float = -1.0, **kwargs: Optional[Any]) -> None:
        """
        signals test suite has ended

        :param test_run_name: name of test run
        :param duration: device-reported elapsed time
        :param kwargs: additional data to store with this test run
        """

    @abstractmethod
    def test_suite_failed(self, test_run_name: str, error_message: str) -> None:
        """
        Reports test run failed to complete due to a fatal error.

        :param test_run_name: name of test run
        :param error_message: description of reason for run failure
        """

    @abstractmethod
    def test_failed(self, test_run_name: str, class_name: str, test_name: str, stack_trace: str) -> None:
        """
        signals test failure

        :param test_run_name: name of test run
        :param class_name: fully qualified class name of the test
        :param test_name: name of the test
        :param stack_trace: a stack trace of the failure cause
        """

    @abstractmethod
    def test_ignored(self, test_run_name: str, class_name: str, test_name: str) -> None:
        """
        signals test was ignored

        :param test_run_name: name of test run
        :param class_name: fully qualified class name of the test
        :param test_name: name of the test
        """

    @abstractmethod
    def test_assumption_failure(self, test_run_name: str, class_name: str, test_name: str, stack_trace: str) -> None:
        """
        signal test assumption was violated and test was skipped since platform did not support it

        :param test_run_name: name of test run
        :param class_name: fully qualified class name of the test
        :param test_name: name of the test
        :param stack_trace: a stack trace of the assumption failure cause
        """

    @abstractmethod
    def test_started(self, test_run_name: str, class_name: str, test_name: str) -> None:
        """
        signal test has started

        :param test_run_name: name of test run
        :param class_name: fully qualified class name of the test
        :param test_name: name of the test
        """

    @abstractmethod
    def test_ended(self, test_run_name: str, class_name: str, test_name: str,
                   instrumentation_output: Optional[str] = None, **kwargs: Optional[Any]) -> None:
        """
        signal test has ended, presumably with success

        :param test_run_name: name of test run
        :param class_name: fully qualified class name of the test
        :param test_name: name of the test
        :param instrumentation_output: if parser was so configured, will contain the lines of output from adb
           instrument for the test that ended
        :param kwargs: additional data to store with this test
        """

    def observing_test(self, test_run_name: str, class_name: str, test_name: str) -> 'TestResultContextManager':
        """
        Taken from mdl-integration. Creates an context manager to wrap test execution for reporting. By default, exiting
        the context manager marks the test as passed, unless an exception was raised, in which case the test is marked
        as failed with that exception's message. This may be overridden by calling "test_failed" (or other similar
        methods) manually on the context manager.

        :param test_run_name: name of test run
        :param class_name: fully qualified class name of the test
        :param test_name: name of the test
        :return: A TestResultContextManager
        """
        return TestResultContextManager(self, test_run_name, class_name, test_name)


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


class TestResultContextManager:
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

    def __init__(self, listener: TestExecutionListener, test_run_name: str, class_name: str, test_name: str):
        self._listener = listener
        self._test_run_name = test_run_name
        self._class_name = class_name
        self._test_name = test_name

    def __enter__(self) -> 'TestResultContextManager':
        self._listener.test_started(self._test_run_name, self._class_name, self._test_name)
        return self

    def __exit__(self, exc_type: Optional[Type[BaseException]], exc_val: Optional[BaseException],
                 exc_tb: Optional[TracebackType]) -> None:
        if exc_type is not None:
            self.test_failed(str(exc_val))
        self._listener.test_ended(self._test_run_name, self._class_name, self._test_name)

    def test_failed(self, stack_trace: str) -> None:
        self._listener.test_failed(self._test_run_name, self._class_name, self._test_name, stack_trace)

    def test_assumption_failure(self, stack_trace: str) -> None:
        self._listener.test_assumption_failure(self._test_run_name, self._class_name, self._test_name, stack_trace)

    def test_ignored(self) -> None:
        self._listener.test_ignored(self._test_run_name, self._class_name, self._test_name)


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
