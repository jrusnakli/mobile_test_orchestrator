import datetime
import json
import logging

from abc import abstractmethod, ABC
from contextlib import suppress
from typing import List, Tuple

from androidtestorchestrator import ServiceApplication, Application
from .reporting import TestStatus, TestListener
from .timing import StopWatch

with suppress(ModuleNotFoundError):
    from dataclasses import dataclass  # for Python 3.6, but not in Python 3.7 where dataclass is builtin

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
        result: TestStatus = None
        runner: str = ""
        start_time: datetime.datetime.date = datetime.datetime.utcnow()

        def set(self, field_name: str, value: str):
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

    def __init__(self, test_listener: TestListener):
        """
        :param test_listener: Reporter object to report test status on an on-going basis
        """
        super(InstrumentationOutputParser, self).__init__()
        # internal attributes:
        self._reporter = test_listener
        self._execution_listeners: List[StopWatch] = []
        self._test_result: InstrumentationOutputParser.InstrumentTestResult = None
        self._current_key: str = None
        # attributes made public through getters:
        self._execution_time: float = -1.0
        self._total_test_count: int = 0
        self._return_code = None
        self._streamed = []

    # PROPERTIES

    @property
    def execution_time(self) -> float:
        return self._execution_time

    @property
    def total_test_count(self) -> int:
        return self._total_test_count

    # PRIVATE API

    def _process_test_code(self, code: int):
        """
        parse test code value from an instrumentation output
        :param code: code to evaluate

        :raises: ValueError if code is unrecognized
        """
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
                self._test_result.result = TestStatus.SKIPPED
            elif code in [self.CODE_ASSUMPTION_VIOLATION]:
                self._test_result.result = TestStatus.ASSUMPTION_VIOLATED
            else:
                raise ValueError("Unknown test dode: %d" % code)
            # capture result and start over with clean slate:
            if self._test_result.result == TestStatus.PASSED:
                duration = (datetime.datetime.utcnow() - self._test_result.start_time).total_seconds()
                self._reporter.test_ended(test_name=self._test_result.test_id,
                                          test_class=self._test_result.clazz,
                                          test_no=self._test_result.test_no,
                                          duration=duration,
                                          msg=self._test_result.stream)
            elif self._test_result.result == TestStatus.FAILED:
                self._reporter.test_failed(test_name=self._test_result.test_id,
                                           test_class=self._test_result.clazz,
                                           test_no=self._test_result.test_no,
                                           msg=self._test_result.stream,
                                           stack=self._test_result.stack)
            elif self._test_result.result == TestStatus.SKIPPED:
                self._reporter.test_ignored(test_name=self._test_result.test_id,
                                            test_class=self._test_result.clazz,
                                            test_no=self._test_result.test_no,
                                            msg=self._test_result.stream)
            elif self._test_result.result == TestStatus.ASSUMPTION_VIOLATED:
                self._reporter.test_assumption_violated(test_name=self._test_result.test_id,
                                                        test_class=self._test_result.clazz,
                                                        test_no=self._test_result.test_no,
                                                        reason=self._test_result.stream)
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


class TestButlerCommandParser(LineParser):
    """
    Concrete implementation of the `LineParser` class that parses an Android device's logcat output
    for TestButler commands to enact,

    Note that `androidtestorchestrator.logcapture.LogcatTagDemuxer` provides the AsyncLineParser interface, which
    demuxes output based on logcat tag and directs to this class for those tags matching the test butler tag
    """
    BUTLER_SERVICE_TAG = "ButlerService-MDC"

    SETTINGS_PREFIX = "TEST_BUTLER_SETTING"
    PROPERTY_PREFIX = "TEST_BUTLER_PROPERTY"
    GRANT_PREFIX = "TEST_BUTLER_GRANT"
    TEST_ONLY_PREFIX = "TEST_ONLY"

    CODE_ASSUMPTION_VIOLATION = 4  # must match on Java side

    class DeviceChangeListener(ABC):
        """
        Listener class to listen for device changes evoked by test butler
        """

        @abstractmethod
        def device_setting_changed(self, namespace, key, previous, new) -> None:
            """

            :param namespace: namespace of changed setting
            :param key: key of changed setting
            :param previous: previous value of setting (for restoration at end of testing if desired)
            :param new: new value for setting
            """

        @abstractmethod
        def device_property_changed(self, key, previous, new):
            """
            :param key: key of changed property
            :param previous: previous value of property (for restoration purposes if desired)
            :param new: new value of property
            """

    def __init__(self, service: ServiceApplication, app_under_test: Application,
                 listener: DeviceChangeListener=None):
        """

        :param service: the service app running test butler on the remote device
        :param listener: listener for device settings and property changes
        """
        super(LineParser, self).__init__()
        self._service_app = service
        self._app_under_test = app_under_test
        self._listener = listener
        # mapping of string prefix to method to invoke to execute command
        self._method_map = {
            self.SETTINGS_PREFIX: self._process_set_device_setting_cmd,
            self.PROPERTY_PREFIX: self._process_set_property_cmd,
            self.GRANT_PREFIX: self._process_grant_permission_cmd,
        }

    def parse_line(self, line: str):
        """
        Main processing logic
        :param line: line of output from logcat containing a test butler command
        """
        try:
            priority, tag = line.split('(')[0].split('/')
        except ValueError:
            log.error("Unexpected format for logcat message in line: '%s'" % line)
            return
        if priority != 'I':  # only process information logcat messages
            return
        if tag != "TestButler":
            log.error("Received invalid tag: %s when processing test butler input" % tag)
            return
        try:
            msg = line.split(':', 1)[-1]
            command_and_id, payload = msg.strip().split(":", 1)
            command_id, command = command_and_id.split(' ')
            command_id = int(command_id)
            payload = payload.strip()
            if command not in self._method_map:
                log.error("Unknown command received: %s" + line)
                self._send_response(command_id, 1, "Unknown command")
                return
            else:
                try:
                    response_code, response_msg = self._method_map[command](payload)
                    self._send_response(command_id, response_code, response_msg)
                    if response_code != 0:
                        log.error(response_msg)
                except Exception as e:
                    log.error(str(e))
                    self._send_response(command_id, 1, "Host error in processing command : %s" % str(e))

        except ValueError:
            log.error("Unexpected format for command and payload: '%s'" % line)

    def _send_response(self, cmd_id: int, response_code: int, response_msg: str):
        """
        handshake to send command response back
        :param cmd_id: id of command
        :param response_code: integer code (0 for success, 1 for error -- for now)
        :param response_msg: textual response message
        """
        msg = "\"%d,%d,%s\"" % (cmd_id, response_code, response_msg)
        self._service_app.start(".ButlerService", "--es", "response", msg,
                                intent="com.linkedin.android.testbutler.COMMAND_RESPONSE")

    # Command-handling methods

    def _process_set_device_setting_cmd(self, cmd: str) -> Tuple[int, str]:
        """
        process device setting change command, informing any listener of change

        :param cmd: string command containing json data for new device setting
        """
        try:
            namespace, key, value = cmd.split(' ', 2)
        except ValueError:
            log.error("Received invalid format in command: %s" % cmd)
            return

        if namespace is None or key is None or value is None:
            return 1, "Missing namespace, key or value in command: %s" % cmd
        previous = self._service_app.device.set_device_setting(namespace, key, value)
        if previous is None:
            msg = "Test Butler command '%s' failed"
            return 1, msg
        else:
            new_value = self._service_app.device.get_device_setting(namespace, key)
            if value.startswith('+'):
                if value[1:] not in new_value:
                    return self.CODE_ASSUMPTION_VIOLATION, \
                           "Setting of %s to %s not supported on this device" % (namespace + ':' + key, value)
                else:
                    return 0, "Success"
            elif value.startswith('-'):
                if value[1:] in new_value:
                    return 1, "Unable to remove from setting: %s" % value
                else:
                    return 0, "Success"
            elif new_value.replace("\"", '') != value.replace("\\", '').replace("\"", ""):
                msg = "Expected system setting %s:%s to be set to '%s' but output from system-get was: '%s'" % (
                    namespace, key, value, new_value)
                return 1, msg
            else:
                if previous != "null" and self._listener is not None:
                    self._listener.device_setting_changed(namespace, key, previous, value)
                log.debug("Successful return status from test butler command '%s': new value of %s" % (cmd, value))
                return 0, "Success"

    def _process_set_property_cmd(self, cmd: str) -> Tuple[int, str]:
        """
        process device property change command, informing any listener of change

        :param cmd: string command containing json data for new device property
        """
        items = cmd.split(' ')

        if len(items) < 2:
            msg = "Invalid test butler command; not enough arguments to set property: %s" % cmd
            return 1, msg
        key = items[-2]
        value = items[-1]
        if key == "locale":
            self._service_app.device.set_locale(value)
        else:
            previous = self._service_app.device.set_system_property(key, value)
            if self._listener is not None:
                self._listener.device_property_changed(key, previous, value)
            if previous is None and key not in ["location_providers_allowed"]:
                msg = "Test Butler command %s failed" % cmd
                return 1, msg
            else:
                new_value = self._service_app.device.get_system_property(key)
                if new_value != value:
                    msg = "Expected system property %s to be set to %s but found value %s" % (key, value, new_value)
                    return 1, msg
                else:
                    log.debug("Successfully set property %s to %s" % (key, value))
                    return 0, "Success"

    def _process_grant_permission_cmd(self, cmd: str) -> Tuple[int, str]:
        """
        process command to grant permission
        :param cmd: json containing permissions to grant
        """
        try:
            grant_json = json.loads(cmd)
        except ValueError:
            log.error("Got invalid grant request json: %s", cmd)
            return 1, "Invalid json in request"

        grant_type = grant_json.get('type', None)
        if grant_type == "permission":
            package = grant_json.get('package', None)
            if package is None:
                log.error("Missing package for grant request: %s" % cmd)
                return
            permissions = grant_json.get('permissions', [])
            if not permissions:
                msg = "Missing permissions for grant request: %s" % cmd
                return 1, msg
            if not self._app_under_test.grant_permissions(permissions):
                return 1, "Failed to grant requested permission(s)"
            else:
                return 0, "Success"
        else:
            msg = "Unexpected grant request type '%s'" % grant_type
            return 1, msg
