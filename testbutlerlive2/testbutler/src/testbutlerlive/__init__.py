import asyncio
import logging
import os
import sys
from contextlib import suppress

import pkg_resources

from typing import (Dict,
                    Generator,
                    List,
                    Tuple,
                    Union,
                    Coroutine)

from .application import TestApplication, ServiceApplication, Application
from .device import Device
from .devicelog import DeviceLog
from testbutlerlive.devicelog import LogcatTagDemuxer
from .parsing import (InstrumentationOutputParser,
                      LineParser,
                      TestButlerCommandParser)
from .timing import StopWatch, Timer
from .reporting import TestListener

log = logging.getLogger(__name__)


def TEST_BUTLER_APK():
    butler_apk =pkg_resources.resource_filename(__name__, os.path.join("resources", "apks", "TestButlerLive.apk"))
    assert os.path.exists(butler_apk), "Invalid distribution! %s does not exist!!" % butler_apk
    return butler_apk


if sys.platform == 'win32':
    loop = asyncio.ProactorEventLoop()
    asyncio.set_event_loop(loop)


def trace(e: Exception):
    import traceback
    return str(e) + "\n\n" + traceback.format_exc()


# noinspection PyShadowingNames
class AndroidTestOrchestrator:
    """
    Class for orchestrating interactions with a device or emulator during execution of a test or suite of tests.

    There are several background processes that are orchestrated during test suite execution:

    TASK-1. Logcat capture: Android provides a streaming device log useful for debugging.  This is captured directly
         to a file in the background.  Key markers (file positions at start and end of each test) are captured during a
         run as well.

    TASK-2. Test status capture and reporting: Each process-invocation of a test suite running on the given remote
         device is monitored in parallel with other background tasks.  The output of such a process contains tests
         status, including start and end of the test suite, when each test starts and ends and the status of each test.
         This status is reported through a client-defined listener

    TASK-3. Processing test commands to effect device changes: Apps running on a device do not have the permissions to
         effect various device changes required for test.  To allow test apps to conduct these device changes,
         a service is installed on the device that coordinates commands to the host, which does have such permissions
         -- over a physical, secure USB connection. A background process is launched that
         watches for and executes any commands issued during test execution, transparent to the client.

         The client can specify which TestButler
         service (see `https://github.com/linkedin/test-butler`) to use, and if not specified will use a default
         internally defined service.  Note that for emaulaors, the latter is probably less efficient, but the former
         does not work on un-rooted (real) devices.

    This class is the sole interface necessary for executing test suites in this manner:

    >>> device = Device("device_serial_id")
    ... test_application = TestApplication("/some/test.apk", device)
    ... with AndroidTestOrchestrator(device_id="<some device/emulator id>", artifact_dir=".") as orchestrator:
    ...     class Listener(TestListener):
    ...         def test_ended(self, test_name: str, test_class: str, test_no: int, duartion: float, msg: str = ""):
    ...             print("Test %s passed" % test_name)
    ...
    ...         def test_failed(self, test_name: str, test_class: str, test_no: int, stack: str, msg: str = ""):
    ...             print("Test %s failed" % test_name)
    ...
    ...         def test_ignored(self, test_name: str, test_class: str, test_no: int, msg: str = ""):
    ...             print("Test %s skipped" % test_name)
    ...
    ...         def test_assumption_violated(self, test_name: str, test_class: str, test_no: int, reason: str):
    ...             print("Test %s skipped" % test_name)
    ...
    ...         def test_suite_started(self, test_suite_name:str):
    ...             print("Test execution started: " + test_suite_name)
    ...
    ...         def test_suite_ended(self, test_suite_name: str, test_count: int):
    ...             print("Test execution ended: " + test_suite_name)
    ...
    ...         def test_suite_errored(self, test_suite_name: str, status_code: int):
    ...             print("Test execution of %s errored with status code: %d" % (test_suite_name, status_code))
    ...
    ...     orchestrator.execute_test_plan(test_application,
    ...                                    iter([("test_suite1", ["--package", "com.some.test.package"])], Listener())
    ...     # or
    ...     orchestrator.execute_test_suite(["--package", "com.some.test.package"], Listener())
    """

    # noinspection PyShadowingNames
    class _DeviceRestoration(TestButlerCommandParser.DeviceChangeListener):
        """
        Internal class to capture settings/properties changed during each test suite execution and restore original
        values
        """
        def __init__(self, adb: Device):
            """
            :param adb:  Android deice bridge used to communicate with device under test
            """
            self._device = adb
            self._restoration_properties = {}
            self._restoration_settings = {}
            self._restoration_permissions = {}

        def device_setting_changed(self, namespace, key, previous, new_value):
            """
            capture device setting change (first change only) during testing,
            to restore to original value on __exit__
            :param namespace: setting's namespace
            
            :param key: key for setting
            :param previous: previous value before change
            :param new_value: new value setting was changed to
            """
            if (namespace, key) not in self._restoration_settings:
                self._restoration_settings[(namespace, key)] = previous
            elif self._restoration_settings[(namespace, key)] == new_value:
                # no longer need to restore
                del self._restoration_settings[(namespace, key)]

        def device_property_changed(self, key, previous, new_value):
            """
            capture device property change (first change only) during testing,
            to restore original value on __exit__
            
            :param key: name of property that changed
            :param previous: previous value
            :param new_value: new value property was set to
            """
            if key not in self._restoration_properties:
                self._restoration_properties[key] = previous
            elif self._restoration_properties[key] == new_value:
                # no longer need to restore
                del self._restoration_properties[key]

        def restore(self):
            """
            Restore original values for any changed settings and properties
            """
            for (namespace, key), original_value in self._restoration_settings.items():
                self._device.set_device_setting(namespace, key, original_value)
            for key, original_value in self._restoration_properties.items():
                self._device.set_system_property(key, original_value)
            self._restoration_properties = {}
            self._restoration_settings = {}

    def __init__(self,
                 device: Device,
                 artifact_dir: str,
                 test_butler_apk_path: Union[str, None]=None,
                 max_test_time: Union[float, None]=None,
                 max_test_suite_time: Union[float, None]=None):
        """
        :param device: remote device to conduct executions
        :param artifact_dir: directory where logs and screenshots are saved
        :param test_butler_apk_path: path to external test butler apk to use (e.g. for emulators);
           or None to use real-device TestButler apk
        :param max_test_time: maximum allowed time for a single test to execute before timing out (or None)
        :param max_test_suite_time:maximum allowed time for a suite of tets (a package under and Android instrument
           command, for example) to execute; or None

        :raises ValueError: if max_test_suite_time is smaller than max_test_time
        :raises FileExistsError: if artifact_dir point to a file and not a directory
        :raises FileNotFoundError: if any of artifact_dir does not exist
        :raises FileNotFoundError: if  adb_path (if not None) or test_butler_apk_path (id not None) does not exist
        :raises FileNotFoundError: if adb_path is None and no adb executable can be found in PATH or under ANDROID_HOME
        """
        if max_test_suite_time is not None and max_test_time is not None and max_test_suite_time < max_test_time:
            raise ValueError("Max test suite time must be larger than max_test_time")
        if not os.path.exists(artifact_dir):
            raise FileNotFoundError("log dir '%s' not found" % artifact_dir)
        if not os.path.isdir(artifact_dir):
            raise FileExistsError("'%s' exists and is not a directory" % artifact_dir)
        if test_butler_apk_path is not None and not os.path.exists(test_butler_apk_path):
            raise FileNotFoundError("test butler apk specified, '%s', does not exists" % test_butler_apk_path)

        self._artifact_dir = artifact_dir
        self._background_coroutines = []
        self._instrumentation_timeout = max_test_suite_time
        self._test_timeout = max_test_time
        self._test_butler_apk_path = test_butler_apk_path or TEST_BUTLER_APK()
        self._timer = None
        # for
        self._test_butler_service = None

        self._device = device
        self._tag_monitors: Dict[str, Tuple[str, LineParser]] = {}

    def __enter__(self):
        """
        starts log capture and test butler command processing (logcat processing) in background
        :return:
        """
        self._test_butler_service = ServiceApplication.install(self._test_butler_apk_path, self._device)
        DeviceLog(self._device).clear()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        1. restore original device settings
        2. stop logcat capture to file
        3. uninstall test butler app
        4. cleanup any active background processes
        """
        # stop background processes and ensure fd's are closed
        self._test_butler_service.uninstall()

        # leave the campground as clean as you left it:
        if self._test_butler_service is not None:
            self._test_butler_service.uninstall()

    def add_logcat_monitor(self, tag: str, handler: LineParser, priority: str= "*"):
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

    # TASK-2: parsing of instrument output for test execuition status
    async def _execute_plan(self,
                            test_plan: Generator[Tuple[str, List[str]], None, None],
                            test_application: TestApplication,
                            test_listener: TestListener,
                            device_restoration: "AndroidTestOrchestrator._DeviceRestoration"):
        """
        Loop over items in test plan and execute one by one, restoring device settings and properties on each
        iteration.

        :param test_plan: generator of tuples of (test_suite_name, list_of_instrument_arguments)
        :param test_application: test application containing (remote) runner to execute tests
        :param device_restoration: to restore device on each iteration
        """

        async def single_buffered_test_suite():
            """
            This coroutine buffers one item out of the test plan to be immediately available

            This is to accommodate the case where iteration over each item in the plan could take a
            little bit of processing time (for example, being asynchronously pulled from a file).  It is
            expected that this shouldn't take much time (10s of ms), but over 1000s of tests this allows
            that overhead of getting the next test to be absorbed while a test is executing on the remote
            device
            """
            test_suite_name, instrument_args = next(test_plan, (None, None))
            if test_suite_name is None:
                log.error("Encountered empty test plan.  Ain't nothing to be done!")
            for next_item in test_plan:
                yield test_suite_name, instrument_args
                test_suite_name, instrument_args = next_item
            yield test_suite_name, instrument_args

        instrumentation_parser = InstrumentationOutputParser(test_listener)

        # add timer that times timeout if any INDIVIDUAL test takes too long
        if self._test_timeout is not None:
            instrumentation_parser.add_test_execution_listener(Timer(self._test_timeout))

        # TASK-3: cature logact to file and markers for beginning/end of each test
        device_log = DeviceLog(test_application.device)
        with device_log.capture_to_file(output_path=os.path.join(self._artifact_dir, "logcat.txt")) as log_capture:
            # log_capture to listen to test status to mark beginning/end of each test run:
            instrumentation_parser.add_test_execution_listener(log_capture)

            async for test_suite_name, instrument_args in single_buffered_test_suite():
                test_listener.test_suite_started(test_suite_name)
                try:
                    async for line in test_application.run(*instrument_args):
                        instrumentation_parser.parse_line(line)
                except Exception as e:
                    test_listener.test_suite_errored(test_suite_name, 1, trace(e))
                finally:
                    test_listener.test_suite_ended(test_suite_name,
                                                   instrumentation_parser.total_test_count,
                                                   instrumentation_parser.execution_time)
                    device_restoration.restore()
            # capture logcat markers (begin/end of eeach test/test suite)
            marker_output_path = os.path.join(self._artifact_dir, 'log_markers.txt')
            if os.path.exists(marker_output_path):
                os.remove(marker_output_path)
            with open(marker_output_path, 'w') as f:
                for marker, pos in log_capture.markers.items():
                    f.write("%s=%s\n" % (marker, str(pos)))

    # TASK-3: monitor logact for TestButler commands
    async def _process_logcat_tags(self, device: Device):
        """
        Process requested tags from logcat (including tag for test butler to process commands, if applicable)

        :param device: remote device to process tags from
        """
        try:
            logcat_demuxer = LogcatTagDemuxer(self._tag_monitors)
            device_log = DeviceLog(device)
            keys = ['%s:%s' % (k, v[0]) for k, v in self._tag_monitors.items()]
            async for line in device_log.logcat("-v", "brief", "-s", *keys):
                logcat_demuxer.parse_line(line)
        except Exception as e:
            log.error("Exception on logcat processing, aborting: \n%s" % trace(e))
            asyncio.get_event_loop().stop()

    def execute_test_plan(self,
                          test_application: TestApplication,
                          test_plan: Generator[Tuple[str, List[str]], None, None],
                          test_reporter: TestListener):
        """
        Execute a test plan (a collection of test suites defined as a collection of arguments to adb instrument)

        :param test_application:  test application to run
        :param test_plan: iterator with each element being a tuple of test suite name and list of string arguments
           to provide to an executionof "adb instrument".  The test suit name is
           used to report start and end of each test suite via the test_reporter
        :param test_reporter: used to report test results as they occur

        :raises: asyncio.TimeoutError if test or test suite times out based on this orchestrator's configuration
        """
        loop = asyncio.get_event_loop()
        device_restoration = self._DeviceRestoration(test_application.device)

        # add testbutler tag for processing
        if self._test_butler_service:
            line_parser: LineParser = TestButlerCommandParser(self._test_butler_service,
                                                              app_under_test=test_application,
                                                              listener=device_restoration)
            self._tag_monitors['TestButler'] = ('I', line_parser)

        if self._tag_monitors:
            log.debug("Creating logcat monitoring task")
            loop.create_task(self._process_logcat_tags(test_application.device))

        # ADD  USER-DEFINED TASKS
        user_tasks = []
        for coroutine in self._background_coroutines:
            user_tasks.append(asyncio.get_event_loop().create_task(coroutine))

        async def timer():
            """
            Timer to timeout if future is not presented in given timeout for overall test suite execution
            """
            await asyncio.wait_for(self._execute_plan(test_plan=test_plan,
                                                      test_application=test_application,
                                                      test_listener=test_reporter,
                                                      device_restoration=device_restoration),
                                   self._instrumentation_timeout)

        try:
            loop.run_until_complete(timer())  # execute plan until completed or until timeout is reached
        finally:
            for task in user_tasks:
                task.cancel()

    def execute_test_suite(self,
                           test_application: TestApplication,
                           instrument_args: List[str],
                           test_reporter: TestListener):
        """
        Execute a suite of tests as given by the argument list, and report test results
        :param test_application: test application to be executed on remote device
        :param instrument_args: list of string args to provide to "adb shell instrument" command
        :param test_reporter: uesd to report test results as they happen

        :raises asyncio.TimeoutError if test or test suite times out
        """
        def single_iteration():
            """
            Generator of single item
            """
            yield ('single_test_suite', instrument_args)

        self.execute_test_plan(test_application, single_iteration(), test_reporter=test_reporter)

    def add_background_task(self, coroutine: Coroutine):
        """
        Add a user-define backgrounde process to be executed during test run.  Note that the coroutine
        will not be invoked until a call to `execute_test_plan` is called

        :param coroutine: coroutine to be executed during asyncio even loop execution of tests
        """
        self._background_coroutines.append(coroutine)
