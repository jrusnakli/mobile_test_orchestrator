import logging
import os

from contextlib import suppress
from typing import Any, Optional

import pytest

from androidtestorchestrator.main import AndroidTestOrchestrator, TestSuite
from androidtestorchestrator.application import Application, TestApplication
from androidtestorchestrator.device import Device
from androidtestorchestrator.parsing import LineParser
from androidtestorchestrator.reporting import TestRunListener
from androidtestorchestrator.testprep import EspressoTestPreparation, DevicePreparation
from ..support import uninstall_apk


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

    def test_add_logcat_tag_monitor(self, device: Device, tmpdir: str):
        with AndroidTestOrchestrator(artifact_dir=str(tmpdir),) as orchestrator:
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

    def test_execute_test_suite(self, device: Device, android_test_app: TestApplication, tmpdir):
        test_suite_count = 0
        expected_test_suite = None
        current_test_suite = None
        uninstall_apk(android_test_app, device)

        class TestExpectations(TestRunListener):

            def __init__(self):
                self.expected_test_class = {
                    'test_suite1': "com.linkedin.mtotestapp.InstrumentedTestAllSuccess",
                    'test_suite2': "com.linkedin.mtotestapp.InstrumentedTestAllSuccess",
                    'test_suite3': "com.linkedin.mtotestapp.InstrumentedTestSomeFailures"
                }
                self.test_count = 0

            def test_run_failed(self, error_message: str):
                assert False, "did not expect test process to error; \n%s" % error_message

            def test_assumption_failure(self, class_name: str, test_name: str, stack_trace: str):
                self.test_count += 1

            def test_run_ended(self, duration: float = -1.0, **kwargs: Optional[Any]) -> None:
                pass

            def test_started(self, class_name: str, test_name: str):
                pass

            def test_ended(self, class_name: str, test_name: str, **kwargs):
                nonlocal current_test_suite
                self.test_count += 1
                assert test_name in ["useAppContext",
                                     "testSuccess",
                                     ]
                assert class_name == self.expected_test_class[current_test_suite]

            def test_failed(self, class_name: str, test_name: str, stack_trace: str):
                self.test_count += 1
                assert class_name == self.expected_test_class[current_test_suite]
                assert test_name == "testFail"  # this test case is designed to be failed

            def test_ignored(self, class_name: str, test_name: str):
                self.test_count += 1
                assert False, "no skipped tests should be present"

            def test_run_started(self, test_run_name: str, count: int = 0):
                nonlocal test_suite_count
                nonlocal expected_test_suite
                nonlocal current_test_suite
                current_test_suite = test_run_name
                print("Started test suite %s" % test_run_name)
                test_suite_count += 1
                expected_test_suite = "test_suite%d" % test_suite_count
                assert test_run_name == expected_test_suite

        def test_generator():
            yield (TestSuite(name='test_suite1',
                             arguments=["-e", "class", "com.linkedin.mtotestapp.InstrumentedTestAllSuccess#useAppContext"]))
            yield (TestSuite(name='test_suite2',
                             arguments=["-e", "class", "com.linkedin.mtotestapp.InstrumentedTestAllSuccess#testSuccess"]))
            yield (TestSuite(name='test_suite3',
                             arguments=["-e", "class", "com.linkedin.mtotestapp.InstrumentedTestSomeFailures"]))

        listener = TestExpectations()
        with AndroidTestOrchestrator(artifact_dir=str(tmpdir)) as orchestrator:
            orchestrator.add_test_listener(listener)
            orchestrator.execute_test_plan(test_plan=test_generator(),
                                           test_applications=[android_test_app])
        assert listener.test_count == 4

    def test_execute_test_suite_multidevice(self, device: Device, device2: Device,
                                            android_test_app: TestApplication,
                                            android_test_app2: TestApplication,
                                            tmpdir):
        test_count = 0
        test_suite_count = 0
        expected_test_suite = None
        current_test_suite = None
        uninstall_apk(android_test_app, device)
        uninstall_apk(android_test_app2, device2)

        class TestExpectations(TestRunListener):

            def __init__(self):
                self.expected_test_class = {
                    'test_suite1': "com.linkedin.mtotestapp.InstrumentedTestAllSuccess",
                    'test_suite2': "com.linkedin.mtotestapp.InstrumentedTestAllSuccess",
                    'test_suite3': "com.linkedin.mtotestapp.InstrumentedTestSomeFailures"
                }

            def test_run_failed(self, error_message: str):
                assert False, "did not expect test process to error; \n%s" % error_message

            def test_assumption_failure(self, class_name: str, test_name: str, stack_trace: str):
                nonlocal test_count
                test_count += 1

            def test_run_ended(self, duration: float = -1.0, **kwargs: Optional[Any]) -> None:
                pass

            def test_started(self, class_name: str, test_name: str):
                log.info("Started test %s %s", class_name, test_name)

            def test_ended(self, class_name: str, test_name: str, **kwargs):
                nonlocal test_count, current_test_suite
                test_count += 1
                assert test_name in ["useAppContext",
                                     "testSuccess",
                                     ]
                assert class_name == self.expected_test_class[current_test_suite]

            def test_failed(self, class_name: str, test_name: str, stack_trace: str):
                nonlocal test_count, current_test_suite
                test_count += 1
                assert class_name == self.expected_test_class[current_test_suite]
                assert test_name == "testFail"  # this test case is designed to be failed

            def test_ignored(self, class_name: str, test_name: str):
                nonlocal test_count
                test_count += 1
                assert False, "no skipped tests should be present"

            def test_run_started(self, test_run_name: str, count: int = 0):
                nonlocal test_count, test_suite_count
                nonlocal expected_test_suite
                nonlocal current_test_suite
                current_test_suite = test_run_name
                print("Started test suite %s" % test_run_name)
                test_suite_count += 1
                expected_test_suite = "test_suite%d" % test_suite_count
                assert test_run_name == expected_test_suite

        def test_generator():
            yield (TestSuite(name='test_suite1',
                             test_parameters={"class": "com.linkedin.mtotestapp.InstrumentedTestAllSuccess#useAppContext"}))
            yield (TestSuite(name='test_suite2',
                             arguments=["-e", "class", "com.linkedin.mtotestapp.InstrumentedTestAllSuccess"],
                             clean_data_on_start=True))
            yield (TestSuite(name='test_suite3',
                             arguments=["-e", "class", "com.linkedin.mtotestapp.InstrumentedTestSomeFailures"]))

        with AndroidTestOrchestrator(artifact_dir=str(tmpdir)) as orchestrator:
            orchestrator.add_test_suite_listener(TestExpectations())
            orchestrator.execute_test_plan(test_plan=test_generator(),
                                           test_applications=[android_test_app, android_test_app2])
        assert test_count == 4

    def test_add_background_task(self,
                                 device: Device,
                                 support_app: str,
                                 support_test_app: str,
                                 tmpdir: str):
        # ensure applications are not already installed as precursor to running tests
        with suppress(Exception):
            Application(device, {'package_name': support_app}).uninstall()
        with suppress(Exception):
            Application(device, {'package_name': support_test_app}).uninstall()

        def test_generator():
            yield (TestSuite(name='test_suite1',
                             arguments=["-e", "class", "com.linkedin.mtotestapp.InstrumentedTestAllSuccess#useAppContext"]))

        was_called = False

        async def some_task(orchestrator: AndroidTestOrchestrator):
            """
            For testing that user-defined background task was indeed executed
            """
            nonlocal was_called
            was_called = True

            with pytest.raises(Exception):
                orchestrator.add_logcat_monitor("BogusTag", None)

        test_vectors = os.path.join(str(tmpdir), "test_vectors")
        os.makedirs(test_vectors)
        with open(os.path.join(test_vectors, "file"), 'w') as f:
            f.write("TEST VECTOR DATA")

        with AndroidTestOrchestrator(artifact_dir=str(tmpdir)) as orchestrator, \
                EspressoTestPreparation(devices=device,
                                        path_to_apk=support_app,
                                        path_to_test_apk=support_test_app,
                                        grant_all_user_permissions=True) as test_prep, \
                DevicePreparation(device) as device_prep:
            device_prep.verify_network_connection("localhost", 4)
            device_prep.port_forward(5748, 5749)
            forwarded_ports = device.execute_remote_cmd("forward", "--list")
            assert "5748" in forwarded_ports and "5749" in forwarded_ports
            device_prep.reverse_port_forward(5432, 5431)
            reverse_forwarded_ports = device.execute_remote_cmd("reverse", "--list")
            assert "5432" in reverse_forwarded_ports and "5431" in reverse_forwarded_ports
            test_prep.upload_test_vectors(test_vectors)
            orchestrator.add_background_task(some_task(orchestrator))
            orchestrator.execute_test_plan(test_plan=test_generator(),
                                           test_applications=test_prep.test_apps)
        assert was_called, "Failed to call user-define background task"

    def test_invalid_test_timesout(self, device: Device, tmpdir):
        with pytest.raises(ValueError):
            # individual test time greater than overall timeout for suite
            with AndroidTestOrchestrator(artifact_dir=str(tmpdir),
                                         max_test_suite_time=1, max_test_time=10):
                pass

    def test_nonexistent_artifact_dir(self):
        with pytest.raises(FileNotFoundError):
            # individual test time greater than overall timeout for suite
            with AndroidTestOrchestrator(artifact_dir="/no/such/dir"):
                pass

    def test_invalid_artifact_dir_is_file(self):
        with pytest.raises(FileExistsError):
            # individual test time greater than overall timeout for suite
            with AndroidTestOrchestrator(artifact_dir=__file__):
                pass

    def test_foreign_apk_install(self, device: Device, support_app: str, support_test_app: str):
        with EspressoTestPreparation(devices=device, path_to_test_apk=support_test_app, path_to_apk=support_app ) as prep, \
             DevicePreparation(devices=device) as device_prep:
            now = device.get_device_setting("system", "dim_screen")
            new = {"1": "0", "0": "1"}[now]
            for test_app in prep.test_apps:
                test_app.uninstall()
                assert test_app.package_name not in device.list_installed_packages()
            apps = prep.setup_foreign_apps(paths_to_foreign_apks=[support_test_app])
            assert len(apps) == 1
            assert apps[0].package_name in device.list_installed_packages()
            device.set_system_property("debug.mock2", "\"\"\"\"")
            device_prep.configure_devices(settings={'system:dim_screen': new},
                                          properties={"debug.mock2": "5555"})

            assert device.get_system_property("debug.mock2") == "5555"
            assert device.get_device_setting("system", "dim_screen") == new

    def test_execute_test_suite_orchestrated(self, device: Device, support_app: str,
                                             support_test_app: str, tmpdir):
        uninstall_apk(support_app, device)
        uninstall_apk(support_test_app, device)

        test_count = 0
        test_suite_count = 0
        expected_test_suite = None
        current_test_suite = None

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

        class TestExpectations(TestRunListener):

            def __init__(self):
                self.expected_test_class = {
                    'test_suite1': "com.linkedin.mtotestapp.InstrumentedTestAllSuccess",
                }

            def test_run_failed(self, error_message: str):
                assert False, "did not expect test process to error; \n%s" % error_message

            def test_assumption_failure(self, class_name: str, test_name: str, stack_trace: str):
                pass

            def test_run_ended(self, duration: float = -1.0, **kwargs: Optional[Any]) -> None:
                pass

            def test_started(self, class_name: str, test_name: str):
                pass

            def test_ended(self, class_name: str, test_name: str, **kwargs):
                nonlocal test_count, current_test_suite
                test_count += 1
                assert test_name in ["useAppContext",
                                     "testSuccess",
                                     "testFail"
                                     ]
                assert class_name in [
                    "com.linkedin.mtotestapp.InstrumentedTestAllSuccess",
                    "com.linkedin.mtotestapp.InstrumentedTestSomeFailures"
                ]

            def test_failed(self, class_name: str, test_name: str, stack_trace: str):
                nonlocal test_count, current_test_suite
                assert class_name in [
                    "com.linkedin.mtotestapp.InstrumentedTestAllSuccess",
                    "com.linkedin.mtotestapp.InstrumentedTestSomeFailures"
                ]
                assert test_name == "testFail"  # this test case is designed to be failed

            def test_ignored(self, class_name: str, test_name: str):
                nonlocal test_count
                assert False, "no skipped tests should be present"

            def test_run_started(self, test_run_name: str, count: int = 0):
                nonlocal test_count, test_suite_count
                nonlocal expected_test_suite
                nonlocal current_test_suite
                current_test_suite = test_run_name
                print("Started test suite %s" % test_run_name)
                test_count = 0  # reset
                test_suite_count += 1
                expected_test_suite = "test_suite%d" % test_suite_count
                assert test_run_name == expected_test_suite

        def test_generator():
            yield (TestSuite(name='test_suite1',
                             test_parameters={"class": "com.linkedin.mtotestapp.InstrumentedTestAllSuccess"}))

        with EspressoTestPreparation(device, path_to_apk=support_app, path_to_test_apk=support_test_app) as test_prep, \
                AndroidTestOrchestrator(artifact_dir=str(tmpdir), run_under_orchestration=True) as orchestrator:
            test_prep.setup_foreign_apps([test_services_apk, android_orchestrator_apk])
            orchestrator.add_test_suite_listener(TestExpectations())
            orchestrator.execute_test_plan(test_plan=test_generator(),
                                           test_application=test_prep.test_app)
        assert test_count == 3  # last test suite had one test
