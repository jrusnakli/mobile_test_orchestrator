import asyncio
import sys

import logging
import os
from dataclasses import dataclass, field
from types import TracebackType
from typing import AsyncIterator, Dict, Iterator, List, Optional, Tuple, Type, Union, Coroutine

from .testprep import EspressoTestSetup
from .application import TestApplication
from .device import Device
from androidtestorchestrator.devicepool import AsyncDevicePool
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
    ...     def test_started(self, test_run_name: str, class_name: str, test_name: str):
    ...         print(f"Test {class_name}::{test_name} for suite {test_run_name} started")
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
    ... async def launch_emulators_and_run():
    ...    setup = EspressoTestSetup(path_to_apk="/a/path/to/an/apk/file",
    ...                              path_to_test_apk="/path/to/corresponding/test/apk")
    ...    emulator_config = EmulatorBundleConfig(...)
    ...    emulator_q = AsyncEmulatorPool.create(count, emulator_config)
    ...    #
    ...    # call other methods on setup to prepare device for testing as needed...
    ...    #
    ...    async with AndroidTestOrchestrator(,
    ...          artifact_dir=os.get_cwd(),
    ...          max_device_count = 4,
    ...          max_test_time = 5*60,  # five minutes
    ...          max_test_suite_time = 1*60*60,  # one hour
    ...          run_under_orchestration= False) as orchestrator:
    ...        test_suite = TestSuite('test_suite1', {"package": "com.some.test.package"})
    ...        test_plan = iter([test_suite])
    ...        orchestrator.add_test_listener(Listener())
    ...        await orchestrator.execute_test_plan(
    ...            test_setup=setup,
    ...            test_plan=test_plan,
    ...            devices = emulator_q
    ...        )
    ...        # or
    ...        await orchestrator.execute_single_test_suite(
    ...           test_suite=test_suite
    ...           devices=emulator_q,
    ...           test_setup=setup
    ...        )

    """

    def __init__(self,
                 artifact_dir: str,
                 max_device_count: Optional[int] = None,
                 max_test_time: Optional[float] = None,
                 max_test_suite_time: Optional[float] = None,
                 run_under_orchestration: bool = False) -> None:
        """
        :param artifact_dir: directory where logs and screenshots are saved
        :param max_device_count: max number of devices to utilize, or None for unbounded
        :param max_test_time: maximum allowed time for a single test to execute before timing out (or None)
        :param max_test_suite_time: maximum allowed time for a suite to execute; or None
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
        self._max_device_count = max_device_count
        self._instrumentation_timeout = max_test_suite_time
        self._test_timeout = max_test_time
        self._timer = None
        self._tag_monitors: Dict[str, Tuple[str, LineParser]] = {}
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
        pass  # nothing do for now

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

    async def run(self,
                  device: Device,
                  test_setup: EspressoTestSetup,
                  test_plan: Union[Iterator[TestSuite], AsyncIterator[TestSuite]],
                  completion_callback: Optional[Coroutine[None, None, None]] = None) -> None:
        """
        Run a collection test suites against a single device, pulling each test suite from an externally supplied iterator

        :param test_setup: information to prepare device for test execution
        :param device: device to run against
        :param test_plan: iterator of test suites to pull from
        :param completion_callback: coroutine to be awaited upon completion, or None
        """
        # monitor requested logcat tags
        worker = Worker(device=device,
                        tests=test_plan,
                        test_setup=test_setup,
                        artifact_dir=self._artifact_dir,
                        listeners=self._run_listeners)
        await worker.run(
            completion_callback=completion_callback,
            under_orchestration=self._run_under_orchestration,
            test_timeout=self._test_timeout,
            monitor_tags=self._tag_monitors
        )

    async def run_single_test_suite(self,
                                    test_setup: EspressoTestSetup,
                                    device: Device,
                                    test_suite: TestSuite,
                                    completion_callback: Optional[Coroutine[None, None, None]] = None) -> None:
        """
        Convenience method to execute a single test suite against a single device

        :param test_setup: information to prepare device for test execution
        :param device: which device to run against
        :param test_suite: the test suite to run
        :param completion_callback: coroutine to be awaited upon completion, or None
        """
        await self.run(test_setup=test_setup, device=device, test_plan=iter([test_suite]),
                       completion_callback=completion_callback)

    async def execute_test_plan(self,
                                test_setup: EspressoTestSetup,
                                devices: AsyncDevicePool,
                                test_plan: Union[Iterator[TestSuite], AsyncIterator[TestSuite]]) -> None:
        """
        Execute the given test plan, distributing test exeuction across the given test application instances

        :param test_setup: used to set up the test apk, target apk and such
        :param devices: queue to reserve devices to run on
        :param test_plan: plan of test runs to execute
        """
        have_tests_to_process = True
        # see comment on acquire() below
        sem = asyncio.Semaphore(0)

        async def worker_completion():
            nonlocal have_tests_to_process
            have_tests_to_process = False

        async def run_loop():
            worker_tasks = []
            while have_tests_to_process and (self._max_device_count is None or len(worker_tasks) < self._max_device_count):
                # call worker to start processing and running tests from the test plan
                # (completes when all tests ar exhausted)

                async def do_work():
                    async with devices.reserve() as device:
                        sem.release()
                        if not have_tests_to_process:
                            return
                        await self.run(
                            device=device,
                            test_setup=test_setup,
                            test_plan=test_plan,
                            completion_callback=worker_completion()
                        )
                if have_tests_to_process:
                    task = asyncio.get_event_loop().create_task(do_work())
                    worker_tasks.append(task)
                    # synchronize, to ensure we don't spin our wheels and that we wait for the device to first
                    # be reserved.  This is a little quirky, but it also allows the provision of having a simpler
                    # and safer API on the AsyncDevicePool that ensures reserved devices are always relinquished
                    # upon completion
                    await sem.acquire()
            results, pending = await asyncio.wait(worker_tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in pending:
                task.cancel()
            [r.result() for r in results]  # will raise any exception caught during task execution
        await asyncio.wait_for(run_loop(), timeout=self._instrumentation_timeout)
        log.info("Test execution completed")

    async def execute_single_test_suite(self,
                                        test_setup: EspressoTestSetup,
                                        devices: AsyncDevicePool,
                                        test_suite: TestSuite) -> None:
        """
        Convenience method to execute a single test suite

        :param test_setup: test configuration used to prep device for test execution
        :param devices: DeviceQueue to pull devices from for test execution
        :param test_suite: `TestSuite` to execute on remote device

        :raises asyncio.TimeoutError if test or test suite times out
        """
        await self.execute_test_plan(test_setup,
                                     devices,
                                     test_plan=iter([test_suite]))
