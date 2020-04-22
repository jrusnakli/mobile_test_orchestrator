import logging
import os

from typing import Any, Optional

import pytest
import pytest_mproc

from androidtestorchestrator.main import AndroidTestOrchestrator, TestSuite
from androidtestorchestrator.device import Device, AsyncDeviceQueue
from androidtestorchestrator.parsing import LineParser
from androidtestorchestrator.reporting import TestExecutionListener
from androidtestorchestrator.testprep import EspressoTestSetup


log = logging.getLogger(__name__)


# noinspection PyShadowingNames
class TestAndroidTestOrchestrator(object):

    class TagListener(LineParser):
        """
        For capturing logcat output lines for test assertions
        """

        def __init__(self):
            """
            just capture lines to memory as they come ine
            """
            super().__init__()
            self.lines = []

        def parse_line(self, line: str):
            """

            :param line: line to parse
            """
            self.lines.append(line)

    @pytest.mark.asyncio
    async def test_add_logcat_tag_monitor(self, tmpdir: str):
        async with AndroidTestOrchestrator(artifact_dir=str(tmpdir),) as orchestrator:
            handler = TestAndroidTestOrchestrator.TagListener()
            orchestrator.add_logcat_monitor("TestTag", handler)
            assert orchestrator._tag_monitors.get('TestTag') == ('*', handler)
            orchestrator.add_logcat_monitor("TestTag2", handler, priority='I')
            assert orchestrator._tag_monitors.get('TestTag2') == ('I', handler)

    def test_invalid_logcat_tag_monitor_invocations(self, tmpdir):
        orchestrator = AndroidTestOrchestrator(artifact_dir=str(tmpdir))
        handler = TestAndroidTestOrchestrator.TagListener()
        with pytest.raises(ValueError):
            orchestrator.add_logcat_monitor("TestTag3", handler, priority='Bogus')
        orchestrator.add_logcat_monitor("TestTag", handler)
        with pytest.raises(ValueError):
            orchestrator.add_logcat_monitor("TestTag", handler)  # duplicate tag/priority

    @pytest.mark.asyncio
    async def test_invalid_test_timesout(self, device: Device, tmpdir):
        with pytest.raises(ValueError):
            # individual test time greater than overall timeout for suite
            async with AndroidTestOrchestrator(artifact_dir=str(tmpdir),
                                               max_test_suite_time=1, max_test_time=10):
                pass

    @pytest.mark.asyncio
    async def test_nonexistent_artifact_dir(self):
        with pytest.raises(FileNotFoundError):
            # individual test time greater than overall timeout for suite
            with AndroidTestOrchestrator(artifact_dir="/no/such/dir"):
                pass

    @pytest.mark.asyncio
    async def test_invalid_artifact_dir_is_file(self):
        with pytest.raises(FileExistsError):
            # individual test time greater than overall timeout for suite
            async with AndroidTestOrchestrator(artifact_dir=__file__):
                pass

    @pytest_mproc.group("device_q")
    @pytest.mark.asyncio
    async def test_execute_test_suite(self,
                                      devices: AsyncDeviceQueue,
                                      support_app: str,
                                      support_test_app: str,
                                      tmpdir):

        class TestExpectations(TestExecutionListener):

            def __init__(self):
                self.expected_test_class = {
                    'test_suite1': "com.linkedin.mtotestapp.InstrumentedTestAllSuccess",
                    'test_suite2': "com.linkedin.mtotestapp.InstrumentedTestAllSuccess",
                    'test_suite3': "com.linkedin.mtotestapp.InstrumentedTestSomeFailures"
                }
                self.test_count = 0
                self.test_suites = []

            def test_suite_failed(self, test_run_name: str, error_message: str):
                assert test_run_name in self.expected_test_class.keys()
                assert False, "did not expect test process to error; \n%s" % error_message

            def test_assumption_failure(self, test_run_name: str, class_name: str, test_name: str, stack_trace: str):
                assert False, "did not expect test assumption failure"

            def test_suite_ended(self, test_run_name: str, duration: float = -1.0, **kwargs: Optional[Any]) -> None:
                self.test_suites.append(test_run_name)
                assert test_run_name in self.expected_test_class.keys()

            def test_started(self, test_run_name: str, class_name: str, test_name: str):
                assert test_run_name in self.expected_test_class.keys()

            def test_ended(self, test_run_name: str, class_name: str, test_name: str, **kwargs):
                self.test_count += 1
                assert test_run_name in self.expected_test_class.keys()
                assert test_name in ["useAppContext", "testSuccess", "testFail"]
                assert class_name in self.expected_test_class.values()

            def test_failed(self, test_run_name: str, class_name: str, test_name: str, stack_trace: str):
                assert class_name == 'com.linkedin.mtotestapp.InstrumentedTestSomeFailures'
                assert test_name == "testFail"  # this test case is designed to be failed

            def test_ignored(self, test_run_name: str, class_name: str, test_name: str):
                assert False, "no skipped tests should be present"

            def test_suite_started(self, test_run_name: str, count: int = 0):
                print("Started test suite %s" % test_run_name)
                assert test_run_name in self.expected_test_class.keys()

        def test_generator():
            yield (TestSuite(name='test_suite1',
                             test_parameters={"class": "com.linkedin.mtotestapp.InstrumentedTestAllSuccess#useAppContext"}))
            yield (TestSuite(name='test_suite2',
                             test_parameters={"class": "com.linkedin.mtotestapp.InstrumentedTestAllSuccess#testSuccess"}))
            yield (TestSuite(name='test_suite3',
                             test_parameters={"class": "com.linkedin.mtotestapp.InstrumentedTestSomeFailures"}))

        listener = TestExpectations()
        bundle = EspressoTestSetup(path_to_apk=support_app, path_to_test_apk=support_test_app)
        async with AndroidTestOrchestrator(artifact_dir=str(tmpdir)) as orchestrator:
            orchestrator.add_test_listener(listener)
            await orchestrator.execute_test_plan(test_plan=test_generator(),
                                                 test_setup=bundle,
                                                 devices=devices)
        assert listener.test_count == 4
        assert set(listener.expected_test_class.keys()) == set(listener.test_suites)

    @pytest_mproc.group("device_q")
    @pytest.mark.asyncio
    async def test_execute_test_suite_orchestrated(self, devices: AsyncDeviceQueue, support_app: str,
                                                   support_test_app: str, tmpdir):
        test_count = 0
        test_suite_count = 0
        expected_test_suite = None

        gradle_cache_dir = os.environ.get("GRADLE_USER_HOME", os.path.join(os.path.expanduser('~'), '.gradle'))
        gradle_apk_root_dir = os.path.join(gradle_cache_dir, 'caches', 'modules-2', 'files-2.1')
        test_services_root = os.path.join(gradle_apk_root_dir, 'com.android.support.test.services', 'test-services')
        orchestrator_root = os.path.join(gradle_apk_root_dir, 'com.android.support.test', 'orchestrator')

        def find_file(in_path: str, name_prefix: str) -> str:
            for root, dirs, files in os.walk(in_path):
                for file in files:
                    if file.startswith(name_prefix) and file.endswith('.apk'):
                        return os.path.join(root, file)

        test_services_apk = find_file(test_services_root, 'test-services')
        android_orchestrator_apk = find_file(orchestrator_root, 'orchestrator')

        if not test_services_apk or not android_orchestrator_apk:
            raise Exception("Unable to locate test-services apk or orchestrator apk for orchestrated run. Aborting")

        class TestExpectations(TestExecutionListener):

            def __init__(self):
                self.expected_test_class = {
                    'test_suite1': "com.linkedin.mtotestapp.InstrumentedTestAllSuccess",
                }

            def test_suite_failed(self, test_suite_name: str, error_message: str):
                assert False, "did not expect test process to error; \n%s" % error_message

            def test_assumption_failure(self, test_suite_name: str, class_name: str, test_name: str, stack_trace: str):
                pass

            def test_suite_ended(self, test_suite_name: str, duration: float = -1.0, **kwargs: Optional[Any]) -> None:
                pass

            def test_started(self, test_suite_name: str, class_name: str, test_name: str):
                pass

            def test_ended(self, test_suite_name: str, class_name: str, test_name: str, **kwargs):
                nonlocal test_count
                test_count += 1
                assert test_name in ["useAppContext",
                                     "testSuccess",
                                     "testFail"
                                     ]
                assert class_name in [
                    "com.linkedin.mtotestapp.InstrumentedTestAllSuccess",
                    "com.linkedin.mtotestapp.InstrumentedTestSomeFailures"
                ]

            def test_failed(self, test_suite_name: str, class_name: str, test_name: str, stack_trace: str):
                nonlocal test_count
                assert class_name in [
                    "com.linkedin.mtotestapp.InstrumentedTestAllSuccess",
                    "com.linkedin.mtotestapp.InstrumentedTestSomeFailures"
                ]
                assert test_name == "testFail"  # this test case is designed to be failed

            def test_ignored(self, test_suite_name: str, class_name: str, test_name: str):
                nonlocal test_count
                assert False, "no skipped tests should be present"

            def test_suite_started(self, test_run_name: str, count: int = 0):
                nonlocal test_count, test_suite_count
                nonlocal expected_test_suite
                print("Started test suite %s" % test_run_name)
                test_count = 0  # reset
                test_suite_count += 1
                expected_test_suite = "test_suite%d" % test_suite_count
                assert test_run_name == expected_test_suite

        def test_generator():
            yield (TestSuite(name='test_suite1',
                             test_parameters={"class": "com.linkedin.mtotestapp.InstrumentedTestAllSuccess"}))

        bundle =EspressoTestSetup(path_to_apk=support_app, path_to_test_apk=support_test_app)
        bundle.add_foreign_apks([test_services_apk, android_orchestrator_apk])
        async with AndroidTestOrchestrator(artifact_dir=str(tmpdir), run_under_orchestration=True) as orchestrator:
            orchestrator.add_test_listener(TestExpectations())
            await orchestrator.execute_test_plan(test_plan=test_generator(),
                                                 test_setup=bundle,
                                                 devices=devices)

        assert test_count == 4
