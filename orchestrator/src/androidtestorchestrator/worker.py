import logging
import os

from typing import Any, AsyncIterator, List, Optional

from .reporting import TestExecutionListener
from .application import TestApplication
from .devicestorage import DeviceStorage
from .devicelog import DeviceLog
from .parsing import InstrumentationOutputParser
from .timing import Timer


log = logging.getLogger(__name__)
log.setLevel(logging.WARNING)


class Worker:

    def __init__(self,
                 tests: AsyncIterator["androidtestorchestrator.main.TestSuite"],
                 test_application: TestApplication,
                 artifact_dir: str,
                 listeners: List[TestExecutionListener]):
        """
        :param tests: AyncIterator to iterate through available tests
        :param test_application: test application to run against
        :param artifact_dir: path to place file articats created furing run
        :param listeners: List of listeners to watch test execution
        """
        self._tests = tests
        self._test_application = test_application
        self._artifact_dir = artifact_dir
        # CAUTION: this is a reference to what is passed in and held by a client, and will
        # be updated as the client's listeners get updated
        self._run_listeners = listeners

    async def run(self,
                  under_orchestration: bool = False,
                  test_timeout: Optional[int] = None) -> None:
        """
        Worker coroutine where test execution against a given test application (on a single device) happens
        :param under_orchestration: whether to run under orchestration or not
        :param test_timeout: raises TimeoutError after this many seconds if not None and test has not completed
        """
        def signal_listeners(method_name: str, *args: Any, **kargs: Any) -> Any:
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

        # TASK-3: capture logcat to file and markers for beginning/end of each test
        device_log = DeviceLog(self._test_application.device)
        device_storage = DeviceStorage(self._test_application.device)
        # log_capture is to listen to test status to mark beginning/end of each test run:
        logcat_output_path = os.path.join(self._artifact_dir, f"logcat-{self._test_application.device.device_id}.txt")
        with device_log.capture_to_file(output_path=logcat_output_path):
            async for test_run in self._tests:
                signal_listeners("test_suite_started", test_run.name)
                # chain the listeners to the parser of the "adb instrument" command,
                # which is the source of test status from the device:
                instrumentation_parser = InstrumentationOutputParser(test_run.name)
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
                        run_future = self._test_application.run_orchestrated(*test_args)
                    else:
                        run_future = self._test_application.run(*test_args)
                    async with await run_future as proc:
                        async for line in proc.output(unresponsive_timeout=test_timeout):
                            instrumentation_parser.parse_line(line)
                        proc.wait(timeout=test_timeout)
                except Exception as e:
                    log.exception("Test run failed \n%s", str(e))
                    signal_listeners("test_suite_failed", test_run.name, str(e))
                finally:
                    signal_listeners("test_suite_ended", test_run.name, duration=instrumentation_parser.execution_time)
                    # cleanup
                    for _, remote_path in test_run.uploadables:
                        try:
                            device_storage.remove(remote_path, recursive=True)
                        except Exception:
                            log.error("Failed to remove temporary test vector %s from device" % remote_path)
