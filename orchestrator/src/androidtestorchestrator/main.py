import asyncio
import sys

import logging
import os
from contextlib import suppress
from dataclasses import dataclass, field
from types import TracebackType
from typing import Dict, Iterator, List, Tuple, Coroutine, Optional, Any, Type, AsyncIterator, Union

from .application import TestApplication
from .device import Device, DeviceSet
from .devicelog import DeviceLog, LogcatTagDemuxer
from .devicestorage import DeviceStorage
from .parsing import InstrumentationOutputParser, LineParser
from .reporting import TestExecutionListener
from .timing import Timer

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
        status provided via an instance of `androidtestorchestrator.reporting.TestListener`

    TASK-3.
        Processing commands from the test app to take action on the host side (host that is hosting the device or
        emulator).


    >>> device = Device("device_serial_id")
    ... test_application = TestApplication("/some/test.apk", device)
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
    ...     def test_assumption_failure(self, test_run_name: str, class_name: str, test_name: str, stack_trace: str) -> None:
    ...         print("Test assumption failed, %s skipped" % test_name)
    ...
    ...     def test_run_started(self, test_run_name: str, count: int = 0) -> None:
    ...         print("Test execution started: " + test_run_name)
    ...
    ...     def test_run_ended(self, test_run_name: str, duration: float = -1.0, **kwargs) -> None:
    ...         print("Test execution ended")
    ...
    ...     def test_run_failed(self, test_run_name: str, error_message: str) -> None:
    ...         print("Test execution failed with error message: %s" % error_message)
    ...
    ...
    ... with AndroidTestOrchestrator(device_id="<some device/emulator id>", artifact_dir=".") as orchestrator:
    ...
    ...     test_suite = TestSuite('test_suite1', ["--package", "com.some.test.package"])
    ...     test_plan = iter([test_suite])
    ...     orchestrator.add_test_listener(Listener())
    ...     orchestrator.execute_test_plan(test_application, test_plan)
    ...     # or
    ...     orchestrator.execute_test_suite(test_suite)

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
        self._background_tasks: List[Coroutine[List[Any], Any, Any]] = []
        self._instrumentation_timeout = max_test_suite_time
        self._test_timeout = max_test_time
        self._timer = None
        self._tag_monitors: Dict[str, Tuple[str, LineParser]] = {}
        self._logcat_procs: Dict[Device, Any] = {}
        self._run_listeners: List[TestExecutionListener] = []

    def __enter__(self) -> "AndroidTestOrchestrator":
        return self

    def __exit__(self, exc_type: Optional[Type[BaseException]], exc_val: Optional[BaseException],
                 exc_tb: Optional[TracebackType]) -> None:
        """
        cleanup
        """
        # leave the campground as clean as you left it:
        for proc in self._logcat_procs.values():
            asyncio.wait_for(proc.stop(), timeout=10)

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
                            test_applications: List[TestApplication]) -> None:
        """
        Execute the given test plan, distributing test exeuction across the given test application instances

        :param test_plan: plan of test runs to execute
        :param test_applications: test application instances (each on a unique device) to execute test runs against
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=len(test_applications))  # type: ignore

        async def populate_q() -> None:
            """
            Called in coordinator coroutine to populate the test queue, posting None to all worker coroutines
            to signal end
            """
            async for test_run in test_plan:
                await queue.put(test_run)
            for _ in test_applications:
                await queue.put(None)  # signals completion

        async def queue_iterator() -> AsyncIterator[TestSuite]:
            """
            When more than one test application is specified, this is run by each worker coroutine to pop
            the next available test off of the queue
            """
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item

        async def main_execution() -> None:
            """
            Launch worker coroutines to distribute the testing, and process any requested logcat tags as we go
            """
            if len(test_applications) > 1:
                # distributed testing:
                results = await asyncio.gather(
                    populate_q(),
                    *[self._worker(queue_iterator(), test_app) for test_app in test_applications],
                    *[self._process_logcat_tags(test_app.device) for test_app in test_applications],
                    return_exceptions=True)
                if results[1] is not None:
                    # worker thread had exception, so raise this as the most critical
                    raise Exception("Failed to execute all tests properly") from results[1]
                elif results[2] is not None:
                    raise Exception("Failed to process all logcat commands from device test execution") from results[2]
                elif results[0] is not None:
                    raise Exception("Failed to distribute all tests for execution") from results[0]
            elif len(test_applications) == 1:
                # serial testing, since only one test app:
                test_app = test_applications[0]
                results = await asyncio.gather(self._worker(test_plan, test_app),
                                               self._process_logcat_tags(test_app.device),
                                               return_exceptions=True)
                if results[0] is not None:
                    # worker thread had exception, so raise this as the most critical
                    raise Exception("Failed to execute all tests properly") from results[0]
                elif results[1] is not None:
                    raise Exception("Failed to process all logcat commands from device test execution") from results[1]
            else:
                raise Exception("No test applications provided to test against!!")
        await asyncio.wait_for(main_execution(), timeout=self._instrumentation_timeout)

    async def _worker(self, iterator: AsyncIterator, test_application: TestApplication) -> None:  # type: ignore
        """
        Worker coroutine where test execution against a given test application (on a single device) happens
        :param iterator: where to pull the next test run (suite) from
        :param test_application: test application instance to use for execution
        """
        def signal_listeners(methodname: str, *args: Any, **kargs: Any) -> Any:
            """
            apply the given method with given args across the full collection of listeners
            :param methodname: which method to invoke
            :param args: args to pass to method
            :param kargs: keyword args to pass to method
            :return: return value from method
            """
            for listener in self._run_listeners:
                method = getattr(listener, methodname)
                method(*args, **kargs)

        # TASK-3: capture logcat to file and markers for beginning/end of each test
        device_log = DeviceLog(test_application.device)
        device_storage = DeviceStorage(test_application.device)
        logcat_output_path = os.path.join(self._artifact_dir, f"logcat-{test_application.device.device_id}.txt")
        with device_log.capture_to_file(output_path=logcat_output_path):
            # log_capture is to listen to test status to mark beginning/end of each test run:
            try:
                async for test_run in iterator:
                    signal_listeners("test_run_started", test_run.name)
                    instrumentation_parser = InstrumentationOutputParser(test_run.name)
                    instrumentation_parser.add_execution_listeners(self._run_listeners)
                    # add timer that times timeout if any INDIVIDUAL test takes too long
                    if self._test_timeout is not None:
                        instrumentation_parser.add_simple_test_listener(Timer(self._test_timeout))
                    try:
                        # push test vectors, if any, to device
                        for local_path, remote_path in test_run.uploadables:
                            device_storage.push(local_path=local_path, remote_path=remote_path)
                        # run tests on the device, and parse output
                        test_args = []
                        for key, value in test_run.test_parameters.items():
                            test_args += ["-e", key, value]
                        async with await test_application.run(*test_args) as proc:
                            async for line in proc.output(unresponsive_timeout=self._test_timeout):
                                instrumentation_parser.parse_line(line)
                            proc.wait(timeout=self._test_timeout)
                    except Exception as e:
                        log.error("Test run failed \n%s", str(e))
                        signal_listeners("test_run_failed", str(e))
                    finally:
                        signal_listeners("test_run_ended", instrumentation_parser.execution_time)
                        for _, remote_path in test_run.uploadables:
                            try:
                                device_storage.remove(remote_path, recursive=True)
                            except Exception:
                                log.error("Failed to remove temporary test vector %s from device" % remote_path)
            finally:
                if test_application.device in self._logcat_procs:
                    asyncio.wait_for(self._logcat_procs[test_application.device].stop(), timeout=10)
                    self._logcat_procs[test_application.device].remove(test_application.device)

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

    def execute_test_plan(self,
                          test_application: TestApplication,
                          test_plan: Union[AsyncIterator[TestSuite], Iterator[TestSuite]],
                          global_uploadables: Optional[List[Tuple[str, str]]] = None) -> None:
        """
        Execute a test plan (a collection of test suites) against a given test application running on a single device

        :param test_application: list of distributed TestApplication instances available against which tests will be run
        :param test_plan: iterator or async iterator with each element being a tuple of test suite name and list of
           string arguments to provide to an execution of "adb instrument".  The test suit name is used to report start
           and end of each test suite via the test_listeners of this object
        :param global_uploadables: files (for test) to upload and accessible across all tests

        :raises: asyncio.TimeoutError if test or test suite times out based on this orchestrator's configuration
        """

        self.execute_test_plan_distributed([test_application], test_plan, global_uploadables)

    def execute_test_plan_distributed(self,
                                      test_appl_instances:List[TestApplication],
                                      test_plan: Union[AsyncIterator[TestSuite], Iterator[TestSuite]],
                                      global_uploadables: Optional[List[Tuple[str, str]]] = None) -> None:
        """
        Execute a test plan (a collection of test suites) across multiple instantitions of a test application runing
        across a set of distributed devices

        :param test_appl_instances: list of distributed TestApplication instances available against which tests will be run
        :param test_plan: iterator or async iterator with each element being a tuple of test suite name and list of
           string arguments to provide to an execution of "adb instrument".  The test suit name is used to report start
           and end of each test suite via the test_listeners of this object
        :param global_uploadables: files (for test) to upload and accessible across all tests

        :raises: asyncio.TimeoutError if test or test suite times out based on this orchestrator's configuration
        """
        if not isinstance(test_plan, AsyncIterator):
            async def _async_iter(test_plan: Iterator[TestSuite]) -> AsyncIterator[TestSuite]:
                for item in test_plan:
                    yield item
            test_plan = _async_iter(test_plan)

        # push uploadables to device
        async def push(device: Device) -> None:
            device_storage = DeviceStorage(device)
            for local_path, remote_path in global_uploadables or []:
                await device_storage.push_async(local_path=local_path, remote_path=remote_path,
                                                timeout=5*60)

        async def gather() -> None:
            device_set = DeviceSet([test_app.device for test_app in test_appl_instances])
            await device_set.apply_concurrent(push)

        if global_uploadables:
            asyncio.run(gather())

        # execute the main attraction!
        background_tasks: List[asyncio.Task[Any]] = []

        async def main(test_plan: AsyncIterator[TestSuite]) -> None:
            """
            Timer to timeout if future is not presented in given timeout for overall test suite execution
            """
            # ADD  USER-DEFINED TASKS
            for coroutine in self._background_tasks:
                background_tasks.append(asyncio.create_task(coroutine))

            await asyncio.wait_for(self._execute_plan(test_plan=test_plan,
                                                      test_applications=test_appl_instances),
                                   timeout=self._instrumentation_timeout)

        try:
            asyncio.run(main(test_plan))  # execute plan until completed or until timeout is reached
        finally:
            for task in background_tasks:
                with suppress(Exception):
                    task.cancel()
            for device in [test_app.device for test_app in test_appl_instances]:
                device_storage = DeviceStorage(device)
                for _, remote_path in global_uploadables or []:
                    device_storage.remove(remote_path, recursive=True)

    def execute_test_suite(self,
                           test_application: TestApplication,
                           test_suite: TestSuite,
                           global_uploadables: Optional[List[Tuple[str, str]]] = None) -> None:
        """
        Execute a suite of tests as given by the argument list, and report test results

        :param test_application: single TestApplication against which tests will be executed
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

    def execute_test_suite_distributed(self,
                                       test_appl_instances: Union[TestApplication, List[TestApplication]],
                                       test_suite: TestSuite,
                                       global_uploadables: Optional[List[Tuple[str, str]]] = None) -> None:
        """
        Execute a suite of tests as given by the argument list, and report test results

        :param test_appl_instances: list of TestApplication instancess on a distributed set of devices, against whicfh
           tests will be run
        :param test_suite: `TestSuite` to execute on remote device
        :param global_uploadables: list of tuples of (local_path, remote_path) of files/dirs to upload to device or None
           The files will stay on the device until the entire test plan is completed.  Note that each test suite
           can also specify uploadables that are pushed and then removed once the suite is completed.  The choice is
           a trade-off of efficiency vs storage utilization

        :raises asyncio.TimeoutError if test or test suite times out
        """
        self.execute_test_plan_distributed(test_appl_instances,
                                           test_plan=iter([test_suite]),
                                           global_uploadables=global_uploadables)

    def add_background_task(self, coroutine: Coroutine[Any, Any, Any]) -> None:
        """
        Add a user-define background task to be executed during test run.  Note that the coroutine
        will not be invoked until a call to `execute_test_plan` is called, and will be canceled
        upon completion of a test run execution

        :param coroutine: coroutine to be executed during asyncio even loop execution of tests
        """
        self._background_tasks.append(coroutine)
