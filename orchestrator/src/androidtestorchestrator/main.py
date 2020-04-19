import asyncio
import sys

import logging
import os
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, AsyncIterator, Dict, Iterator, List, Optional, Tuple, Type, Union

from .application import TestApplication
from .device import Device
from .devicelog import DeviceLog, LogcatTagDemuxer
from .parsing import LineParser
from .reporting import TestExecutionListener
from .worker import Worker

log = logging.getLogger(__name__)


if sys.platform == 'win32':
    loop = asyncio.ProactorEventLoop()
    asyncio.set_event_loop(loop)


@dataclass(frozen=True)
class TestSuite:
    """
    A dataclass representing a test suite that defines the attributes:
    """

    "unique name of test suite"
    name: str
    """
    arguments to be passed to the am instrument command, run as
        "am instrument -w -r [-e key value for key,value in arguments] <package>/<runner> ..."
    """
    test_parameters: Dict[str, str]
    "optional list of tuples of (loacl_path, remote_path) of test vector files to be uploaded to remote device"
    uploadables: List[Tuple[str, str]] = field(default_factory=list)
    "optional list of tuples of (loacl_path, remote_path) of test vector files to be uploaded to remote device"
    clean_data_on_start: bool = False


class AndroidTestOrchestrator:
    """
    Class for orchestrating interactions with a device or emulator during execution of a test or suite of tests.
    The idea is to execute a set of (or single) test suites, referred to here as a "test plan", with each
    suite being a collection of (adb shell am) instrument commands to run.  Each item in the
    test suite contains the command line options to pass to the instrument command
    which, in part, includes which set of tests to run.  app data is cleared between each test suite execution
    and "dangerous" permissions re-granted to prevent pop-ups.

    :param artifact_dir: directory where logs and screenshots are saved
    :param max_test_time: maximum allowed time for a single test to execute before timing out (or None)
    :param max_test_suite_time: maximum allowed time for test plan to complete to or None

    :raises ValueError: if max_test_suite_time is smaller than max_test_time
    :raises FileExistsError: if artifact_dir point to a file and not a directory
    :raises FileNotFoundError: if any of artifact_dir does not exist
    :raises FileNotFoundError: if adb_path is None and no adb executable can be found in PATH or under ANDROID_HOME

    >>> device = Device("device_serial_id")
    ... test_application = TestApplication.from_apk("/some/test.apk", device)
    ...
    ... class Listener(TestExecutionListener):
    ...     def test_ended(self, test_run_name: str, class_name: str, test_name: str, **kwargs) -> None:
    ...         print("Test %s passed" % test_name)
    ...
    ...     def test_failed(self, test_run_name: str, class_name: str, test_name: str, stack_trace: str) -> None:
    ...         print("Test %s failed" % test_name)
    ...
    ...     def test_ignored(self, test_run_name: str, class_name: str, test_name: str) -> None:
    ...         print("Test %s ignored" % test_name)
    ...
    ...     def test_assumption_failure(self, test_run_name: str, class_name: str, test_name: str,
    ...                                 stack_trace: str) -> None:
    ...         print("Test assumption failed, %s skipped" % test_name)
    ...
    ...     def test_suite_started(self, test_run_name: str, count: int = 0) -> None:
    ...         print("Test execution started: " + test_run_name)
    ...
    ...     def test_suite_ended(self, test_run_name: str, duration: float = -1.0, **kwargs) -> None:
    ...         print("Test execution ended")
    ...
    ...     def test_suite_failed(self, test_run_name: str, error_message: str) -> None:
    ...         print("Test execution failed with error message: %s" % error_message)
    ...
    ...
    ... async with AndroidTestOrchestrator(device_id="<some device/emulator id>", artifact_dir=".") as orchestrator:
    ...     test_suite = TestSuite('test_suite1', {"package": "com.some.test.package"})
    ...     test_plan = iter([test_suite])
    ...     orchestrator.add_test_listener(Listener())
    ...     orchestrator.execute_test_plan(test_application, test_plan)
    ...     # or
    ...     await orchestrator.execute_test_suite(test_suite)

    """

    def __init__(self,
                 artifact_dir: str,
                 max_test_time: Optional[float] = None,
                 max_test_suite_time: Optional[float] = None,
                 run_under_orchestration: bool = False) -> None:
        """
        :param artifact_dir: directory where logs and screenshots are saved
        :param max_test_time: maximum allowed time for a single test to execute before timing out (or None)
        :param max_test_suite_time: maximum allowed time for a suite to execut; or None
        :param run_under_orchestration: whether to run under Android Test Orchestrator or regular instument command

        :raises ValueError: if max_test_suite_time is smaller than max_test_time
        :raises FileExistsError: if artifact_dir point to a file and not a directory
        :raises FileNotFoundError: if artifact_dir does not exist
        """
        if max_test_suite_time is not None and max_test_time is not None and max_test_suite_time < max_test_time:
            raise ValueError("Max test suite time must be larger than max_test_time")
        if not os.path.exists(artifact_dir):
            raise FileNotFoundError("log dir '%s' not found" % artifact_dir)
        if not os.path.isdir(artifact_dir):
            raise FileExistsError("'%s' exists and is not a directory" % artifact_dir)

        self._artifact_dir = artifact_dir
        self._instrumentation_timeout = max_test_suite_time
        self._test_timeout = max_test_time
        self._timer = None
        self._tag_monitors: Dict[str, Tuple[str, LineParser]] = {}
        self._logcat_procs: Dict[Device, Any] = {}
        self._run_listeners: List[TestExecutionListener] = []
        self._run_under_orchestration = run_under_orchestration
        self._in_execution = False

    async def __aenter__(self) -> "AndroidTestOrchestrator":
        return self

    async def __aexit__(self,
                        exc_type: Optional[Type[BaseException]],
                        exc_val: Optional[BaseException],
                        exc_tb: Optional[TracebackType]) -> None:
        """
        cleanup
        """
        # leave the campground as clean as you left it:
        for proc in self._logcat_procs.values():
            await asyncio.wait_for(proc.stop(), timeout=10)
        self._logcat_procs = {}

    def add_test_listener(self, listener: TestExecutionListener) -> None:
        """
        Add given test run listener to listen for test run status updates
        :param listener: listener to add
        """
        if listener not in self._run_listeners:
            self._run_listeners.append(listener)

    def add_logcat_monitor(self, tag: str, handler: LineParser, priority: str = "*") -> None:
        """
        Add additional tag to be monitored out of logcat

        :param tag: tag to monitor
        :param handler:  handler to use to process lines of output under that tag
        :param priority: priority level of tag to watch, or "*" for all (see adb logcat usage)

        :raises Exception: if attempting to add a monitor to an ongoing test execution.  The only way this
           could happen is if a user defined task attempts to add additional tags to monitor

        :raises ValueError: if priority is invalid or is tag is already being monitored
        """
        if self._in_execution:
            raise Exception("Cannot add tag to monitor from logcat while a test suite is in progress")
        if priority not in ["I", "D", "E", "*"]:
            raise ValueError("Priority must be ont of 'I', 'D', 'E' or '*'")
        if tag in self._tag_monitors:
            raise ValueError("A handler for tag '%s' and priority '%s' already added" % (tag, priority))
        self._tag_monitors[tag] = (priority, handler)

    # TASK-2: parsing of instrument output for test execution status
    async def _execute_plan(self,
                            test_plan: AsyncIterator[TestSuite],
                            test_applications: List[TestApplication]) -> None:
        """
        Execute the given test plan, distributing test exeuction across the given test application instances

        :param test_plan: plan of test runs to execute
        :param test_applications: test application instances (each on a unique device) to execute test runs against
        """
        workers = [Worker(test_plan, test_app, artifact_dir=self._artifact_dir, listeners=self._run_listeners)
                   for test_app in test_applications]

        async def main_execution() -> None:
            """
            Launch worker coroutines to distribute the testing, and process any requested logcat tags as we go
            """
            # distributed testing:
            results = await asyncio.gather(
                *[worker.run(test_timeout=self._test_timeout,
                             under_orchestration=self._run_under_orchestration) for worker in workers],
                *[self._process_logcat_tags(test_app.device) for test_app in test_applications],
                return_exceptions=True)

            all_test_errors = [result for result in results[:len(test_applications)] if result is not None]
            all_logcat_errors = [result for result in results[len(test_applications):] if result is not None]
            if all_test_errors:
                text = '\n'.join([str(result) for result in all_test_errors])
                raise Exception(f"Failed to execute all tests properly {text};" +
                                "only first traceback shown") from all_test_errors[0]
            elif all_logcat_errors:
                text = '\n'.join([str(result) for result in all_logcat_errors])
                # worker thread had exception, so raise this as the most critical
                raise Exception(f"Failed to capture logact for all tests {text};" +
                                "only first traceback shown") from all_logcat_errors[0]
        await asyncio.wait_for(main_execution(), timeout=self._instrumentation_timeout)

    # TASK-3: monitor logcat for given tags in _tag_monitors
    async def _process_logcat_tags(self, device: Device) -> None:
        """
        Process requested tags from logcat

        :param device: remote device to process tags from
        """
        if not self._tag_monitors:
            return
        try:
            logcat_demuxer = LogcatTagDemuxer(self._tag_monitors)
            device_log = DeviceLog(device)
            keys = ['%s:%s' % (k, v[0]) for k, v in self._tag_monitors.items()]
            async with await device_log.logcat("-v", "brief", "-s", *keys) as proc:
                self._logcat_procs[device] = proc
                async for line in proc.output():
                    logcat_demuxer.parse_line(line)
                # proc is stopped by test execution coroutine

        except Exception as e:
            log.error("Exception on logcat processing, aborting: \n%s" % str(e))

    async def execute_test_plan(self,
                                test_application: TestApplication,
                                test_plan: Union[AsyncIterator[TestSuite], Iterator[TestSuite]]) -> None:
        """
        Execute a test plan (a collection of test suites) against a given test application running on a single device

        :param test_application: list of distributed TestApplication instances available against which tests will be run
        :param test_plan: iterator or async iterator of TestSuite's to be run under"adb instrument"

        :raises: asyncio.TimeoutError if test or test suite times out based on this orchestrator's configuration
        """

        await self.execute_test_plan_distributed([test_application], test_plan)

    async def execute_test_plan_distributed(self,
                                            test_appl_instances: List[TestApplication],
                                            test_plan: Union[AsyncIterator[TestSuite], Iterator[TestSuite]]) -> None:
        """
        Acts the same as `execute_test_plan` method, except across multiple test application instances distributed
        across multiple devices
        """
        try:
            self._in_execution = True
            if not isinstance(test_plan, AsyncIterator):
                async def _async_iter(test_plan: Iterator[TestSuite]) -> AsyncIterator[TestSuite]:
                    for item in test_plan:
                        yield item
                test_plan = _async_iter(test_plan)

            await asyncio.wait_for(self._execute_plan(test_plan=test_plan,
                                                      test_applications=test_appl_instances),
                                   timeout=self._instrumentation_timeout)

        finally:
            self._in_execution = False

    async def execute_test_suite(self,
                                 test_application: TestApplication,
                                 test_suite: TestSuite) -> None:
        """
        Execute a suite of tests as given by the argument list, and report test results

        :param test_application: single TestApplication against which tests will be executed
        :param test_suite: `TestSuite` to execute on remote device

        :raises asyncio.TimeoutError if test or test suite times out
        """
        await self.execute_test_plan(test_application,
                                     test_plan=iter([test_suite]))

    async def execute_test_suite_distributed(self,
                                             test_appl_instances: Union[TestApplication, List[TestApplication]],
                                             test_suite: TestSuite) -> None:
        """
        Execute a suite of tests as given by the argument list, and report test results

        :param test_appl_instances: list of TestApplication's on a distributed set of devices, against which
           tests will be run
        :param test_suite: `TestSuite` to execute on remote device

        :raises asyncio.TimeoutError if test or test suite times out
        """
        await self.execute_test_plan_distributed(test_appl_instances, test_plan=iter([test_suite]))
