"""
This package deals with parsing output from command line utilities, including command executed on a remote device.
The primary interaction with devices is conducted through adb (a command line utility), and progres and status is
received as part of the standard output of such commands.

The core class for parsing this output is a simple, abstract LineParser class.  Core concrete subclasses for parsing
the test status through the output of "adb instrument"  test status, and for demultiplexing logcat output based on tags
are provided by this package.
"""
import logging

from abc import abstractmethod, ABC
from typing import List, Optional, Any, Dict, Tuple

from .reporting import TestExecutionListener
from .timing import StopWatch

log = logging.getLogger(__name__)


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
    Inspired by:
    https://android.googlesource.com/platform/tools/base/+/master/ddmlib/src/main/java/com/android/ddmlib/testrunner/InstrumentationResultParser.java

    Parses the 'raw output mode' results of an instrumentation test run from shell and informs a
    TestExecutionListener of the results.

    Expects the following output:

    If fatal error occurred when attempting to run the tests:

    .. code-block:: bash

        INSTRUMENTATION_STATUS: Error=error Message
        INSTRUMENTATION_FAILED:

    or

    .. code-block:: bash

        INSTRUMENTATION_RESULT: shortMsg=error Message

    Otherwise, expect a series of test results, each one containing a set of status key/value
    pairs, delimited by a start(1)/pass(0)/fail(-2)/error(-1) status code result. At end of test
    run, expects that the elapsed test time in seconds will be displayed

    For example:

    .. code-block:: bash

        INSTRUMENTATION_STATUS: id=...TestRunner
        INSTRUMENTATION_STATUS: class=com.foo.FooTest
        INSTRUMENTATION_STATUS: test=testFoo
        INSTRUMENTATION_STATUS: numtests=2
        INSTRUMENTATION_STATUS: stack=com.foo.FooTest#testFoo:312
         com.foo.X
        INSTRUMENTATION_STATUS_CODE: -2
        ...

        Time: X

    Note that the "value" portion of the key-value pair may wrap over several text lines
    """
    CODE_START = 1
    CODE_IN_PROGRESS = 2
    CODE_PASS = 0
    CODE_ERROR = -1
    CODE_FAIL = -2
    CODE_SKIPPED = -3
    # if a junit assumption-failure-exception is raised, it means test should be counted as skipped;  code for this is
    # -4:
    CODE_ASSUMPTION_VIOLATION = -4

    # line control prefix
    PREFIX_STATUS = "INSTRUMENTATION_STATUS: "
    PREFIX_STATUS_CODE = "INSTRUMENTATION_STATUS_CODE: "
    PREFIX_FAILED = "INSTRUMENTATION_FAILED: "
    PREFIX_CODE = "INSTRUMENTATION_CODE: "
    PREFIX_RESULT = "INSTRUMENTATION_RESULT: "
    PREFIX_TIME = "Time: "

    FAILURE_MSG = "FAILURES!!!"

    # instrumentation status keys
    KEY_TEST = "test"
    KEY_CLASS = "class"
    KEY_STACK = "stack"
    KEY_STREAM = "stream"
    KEY_NUM_TESTS = "numtests"
    KEY_ERROR = "Error"
    KEY_SHORT_MSG = "shortMsg"
    # unused keys
    KEY_ID = "id"
    KEY_CURRENT = "current"

    KNOWN_KEYS = {
        KEY_TEST,
        KEY_CLASS,
        KEY_STACK,
        KEY_STREAM,
        KEY_NUM_TESTS,
        KEY_ERROR,
        KEY_SHORT_MSG,
        KEY_ID,
        KEY_CURRENT
    }

    UNKNOWN_TEST_CLASS = "<<UNKNOWN TEST CLASS!>>"
    UNKNOWN_TEST_NAME = "<<UNKNOWN TEST NAME!>>"
    MISSING_STACK_TRACE = "<<NO STACK TRACE>>"

    class TestParsingResult:
        """Holds information about current test while parsing"""
        def __init__(self) -> None:
            self.code: Optional[int] = None
            self.test_name: Optional[str] = None
            self.test_class: Optional[str] = None
            self.num_tests: Optional[int] = None
            self.stack_trace: Optional[str] = None
            self.stream: Optional[str] = None

        def is_complete(self) -> bool:
            return (self.code is not None
                    and self.test_name is not None
                    and self.test_class is not None)

        def __repr__(self) -> str:
            return self.__class__.__name__ + str(self.__dict__)

    class TestRunError(Exception):
        pass

    def __init__(self, test_run_name: str, test_run_listener: Optional[TestExecutionListener] = None,
                 include_instrumentation_output: bool = False) -> None:
        super().__init__()
        self._test_run_name = test_run_name
        self._execution_listeners: List[TestExecutionListener] = [test_run_listener] if test_run_listener else []
        self._test_listeners: List[StopWatch] = []
        self._current_test: Optional[InstrumentationOutputParser.TestParsingResult] = None
        self._last_test: Optional[InstrumentationOutputParser.TestParsingResult] = None
        self._in_result_key_value: bool = False
        self._current_key: Optional[str] = None
        self._current_value: Optional[List[str]] = None

        # either we got INSTRUMENTATION_CODE or INSTRUMENTATION_FAILED signaling end of run
        self._test_run_finished: bool = False
        self._reported_any_results: bool = False
        self._got_failure_msg: bool = False

        self._num_tests_expected: int = 0
        self._num_tests_run: int = 0

        # todo: currently unused. However, android studio reports this as test run duration.
        self._test_run_time: float = 0

        self._include_instrumentation_output = include_instrumentation_output
        self._instrumentation_text = ""
        self._test_run_failure_msgs: List[str] = []

    def __enter__(self) -> "InstrumentationOutputParser":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    @property
    def execution_time(self) -> float:
        return self._test_run_time

    @property
    def total_test_count(self) -> int:
        return self._num_tests_run

    @property
    def num_tests_expected(self) -> int:
        return self._num_tests_expected

    def failure_message(self) -> str:
        if not self._test_run_failure_msgs:
            return ""
        return "\n".join(["Test run failed to execute all tests. Erros message(s) were:"] + self._test_run_failure_msgs)

    def add_execution_listener(self, listener: TestExecutionListener) -> None:
        """
        Add listener for test start/end as well as test status
        :param listener: listener to add
        """
        self._execution_listeners.append(listener)

    def add_execution_listeners(self, listeners: List[TestExecutionListener]) -> None:
        """
        Add a list of listeners for test run status
        :param listeners: list of listeners to add
        """
        self._execution_listeners += listeners

    def add_simple_test_listener(self, listener: StopWatch) -> None:
        """
        add an agent for this parser to use to mark the start and end of tests
        without need to listen for test status
        """
        self._test_listeners.append(listener)

    def parse_line(self, line: str) -> None:
        """
        Entry point. Parses one line of output at a time.
        :param line: A single line of output (typically ending in '\n', unless the end of output has been reached)
        """
        if self._include_instrumentation_output:
            # collect raw output to send to client:
            self._instrumentation_text += line + "\n"
        if line.startswith(self.PREFIX_STATUS_CODE):
            self._finalize_current_key_value()
            self._in_result_key_value = False
            self._parse_status_code(line[len(self.PREFIX_STATUS_CODE):])
        elif line.startswith(self.PREFIX_STATUS):
            self._finalize_current_key_value()
            self._in_result_key_value = False
            self._parse_key_value(line[len(self.PREFIX_STATUS):])
        elif line.startswith(self.PREFIX_RESULT):
            self._finalize_current_key_value()
            self._in_result_key_value = True
            self._parse_key_value(line[len(self.PREFIX_RESULT):])
        elif line.startswith(self.PREFIX_FAILED) \
                or line.startswith(self.PREFIX_CODE):
            self._finalize_current_key_value()
            self._in_result_key_value = False
            # at close() we'll report the error
            self._test_run_finished = True
        elif line.startswith(self.PREFIX_TIME):
            self._parse_time(line[len(self.PREFIX_TIME):])
        else:
            # Handles the case where the instrumentation output itself fails. In that case it only outputs:
            # INSTRUMENTATION_RESULT: stream=...
            # ...
            # FAILURES!!!
            # Tests run: 0,  Failures: x
            # INSTRUMENTATION_CODE: -1
            if not self._got_failure_msg and line.startswith(self.FAILURE_MSG):
                self._got_failure_msg = True
            # continuation of value from previous status
            if self._current_value is not None:
                self._current_value.append(line)
            elif line.strip():
                log.debug("Unrecognized line: %s", line)

    def _parse_status_code(self, line: str) -> None:
        """
        Parses content of line after "INSTRUMENTATION_STATUS_CODE: "
        """
        test = self._get_current_test()
        test_class = test.test_class or self.UNKNOWN_TEST_CLASS
        test_name = test.test_name or self.UNKNOWN_TEST_NAME
        try:
            test.code = int(line.strip())
        except ValueError:
            log.warning("Expected int status code, received: %s", line)
            test.code = self.CODE_ERROR

        if test.code != self.CODE_IN_PROGRESS:
            # end of test result bundle -- either test starting or ending
            self._report_result(test)
            self._last_test = self._current_test
            self._current_test = None
        elif test.code == self.CODE_START:
            # mark start of next test
            for listener in self._test_listeners:
                listener.mark_start(".".join([test_class, test_name]))
        else:
            for listener in self._test_listeners:
                listener.mark_end(".".join([test_class, test_name]))

    def _parse_key_value(self, line: str) -> None:
        """
        Parses line format key=value (after "INSTRUMENTATION_STATUS: " or "INSTRUMENTATION_RESULT: ")
        """
        key_val = line.split('=', 1)
        if len(key_val) == 2:
            self._current_key = key_val[0].strip()
            self._current_value = [key_val[1]]
        else:
            log.warning("Expected key=value, got: %s", line)

    def _parse_time(self, line: str) -> None:
        """
        Parses content of line after "Time: "
        """
        try:
            self._test_run_time = float(line)
        except ValueError:
            log.warning("Unexpected time format: %s", line)

    def _get_current_test(self) -> TestParsingResult:  # noqa: https://github.com/PyCQA/pyflakes/issues/427
        """
        :return: TestParsingResult for the currently running test, creating one if none exists
        """
        current_test = self._current_test or self.TestParsingResult()
        if not self._current_test:
            self._current_test = current_test
        return current_test

    def _finalize_current_key_value(self) -> None:
        """
        For key-value lines, the value may consist of multiple lines of output. When we see a key=value line, all
        following lines will be added to the value until we see another known prefix. Once we hit a known prefix, this
        method is called to finalize the value and do some action according to the key: report a test result, record
        some property of the test, etc.
        """
        if self._current_key is not None and self._current_value is not None:
            value = ''.join(self._current_value)

            if self._in_result_key_value:
                if self._current_key not in self.KNOWN_KEYS:
                    # todo: bundle key values?
                    pass
                elif self._current_key == self.KEY_SHORT_MSG:
                    # todo: collect more info? long_msg?
                    self._report_test_run_failed("Instrumentation run failed due to '%s'" % value)
            else:
                test = self._get_current_test()

                if self._current_key == self.KEY_CLASS:
                    test.test_class = value.strip()
                elif self._current_key == self.KEY_TEST:
                    test.test_name = value.strip()
                elif self._current_key == self.KEY_NUM_TESTS:
                    try:
                        test.num_tests = int(value.strip())
                    except ValueError:
                        log.warning("Unexpected number of tests, received: %s", value)
                elif self._current_key == self.KEY_ERROR:
                    self._report_test_run_failed(value)
                elif self._current_key == self.KEY_STACK:
                    test.stack_trace = '\n'.join(self._current_value)
                elif self._current_key == self.KEY_STREAM:
                    test.stream = value
                elif self._current_key not in self.KNOWN_KEYS:
                    # todo: test metrics?
                    pass

            self._current_key = None
            self._current_value = None

    def _report_result(self, test: TestParsingResult) -> None:
        """
        Reports the given TestParsingResult to the TestExecutionListener (test starting or test ending).
        :param test: the TestParsingResult that should be reported
        """
        if not test.is_complete():
            log.warning("Invalid instrumentation status bundle: %s", test)
            return

        if not self._reported_any_results and test.num_tests is not None:
            self._num_tests_expected = test.num_tests
            self._reported_any_results = True

        test_class = test.test_class or self.UNKNOWN_TEST_CLASS
        test_name = test.test_name or self.UNKNOWN_TEST_NAME

        if test.code == self.CODE_START:
            for reporter in self._execution_listeners:
                reporter.test_started(self._test_run_name, test_class, test_name)
            return

        self._num_tests_run += 1
        if test.code == self.CODE_FAIL or test.code == self.CODE_ERROR:
            for reporter in self._execution_listeners:
                reporter.test_failed(self._test_run_name, test_class, test_name,
                                     test.stack_trace or self.MISSING_STACK_TRACE)
                reporter.test_ended(self._test_run_name, test_class, test_name)
        elif test.code == self.CODE_SKIPPED:
            for reporter in self._execution_listeners:
                reporter.test_started(self._test_run_name, test_class, test_name)
                reporter.test_ignored(self._test_run_name, test_class, test_name)
                reporter.test_ended(self._test_run_name, test_class, test_name)
        elif test.code == self.CODE_ASSUMPTION_VIOLATION:
            for reporter in self._execution_listeners:
                reporter.test_assumption_failure(self._test_run_name, test_class, test_name,
                                                 test.stack_trace or self.MISSING_STACK_TRACE)
                reporter.test_ended(self._test_run_name, test_class, test_name)
        elif test.code == self.CODE_PASS:
            for reporter in self._execution_listeners:
                reporter.test_ended(self._test_run_name, test_class, test_name)
        else:
            if test.code == self.CODE_SKIPPED:
                for reporter in self._execution_listeners:
                    reporter.test_started(self._test_run_name, test_class, test_name)
                    reporter.test_ignored(self._test_run_name, test_class, test_name)
            elif test.code == self.CODE_ASSUMPTION_VIOLATION:
                for reporter in self._execution_listeners:
                    reporter.test_assumption_failure(
                        self._test_run_name,
                        test_class,
                        test_name,
                        test.stack_trace or self.MISSING_STACK_TRACE
                    )
            else:
                log.warning("Unknown status code %s. Stacktrace: %s", test.code, test.stack_trace)
                for reporter in self._execution_listeners:
                    reporter.test_failed(self._test_run_name, test_class, test_name,
                                         stack_trace=f"<<internal errro:unknown status code {test.code}")
                    reporter.test_ended(self._test_run_name, test_class, test_name)
            for reporter in self._execution_listeners:
                reporter.test_ended(self._test_run_name, test_class, test_name,
                                    instrumentation_output=self._instrumentation_text)
        self._instrumentation_text = ""

    def _report_test_run_failed(self, error_message: str) -> None:
        """
        Reports a test run failure to the TestExecutionListener
        :param error_message: The error message to report
        """
        log.info("Test run failed: %s", error_message)
        if self._last_test and self._last_test.is_complete and self._last_test.code == self.CODE_START:
            test_class = self._last_test.test_class or self.UNKNOWN_TEST_CLASS
            test_name = self._last_test.test_name or self.UNKNOWN_TEST_NAME
            # got test start but not test stop - assume that test caused this and report as test failure
            stack_trace = "Test failed to run to completion. Reason: '%s'." \
                          " Check device logcat for details." % error_message
            for reporter in self._execution_listeners:
                reporter.test_failed(self._test_run_name, test_class, test_name, stack_trace)
                reporter.test_ended(self._test_run_name, test_class, test_name,
                                    instrumentation_output=self._instrumentation_text)
        self._instrumentation_text = ""
        self._test_run_failure_msgs.append(error_message)
        self._reported_any_results = True

    def close(self) -> None:
        """
        Ensures that all expected tests have been run, or else fails the test run.
        """
        if self._test_run_failure_msgs:
            return
        if not self._reported_any_results and (not self._test_run_finished or self._got_failure_msg):
            self._report_test_run_failed("No test results, instrumentation may have failed")
        elif self._num_tests_run < self._num_tests_expected:
            self._report_test_run_failed(
                "Test run failed to complete."
                " Expected %s tests, received %s" % (self._num_tests_expected, self._num_tests_run))


class LogcatTagDemuxer(LineParser):
    """
    Concrete LineParser that processes lines of output from logcat filtered on a set of tags and demuxes those lines
    based on a specific handler for each tag

    :param handlers: dictionary of tuples of (logcat priority, handler)
    """

    def __init__(self, handlers: Dict[str, Tuple[str, LineParser]]):
        # remove any spec on priority from tags:
        super().__init__()
        self._handlers = {tag: handlers[tag][1] for tag in handlers}

    def parse_line(self, line: str) -> None:
        """
        farm each incoming line to associated handler based on adb tag
        :param line: line to be parsed
        """
        if not self._handlers:
            return
        if line.startswith("-----"):
            # ignore, these are startup output not actual logcat output from device
            return
        try:
            # extract basic tag from line of logcat:
            tag = line.split('(', 2)[0]
            if not len(tag) > 2 and tag[1] == '/':
                log.debug("Invalid tag in logcat output: %s" % line)
                return
            tag = tag[2:]
            if tag not in self._handlers:
                log.error("Unrecognized tag!? %s" % tag)
                return
            # demux and handle through the proper handler
            self._handlers[tag].parse_line(line)
        except ValueError:
            log.error("Unexpected logcat line format: %s" % line)
