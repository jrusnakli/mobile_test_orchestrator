import pytest

from mobiletestorchestrator.testprep import EspressoTestSetup
from mobiletestorchestrator.reporting import  TestExecutionListener
from mobiletestorchestrator.worker import Worker, TestSuite
from typing import Dict, Optional, Any
from mobiletestorchestrator import _async_iter_adapter


class TestWorker:

    class Expectations(TestExecutionListener):

        def __init__(self, tests: Dict[str, str]):
            super().__init__()
            self.expected_test_class = tests
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
            print(f">>>> Test ended {test_run_name}::{class_name}::{test_name}")
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

    @pytest.mark.asyncio
    async def test_run(self, device, support_app, support_test_app, mp_tmp_dir):
        tests = {
            'test_suite1': "com.linkedin.mtotestapp.InstrumentedTestAllSuccess",
            'test_suite2': "com.linkedin.mtotestapp.InstrumentedTestAllSuccess",
            'test_suite3': "com.linkedin.mtotestapp.InstrumentedTestSomeFailures"
        }
        test_suites = [TestSuite(name=key, test_parameters={"class": value}) for key, value in tests.items()]
        expectations = self.Expectations(tests)
        test_setup = EspressoTestSetup.Builder(path_to_apk=support_app,
                                               path_to_test_apk=support_test_app).resolve()
        worker = Worker(device, _async_iter_adapter(iter(test_suites)),
        test_setup, artifact_dir=mp_tmp_dir, listeners=[expectations])
        await worker.run(test_timeout=20)
        assert expectations.test_count == 6
