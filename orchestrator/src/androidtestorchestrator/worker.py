import asyncio
import logging
import os
from contextlib import suppress

from typing import Any, AsyncIterator, List, Optional, Dict, Tuple, Iterator, Union, Coroutine

from androidtestorchestrator import TestApplication
from .device import Device
from .testprep import EspressoTestSetup
from .reporting import TestExecutionListener
from .devicestorage import DeviceStorage
from .devicelog import DeviceLog
from androidtestorchestrator.parsing import LogcatTagDemuxer
from .parsing import InstrumentationOutputParser, LineParser
from .timing import Timer


log = logging.getLogger(__name__)
log.setLevel(logging.INFO)


async def _async_iter_adapter(iter: Iterator):
    for item in iter:
        yield item
        await asyncio.sleep(0)


class Worker:

    def __init__(self,
                 device: Device,
                 tests: Union[AsyncIterator["androidtestorchestrator.main.TestSuite"],
                              Iterator["androidtestorchestrator.main.TestSuite"]],
                 test_setup: EspressoTestSetup,
                 artifact_dir: str,
                 listeners: List[TestExecutionListener]):
        """
        :param tests: AyncIterator or Iterator to iterate through available tests
        :param test_setup: test configuration to determine test execution workflow
        :param artifact_dir: path to place file articats created furing run
        :param listeners: List of listeners to watch test execution
        """
        if isinstance(tests, Iterator):
            self._tests = _async_iter_adapter(tests)
        else:
            self._tests = tests
        self._test_setup = test_setup
        self._artifact_dir = artifact_dir
        # CAUTION: this is a reference to what is passed in and held by a client, and will
        # be updated as the client's listeners get updated
        self._run_listeners = listeners
        self._logcat_proc = None  # if active, process that monitors logcat tags for commands to process
        self._device = device

    async def _process_logcat_tags(self, device: Device, monitor_tags: Dict[str, Tuple[str, LineParser]]) -> None:
        """
        Process requested tags from logcat

        :param device: remote device to process tags from
        """
        if not monitor_tags:
            return
        try:
            logcat_demuxer = LogcatTagDemuxer(monitor_tags)
            device_log = DeviceLog(device)
            keys = ['%s:%s' % (k, v[0]) for k, v in monitor_tags.items()]
            async with await device_log.logcat("-v", "brief", "-s", *keys) as proc:
                self._logcat_proc = proc
                async for line in proc.output():
                    logcat_demuxer.parse_line(line)
                # proc is stopped by test execution coroutine

        except Exception as e:
            log.error("Exception on logcat processing, aborting: \n%s" % str(e))

    async def _loop_over_tests(self, test_app: TestApplication, under_orchestration: bool, test_timeout: Optional[int]):
        log.info("Running tests...")
        device_storage = DeviceStorage(self._device)
        async for test_run in self._tests:
            self._signal_listeners("test_suite_started", test_run.name)
            # chain the listeners to the parser of the "adb instrument" command,
            # which is the source of test status from the device:
            with  InstrumentationOutputParser(test_run.name) as instrumentation_parser:
                instrumentation_parser.add_execution_listeners(self._run_listeners)
                # add timer that times timeout if any INDIVIDUAL test takes too long
                if test_timeout is not None:
                    instrumentation_parser.add_simple_test_listener(Timer(test_timeout))
                try:
                    # push test vectors, if any, to device
                    for local_path, remote_path in test_run.uploadables:
                        device_storage.push(local_path=local_path, remote_path=remote_path)
                    # run tests on the device, and parse output
                    test_args = []
                    for key, value in test_run.test_parameters.items():
                        test_args += ["-e", key, value]
                    if under_orchestration:
                        run_future = test_app.run_orchestrated(*test_args)
                    else:
                        run_future = test_app.run(*test_args)
                    async with await run_future as proc:
                        async for line in proc.output(unresponsive_timeout=test_timeout):
                            instrumentation_parser.parse_line(line)
                        await proc.wait(timeout=test_timeout)
                except Exception as e:
                    log.exception("Test run failed \n%s", str(e))
                    self._signal_listeners("test_suite_failed", test_run.name, str(e))
                finally:
                    # cleanup
                    for _, remote_path in test_run.uploadables:
                        try:
                            device_storage.remove(remote_path, recursive=True)
                        except Exception:
                            log.error("Failed to remove temporary test vector %s from device %s", remote_path,
                                      self._device.device_id)
                    failure_msg = instrumentation_parser.failure_message()
                    if failure_msg:
                        try:
                            self._signal_listeners("test_suite_failed", test_run.name, failure_msg)
                        except Exception:
                            log.exception("Exception raised in client test status handler 'test_suite_failed'!" 
                                          "Ignoring and moving on")
                    try:
                        self._signal_listeners("test_suite_ended", test_run.name,
                                               duration=instrumentation_parser.execution_time)
                    except Exception:
                        log.exception("Exception raised in client test status handler 'test_suite_ended'!"
                                      "Ignoring and moving on")
        log.info("Test queue exhausted. DONE")

    def _signal_listeners(self, method_name: str, *args: Any, **kargs: Any) -> Any:
        """
        apply the given method with given args across the full collection of listeners

        :param method_name: which method to invoke
        :param args: args to pass to method
        :param kargs: keyword args to pass to method
        :return: return value from method
        """
        for listener in self._run_listeners:
            method = getattr(listener, method_name)
            method(*args, **kargs)

    async def run(self,
                  completion_callback: Coroutine[None, None, None],
                  under_orchestration: bool = False,
                  test_timeout: Optional[int] = None,
                  monitor_tags: Optional[Dict[str, Tuple[str, LineParser]]] = None) -> None:
        """
        Worker coroutine where test execution against a given test application (on a single device) happens
        :param completion_callback: corouting that is awaited on upon completion, or None
        :param under_orchestration: whether to run under orchestration or not
        :param test_timeout: raises TimeoutError after this many seconds if not None and test exceution has not completed
        :param monitor_tags: tags mapped to listeners to be monitored in logcat for processing

        :raises asyncio.TimeoutError if execution does not complete in time
        """

        device_log = DeviceLog(self._device)
        # log_capture is to listen to test status to mark beginning/end of each test run:
        logcat_output_path = os.path.join(self._artifact_dir, f"logcat-{self._device.device_id}.txt")
        logcat_task = None
        if monitor_tags:
            logcat_task = asyncio.create_task(self._process_logcat_tags(self._device,
                                                                        monitor_tags=monitor_tags))
        try:
            with device_log.capture_to_file(output_path=logcat_output_path):
                log.info("Preparing device...")
                async with self._test_setup.apply(self._device) as test_app:
                    log.info("Running tests...")
                    await self._loop_over_tests(test_app, under_orchestration, test_timeout)
        finally:
            if self._logcat_proc:
                with suppress(Exception):
                    self._logcat_proc.kill()
            if logcat_task:
                if logcat_task.exception():
                    log.error(f"Exception found in task processing logcat tags/commands: {logcat_task.exception()}")
            try:
                await completion_callback
            except Exception:
                log.exception("Error in completion callback on worker task")
