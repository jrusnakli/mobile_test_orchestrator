import asyncio
import sys
from asyncio import Task

import logging
import os
import traceback
from dataclasses import dataclass, field
from types import TracebackType
from typing import Dict, Iterator, List, Tuple, Coroutine, Optional, Any, Type, AsyncIterator, \
    TypeVar, Union

from .application import TestApplication
from .device import Device
from .devicelog import DeviceLog, LogcatTagDemuxer
from .devicestorage import DeviceStorage
from .parsing import InstrumentationOutputParser, LineParser
from .reporting import TestRunListener
from .timing import Timer

log = logging.getLogger(__name__)


if sys.platform == 'win32':
    loop = asyncio.ProactorEventLoop()
    asyncio.set_event_loop(loop)


def trace(e: Exception) -> str:
    return str(e) + "\n\n" + traceback.format_exc()


T = TypeVar('T')


async def _preloading(it: AsyncIterator[T]) -> AsyncIterator[T]:
    """
    Wraps an async iterator to with one that pre-loads the next item in the background when yielding, rather than
    waiting for __anext__() to start fetching the next item.
    :param it: An async iterator
    :return: A preloading async iterator
    """
    try:
        next_suite = await it.__anext__()
        while True:
            task = asyncio.create_task(it.__anext__())
            yield next_suite
            next_suite = await task
    except StopAsyncIteration:
        return


@dataclass(frozen=True)
class TestSuite:
    """
    A dataclass representing a test suite that defines the attributes:

    *name*
        unique name of test suite
    *arguments*
        arguments to be passed to the am instrument command, run as
        "am instrument -w -r [arguments] <package>/<runner> "
    *uploadables*
        optional list of tuples of (loacl_path, remote_path) of test vector files to be uploaded to remote device
    """
    name: str
    arguments: List[str]
    uploadables: List[Tuple[str, str]] = field(default_factory=list)


# noinspection PyShadowingNames
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

    There are several background processes that are orchestrated during test suite execution:

    TASK-1.
        Logcat capture: Android provides a streaming device log useful for debugging.  This is captured directly
        to a file in the background.  Key markers (file positions at start and end of each test) are captured during a
        run as well.

    TASK-2.
        Test status capture and reporting: The output of test execution on the device is monitored in real-time and
        status provided via an instance of `androidtestorchestrator.reporting.TestRunListener`

    TASK-3.
        Processing commands from the test app to effect device changes: Apps running on a device do not have the
        permissions to effect various device changes required for test.  To allow test apps to conduct these device
        changes, a service is installed on the device that coordinates commands to the host, which does have such
        permissions -- over a physical, secure USB connection. A background process
        watches for and processes any commands issued during test execution, transparent to the client.


    >>> device = Device("device_serial_id")
    ... test_application = TestApplication("/some/test.apk", device)
    ... class Listener(TestRunListener):
    ...     def test_ended(self, class_name: str, test_name: str, **kwargs) -> None:
    ...         print("Test %s passed" % test_name)
    ...
    ...     def test_failed(self, class_name: str, test_name: str, stack_trace: str) -> None:
    ...         print("Test %s failed" % test_name)
    ...
    ...     def test_ignored(self, class_name: str, test_name: str) -> None:
    ...         print("Test %s ignored" % test_name)
    ...
    ...     def test_assumption_failure(self, class_name: str, test_name: str, stack_trace: str) -> None:
    ...         print("Test assumption failed, %s skipped" % test_name)
    ...
    ...     def test_run_started(self, test_run_name: str, count: int = 0) -> None:
    ...         print("Test execution started: " + test_run_name)
    ...
    ...     def test_run_ended(self, duration: float = -1.0, **kwargs) -> None:
    ...         print("Test execution ended")
    ...
    ...     def test_run_failed(self, error_message: str) -> None:
    ...         print("Test execution failed with error message: %s" % error_message)
    ...
    ...
    ... with AndroidTestOrchestrator(device_id="<some device/emulator id>", artifact_dir=".") as orchestrator:
    ...
    ...     test_suite = TestSuite('test_suite1', ["--package", "com.some.test.package"])
    ...     test_plan = iter([test_suite])
    ...     orchestrator.execute_test_plan(test_application, test_plan, Listener())
    ...     # or
    ...     orchestrator.execute_test_suite(test_suite, Listener())

    """

    def __init__(self,
                 artifact_dir: str,
                 max_test_time: Optional[float] = None,
                 max_test_suite_time: Optional[float] = None) -> None:
        """
        :param artifact_dir: directory where logs and screenshots are saved
        :param max_test_time: maximum allowed time for a single test to execute before timing out (or None)
        :param max_test_suite_time:maximum allowed time for a suite of tets (a package under and Android instrument
           command, for example) to execute; or None

        :raises ValueError: if max_test_suite_time is smaller than max_test_time
        :raises FileExistsError: if artifact_dir point to a file and not a directory
        :raises FileNotFoundError: if any of artifact_dir does not exist
        :raises FileNotFoundError: if adb_path is None and no adb executable can be found in PATH or under ANDROID_HOME
        """
        if max_test_suite_time is not None and max_test_time is not None and max_test_suite_time < max_test_time:
            raise ValueError("Max test suite time must be larger than max_test_time")
        if not os.path.exists(artifact_dir):
            raise FileNotFoundError("log dir '%s' not found" % artifact_dir)
        if not os.path.isdir(artifact_dir):
            raise FileExistsError("'%s' exists and is not a directory" % artifact_dir)

        self._artifact_dir = artifact_dir
        self._background_tasks: List[Task[Any]] = []
        self._instrumentation_timeout = max_test_suite_time
        self._test_timeout = max_test_time
        self._timer = None
        self._tag_monitors: Dict[str, Tuple[str, LineParser]] = {}
        self._logcat_proc = None
        self._result_listeners = []

    def __enter__(self) -> "AndroidTestOrchestrator":
        return self

    def __exit__(self, exc_type: Optional[Type[BaseException]], exc_val: Optional[BaseException],
                 exc_tb: Optional[TracebackType]) -> None:
        """
        cleanup
        """
        # leave the campground as clean as you left it:
        if self._logcat_proc is not None:
            asyncio.wait_for(self._logcat_proc.stop(), timeout=10)

    def add_test_listener(self, listener: TestRunListener) -> None:
        """
        Add given test run listener to listen for test run status updates
        :param listener: listener to add
        """
        if listener not in self._result_listeners:
            self._result_listeners.append(listener)

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
        if asyncio.get_event_loop().is_running():
            raise Exception("Cannot add tag to monitor from logcat while a test suite is in progress")
        if priority not in ["I", "D", "E", "*"]:
            raise ValueError("Priority must be ont of 'I', 'D', 'E' or '*'")
        if tag in self._tag_monitors:
            raise ValueError("A handler for tag '%s' and priority '%s' already added" % (tag, priority))
        self._tag_monitors[tag] = (priority, handler)

    # TASK-2: parsing of instrument output for test execution status
    async def _execute_plan(self,
                            test_plan: AsyncIterator[TestSuite],
                            test_application: TestApplication) -> None:

        def apply(methodname, *args, **kargs):
            for listener in self._result_listeners:
                method = getattr(listener, methodname)
                method(*args, **kargs)

        # clear logcat for fresh start
        try:
            DeviceLog(test_application.device).clear()
        except Device.CommandExecutionFailureException as e:
            # sometimes logcat -c will give and error "failed to clear 'main' log';  Ugh
            log.error("clearing logcat failed: " + str(e))

        instrumentation_parser = InstrumentationOutputParser(self._result_listeners)

        # add timer that times timeout if any INDIVIDUAL test takes too long
        if self._test_timeout is not None:
            instrumentation_parser.add_test_execution_listener(Timer(self._test_timeout))

        # TASK-3: capture logcat to file and markers for beginning/end of each test
        device_log = DeviceLog(test_application.device)
        device_storage = DeviceStorage(test_application.device)

        with device_log.capture_to_file(output_path=os.path.join(self._artifact_dir, "logcat.txt")) as log_capture:
            # log_capture is to listen to test status to mark beginning/end of each test run:
            instrumentation_parser.add_test_execution_listener(log_capture)
            try:
                async for test_run in _preloading(test_plan):
                    apply(self._result_listeners, "test_run_started", test_run.name)
                    try:
                        for local_path, remote_path in test_run.uploadables:
                            device_storage.push(local_path=local_path, remote_path=remote_path)
                        async with await test_application.run(*test_run.arguments) as proc:
                            async for line in proc.output(unresponsive_timeout=self._test_timeout):
                                instrumentation_parser.parse_line(line)
                            proc.wait(timeout=self._test_timeout)
                    except Exception as e:
                        print(trace(e))
                        apply(self._result_listeners, "test_run_failed", str(e))
                    finally:
                        apply(self._result_listeners, "test_run_ended", instrumentation_parser.execution_time)
                        for _, remote_path in test_run.uploadables:
                            try:
                                device_storage.remove(remote_path, recursive=True)
                            except Exception:
                                log.error("Failed to remove temporary test vector %s from device" % remote_path)
            finally:
                if self._logcat_proc is not None:
                    await self._logcat_proc.stop()
                    self._logcat_proc = None

            # capture logcat markers (begin/end of each test/test suite)
            marker_output_path = os.path.join(self._artifact_dir, 'log_markers.txt')
            if os.path.exists(marker_output_path):
                os.remove(marker_output_path)
            with open(marker_output_path, 'w') as f:
                for marker, pos in log_capture.markers.items():
                    f.write("%s=%s\n" % (marker, str(pos)))

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
                self._logcat_proc = proc
                async for line in proc.output():
                    logcat_demuxer.parse_line(line)
                # proc is stopped by test execution coroutine

        except Exception as e:
            log.error("Exception on logcat processing, aborting: \n%s" % trace(e))

    def execute_test_plan(self,
                          test_application: TestApplication,
                          test_plan: Union[AsyncIterator[TestSuite], Iterator[TestSuite]],
                          global_uploadables: Optional[List[Tuple[str, str]]] = None) -> None:
        """
        Execute a test plan (a collection of test suites)

        :param test_application:  test application to run
        :param test_plan: iterator or async iterator with each element being a tuple of test suite name and list of
           string arguments to provide to an execution of "adb instrument".  The test suit name is used to report start
           and end of each test suite via the test_listeners of this object

        :raises: asyncio.TimeoutError if test or test suite times out based on this orchestrator's configuration
        """
        loop = asyncio.get_event_loop()
        device_storage = DeviceStorage(test_application.device)

        if not isinstance(test_plan, AsyncIterator):
            async def _async_iter(test_plan: Iterator[TestSuite]) -> AsyncIterator[TestSuite]:
                for i in test_plan:
                    yield i
            test_plan = _async_iter(test_plan)

        # ADD  USER-DEFINED TASKS
        async def timer(test_plan: AsyncIterator[TestSuite]) -> None:
            """
            Timer to timeout if future is not presented in given timeout for overall test suite execution
            """
            for local_path, remote_path in global_uploadables or []:
                device_storage.push(local_path=local_path, remote_path=remote_path)

            async def run() -> None:
                await asyncio.wait([self._execute_plan(test_plan=test_plan,
                                                       test_application=test_application),
                                    self._process_logcat_tags(test_application.device)])
            await asyncio.wait_for(run(), timeout=self._instrumentation_timeout)
        try:
            loop.run_until_complete(timer(test_plan))  # execute plan until completed or until timeout is reached
        finally:
            for _, remote_path in global_uploadables or []:
                device_storage.remove(remote_path, recursive=True)

    def execute_test_suite(self,
                           test_application: TestApplication,
                           test_suite: TestSuite,
                           global_uploadables: Optional[List[Tuple[str, str]]] = None) -> None:
        """
        Execute a suite of tests as given by the argument list, and report test results

        :param test_application: test application to be executed on remote device
        :param test_suite: `TestSuite` to execute on remote device
        :param global_uploadables: list of tuples of (local_path, remote_path) of files/dirs to upload to device or None
           The files will stay on the device until the entire test plan is completed.  Note that each test suite
           can also specify uploadables that are pushed and then removed once the suite is completed.  The choice is
           a trade-off of efficiency vs storage utilization

        :raises asyncio.TimeoutError if test or test suite times out
        """
        self.execute_test_plan(test_application,
                               test_plan=iter([test_suite]),
                               global_uploadables=global_uploadables)

    def add_background_task(self, coroutine: Coroutine[Any, Any, Any]) -> None:
        """
        Add a user-define background task to be executed during test run.  Note that the coroutine
        will not be invoked until a call to `execute_test_plan` is called, and will be canceled
        upon completion of a test run execution

        :param coroutine: coroutine to be executed during asyncio even loop execution of tests
        """
        self._background_tasks.append(asyncio.get_event_loop().create_task(coroutine))
