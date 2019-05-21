import os

import pytest

from androidtestorchestrator import AndroidTestOrchestrator, TestApplication, ServiceApplication, Application, TestSuite
from androidtestorchestrator.device import Device
from androidtestorchestrator.parsing import LineParser
from androidtestorchestrator.reporting import TestListener

# noinspection PyShadowingNames
@pytest.fixture()
def android_test_app(device,
                     request,
                     support_app: str,
                     support_test_app: str,
                     test_butler_service: str):
    app_for_test = TestApplication.from_apk(support_test_app, device)
    support_app = Application.from_apk(support_app, device)
    butler_service = ServiceApplication.from_apk(test_butler_service, device)

    def fin():
        """
        Leave the campground as clean as you found it:
        """
        butler_service.uninstall()
        app_for_test.uninstall()
        support_app.uninstall()
    request.addfinalizer(fin)
    return app_for_test


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

    def test_add_logcat_tag_monitor(self, device: Device, test_butler_service: str, tmpdir: str):
        with AndroidTestOrchestrator(test_butler_apk_path=test_butler_service,
                                     artifact_dir=str(tmpdir),) as orchestrator:
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

    def test_execute_test_suite(self, device: Device, android_test_app: TestApplication, test_butler_service: str, tmpdir):
        test_count = 0
        test_suite_count = 0
        expected_test_suite = None
        current_test_suite = None

        class TestExpectations(TestListener):

            def __init__(self):
                self.expected_test_class = {
                    'test_suite1': "com.linkedin.mdctest.TestButlerTest",
                    'test_suite2': "com.linkedin.mdctest.TestButlerTest",
                    'test_suite3': "com.linkedin.mdctest.TestButlerStressTest"
                }

            def test_suite_errored(self, test_suite_name: str, status_code: int, exc_msg: str=""):
                assert False, "did not expect test process to error with error code %d; \n%s" % \
                              (status_code, exc_msg)

            def test_assumption_violated(self, test_name: str, test_class: str, test_no: int, reason: str):
                nonlocal test_count
                test_count += 1

            def test_suite_ended(self, test_suite_name: str, total_test_count: int, execution_time: float):
                nonlocal test_count
                nonlocal expected_test_suite
                assert test_suite_name == expected_test_suite

            def test_ended(self, test_name: str, test_class: str, test_no: int, duration: float, msg: str = ""):
                nonlocal test_count, current_test_suite
                test_count += 1
                assert test_name in ["testTestButlerSetImmersiveModeConfirmation",
                                     "testTestButlerRotation",
                                     "testTestButlerSetWifiState",
                                     "testTestButlerSetLocationModeBatterySaver",
                                     "testTestButlerSetLocationModeSensorsOnly",
                                     "testTestButlerSetLocationModeHigh",
                                     "testTestButlerStress"
                                     ]
                assert test_class == self.expected_test_class[current_test_suite]

            def test_failed(self, test_name: str, test_class: str, test_no: int, stack: str,
                            msg: str = ""):
                nonlocal test_count, current_test_suite
                test_count += 1
                assert test_class == self.expected_test_class[current_test_suite]

            def test_ignored(self, test_name: str, test_class: str, test_no: int, msg: str = ""):
                nonlocal test_count
                test_count += 1
                assert False, "no skipped tests should be present"

            def test_suite_started(self, test_suite_name: str):
                nonlocal test_count, test_suite_count
                nonlocal expected_test_suite
                nonlocal current_test_suite
                current_test_suite = test_suite_name
                print("Started test suite %s" % test_suite_name)
                test_count = 0  # reset
                test_suite_count += 1
                expected_test_suite = "test_suite%d" % test_suite_count
                assert test_suite_name == expected_test_suite


        def test_generator():
            yield (TestSuite(name='test_suite1',
                             arguments=["-e", "class", "com.linkedin.mdctest.TestButlerTest#testTestButlerRotation"]))
            yield (TestSuite(name='test_suite2',
                             arguments=["-e", "class", "com.linkedin.mdctest.TestButlerTest"]))
            yield (TestSuite(name='test_suite3',
                             arguments=["-e", "class", "com.linkedin.mdctest.TestButlerStressTest"]))

        with AndroidTestOrchestrator(test_butler_apk_path=test_butler_service,
                                     artifact_dir=str(tmpdir)) as orchestrator:

            orchestrator.execute_test_plan(test_plan=test_generator(),
                                           test_application=android_test_app,
                                           test_listener=TestExpectations())

    def test_add_background_task(self,
                                 device: Device,
                                 android_test_app : TestApplication,
                                 test_butler_service: str,
                                 tmpdir: str):
        def test_generator():
            yield (TestSuite(name='test_suite1',
                             arguments=["-e", "class", "com.linkedin.mdctest.TestButlerTest#testTestButlerRotation"]))

        # noinspection PyMissingOrEmptyDocstring
        class EmptyListner(TestListener):

            def test_suite_started(self, test_suite_name: str):
                pass

            def test_suite_ended(self, test_suite_name: str, test_count: int, execution_time: float):
                pass

            def test_suite_errored(self, test_suite_name: str, status_code: int, exc_message: str = ""):
                pass

            def test_failed(self, test_name: str, test_class: str, test_no: int, stack: str, msg: str = ""):
                pass

            def test_ignored(self, test_name: str, test_class: str, test_no: int, msg: str = ""):
                pass

            def test_assumption_violated(self, test_name: str, test_class: str, test_no: int, reason: str):
                pass

            def test_ended(self, test_name: str, test_class: str, test_no: int, duration: float, msg: str = ""):
                pass

        was_called = False

        async def some_task(orchestrator: AndroidTestOrchestrator):
            """
            For testing that user-defined background task was indeed executed
            """
            nonlocal was_called
            was_called = True

            with pytest.raises(Exception):
                orchestrator.add_logcat_monitor("BogusTag", None)

        with AndroidTestOrchestrator(test_butler_apk_path=test_butler_service,
                                     artifact_dir=str(tmpdir)) as orchestrator:
            orchestrator.add_background_task(some_task(orchestrator))
            orchestrator.execute_test_plan(test_plan=test_generator(),
                                           test_application=android_test_app,
                                           test_listener=EmptyListner())
        assert was_called, "Failed to call user-define background task"

    def test_invalid_test_timesout(self, device: Device, tmpdir):
        with pytest.raises(ValueError):
            # individual test time greater than overall timeout for suite
            with AndroidTestOrchestrator(artifact_dir=str(tmpdir),
                                         max_test_suite_time=1, max_test_time=10):
                pass

    def test_nonexistent_artifact_dir(self, device: Device):
        with pytest.raises(FileNotFoundError):
            # individual test time greater than overall timeout for suite
            with AndroidTestOrchestrator(artifact_dir="/no/such/dir"):
                pass

    def test_invalid_artifact_dir_is_file(self, device: Device):
        with pytest.raises(FileExistsError):
            # individual test time greater than overall timeout for suite
            with AndroidTestOrchestrator(artifact_dir=__file__):
                pass

    def test_invalid_butler_apk(self, device: Device, tmpdir):
        with pytest.raises(FileNotFoundError):
            # individual test time greater than overall timeout for suite
            with AndroidTestOrchestrator(artifact_dir=str(tmpdir),
                                         test_butler_apk_path="/no/such/apk"):
                pass
