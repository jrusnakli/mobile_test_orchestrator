import datetime
import logging

from abc import abstractmethod, ABC
from contextlib import suppress
from typing import List, Optional, Any

from .reporting import TestStatus, TestExecutionListener
from .timing import StopWatch

with suppress(ModuleNotFoundError):
    from dataclasses import dataclass  # for Python 3.6, but not in Python 3.7 where dataclass is builtin

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)


class LineParser(ABC):
    """
    Basic line-by-line parser interface
    """

    @abstractmethod
    def parse_line(self, line: str) -> None:
        """
        Parse the given line
        :param line: text of line to parse
        :return:
        """


class InstrumentationOutputParser(LineParser):
    """
    Concrete implementation of :class:`~parsing.AsyncLineParser` class
    to parse test status from the lines of output from an Android device's instrument command
    """

    CODE_PASSED = 0
    CODE_ERROR = -1
    CODE_FAILED = -2
    CODE_SKIPPED = -3
    CODE_ASSUMPTION_VIOLATION = -4

    @dataclass
    class InstrumentTestResult(object):
        test_id: str = ""
        clazz: str = ""
        stream: str = ""
        test_no: int = -1
        stack: str = ""
        started: bool = False
        result: Optional[TestStatus] = None
        runner: str = ""
        start_time: datetime.datetime = datetime.datetime.utcnow()

        def set(self, field_name: str, value: str) -> None:
            value = value.strip()
            if field_name == 'id':
                self.runner = value
            elif field_name == 'test':
                self.test_id = value
            elif field_name == 'stream':
                if self.stream:
                    self.stream += '\n'
                self.stream += value
            elif field_name == "class":
                self.clazz = value
            elif field_name == "current":
                self.test_no = int(value)
            elif field_name == 'stack':
                if self.stack:
                    self.stack += '\n'
                self.stack += value
            else:
                log.warning("Unrecognized field: %s;  ignoring" % field_name)

    def __init__(self, test_run_name: str, test_listeners: List[TestExecutionListener]) -> None:
        """
        :param test_listener: Reporter object to report test status on an on-going basis
        """
        super(InstrumentationOutputParser, self).__init__()
        # internal attributes:
        self._reporters = test_listeners
        self._execution_listeners: List[StopWatch] = []
        self._test_result: Optional[InstrumentationOutputParser.InstrumentTestResult] = None
        self._current_key: Optional[str] = None
        # attributes made public through getters:
        self._execution_time: float = -1.0
        self._total_test_count: int = 0
        self._return_code: Optional[int] = None
        self._streamed: List[str] = []
        self._test_run_name = test_run_name

    # PROPERTIES

    @property
    def execution_time(self) -> float:
        return self._execution_time

    @property
    def total_test_count(self) -> int:
        return self._total_test_count

    # PRIVATE API

    def _process_test_code(self, code: int) -> None:
        """
        parse test code value from an instrumentation output
        :param code: code to evaluate

        :raises: ValueError if code is unrecognized
        """
        def apply(methodname: str, *args: Any, **kargs: Any) -> Any:
            for reporter in self._reporters:
                method = getattr(reporter, methodname)
                method(self._test_run_name, *args, **kargs)

        assert self._test_result, "expected self._test_result to be set"
        if code > 0:
            self._test_result.started = True
            self._test_result.start_time = datetime.datetime.utcnow()
            for listener in self._execution_listeners:
                listener.mark_start(".".join([self._test_result.clazz, self._test_result.test_id]))

            # this code just states test has been started, nothing more to do
            return
        try:
            if code == self.CODE_PASSED:
                self._test_result.result = TestStatus.PASSED
            elif code in [self.CODE_ERROR, self.CODE_FAILED]:
                self._test_result.result = TestStatus.FAILED
            elif code in [self.CODE_SKIPPED]:
                self._test_result.result = TestStatus.IGNORED
            elif code in [self.CODE_ASSUMPTION_VIOLATION]:
                self._test_result.result = TestStatus.ASSUMPTION_FAILURE
            else:
                raise ValueError("Unknown test dode: %d" % code)
            # capture result and start over with clean slate:
            if self._test_result.result == TestStatus.PASSED:
                duration = (datetime.datetime.utcnow() - self._test_result.start_time).total_seconds()
                apply("test_ended",
                      class_name=self._test_result.clazz,
                      test_name=self._test_result.test_id,
                      test_no=self._test_result.test_no,
                      duration=duration,
                      msg=self._test_result.stream)
            elif self._test_result.result == TestStatus.FAILED:
                apply("test_failed",
                      class_name=self._test_result.clazz,
                      test_name=self._test_result.test_id,
                      stack_trace=self._test_result.stack)
            elif self._test_result.result == TestStatus.IGNORED:
                apply("test_ignored",
                      class_name=self._test_result.clazz,
                      test_name=self._test_result.test_id)
            elif self._test_result.result == TestStatus.ASSUMPTION_FAILURE:
                apply("test_assumption_failure",
                      class_name=self._test_result.clazz,
                      test_name=self._test_result.test_id,
                      stack_trace=self._test_result.stack)
            else:
                raise ValueError("Unknown status code for test: %d" % code)
        finally:
            for listener in self._execution_listeners:
                listener.mark_end(".".join([self._test_result.clazz, self._test_result.test_id]))
            self._test_result = None
            self._current_key = None

    def parse_line(self, line: str) -> None:
        """
        parser a line for information on test (no exception handling)
        :param line: line to by parsed
        """
        if not line:
            return
        log.info(f"Instrumentation parsing line: {line}")
        if line.startswith("INSTRUMENTATION_STATUS_CODE:"):
            if not self._test_result:
                raise Exception("test start code received but not in test execution block!")
            code = int(line.split(":")[-1].strip())
            self._process_test_code(code)
            self._streamed = []
        elif line.startswith("INSTRUMENTATION_STATUS:"):
            if not self._test_result:
                self._test_result = InstrumentationOutputParser.InstrumentTestResult()
            key, value = line.split(':', 1)[-1].strip().split('=', 1)
            if key == 'numtests':
                self._total_test_count = int(value)
            else:
                self._test_result.set(key, value)
                self._current_key = key
        elif line.startswith("INSTRUMENTATION_CODE:"):
            self._return_code = int(line.split(':')[-1].strip())
        elif line.startswith("Time:"):
            try:
                time_string = line.split(":")[-1].strip().replace('s', '').replace(',', '')
                self._execution_time = float(time_string)
            except Exception:
                log.error("Error parsing time as float from line %s" % line)
                self._execution_time = -1.0
        elif line.startswith("OK"):
            log.debug("Test execution completed for %d tests" % self._total_test_count)
            if self._test_result:
                log.error("Incomplete test found: %s" % self._test_result.test_id)
            self._current_key = None
            self._test_result = None
        elif self._current_key:
            # A continuation of last processed key:
            assert self._test_result, "expected self._test_result to be set"
            self._test_result.set(self._current_key, line)
        elif self._current_key == "stream" and line:
            self._streamed.append(line)
        elif line:
            log.debug("Unassociated line of output: \"%s\" being ignored" % line)

    # PUBLIC API

    def add_test_execution_listener(self, listener: StopWatch) -> None:
        """
        add an agent for this parser to use to mark the start and end of tests
        (for example to capture start and end positions of a test within logcat output)
        """
        self._execution_listeners.append(listener)
