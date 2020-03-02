import os

import pytest

from androidtestorchestrator.device import Device
from androidtestorchestrator.devicelog import DeviceLog
from androidtestorchestrator.parsing import InstrumentationOutputParser
from androidtestorchestrator.reporting import TestRunListener


class TestInstrumentationOutputParser(object):

    example_output = """
INSTRUMENTATION_STATUS: numtests=3
INSTRUMENTATION_STATUS: stream=
com.test.TestSkipped
INSTRUMENTATION_STATUS: id=AndroidJUnitRunner
INSTRUMENTATION_STATUS: test=transcode1440pAvc
INSTRUMENTATION_STATUS: class=com.test.TestSkipped
INSTRUMENTATION_STATUS: current=1
INSTRUMENTATION_STATUS_CODE: 1
INSTRUMENTATION_STATUS: numtests=3
INSTRUMENTATION_STATUS: stream=
continuation line
INSTRUMENTATION_STATUS: id=AndroidJUnitRunner
INSTRUMENTATION_STATUS: test=transcode1440pAvc
INSTRUMENTATION_STATUS: class=com.test.TestSkipped
INSTRUMENTATION_STATUS: stack=org.junit.AssumptionViolatedException: Device codec max capability does not meet resolution capability requirement
at com.linkedin.android.litr.utils.rules.CodecCapabilityTestRule.shouldIgnoreTest(CodecCapabilityTestRule.java:53)
at com.linkedin.android.litr.utils.rules.CodecCapabilityTestRule.evaluate(CodecCapabilityTestRule.java:34)
at com.linkedin.android.litr.utils.rules.CodecCapabilityTestRule.access$000(CodecCapabilityTestRule.java:12)
at com.linkedin.android.litr.utils.rules.CodecCapabilityTestRule$1.evaluate(CodecCapabilityTestRule.java:28)
at org.junit.rules.RunRules.evaluate(RunRules.java:20)
at org.junit.runners.ParentRunner.runLeaf(ParentRunner.java:325)
at org.junit.runners.BlockJUnit4ClassRunner.runChild(BlockJUnit4ClassRunner.java:78)
at org.junit.runners.BlockJUnit4ClassRunner.runChild(BlockJUnit4ClassRunner.java:57)
at org.junit.runners.ParentRunner$3.run(ParentRunner.java:290)
at org.junit.runners.ParentRunner$1.schedule(ParentRunner.java:71)
at org.junit.runners.ParentRunner.runChildren(ParentRunner.java:288)
at org.junit.runners.ParentRunner.access$000(ParentRunner.java:58)
at org.junit.runners.ParentRunner$2.evaluate(ParentRunner.java:268)
at org.junit.internal.runners.statements.RunBefores.evaluate(RunBefores.java:26)
at org.junit.internal.runners.statements.RunAfters.evaluate(RunAfters.java:27)
at org.junit.runners.ParentRunner.run(ParentRunner.java:363)
at org.junit.runners.Suite.runChild(Suite.java:128)
at org.junit.runners.Suite.runChild(Suite.java:27)
at org.junit.runners.ParentRunner$3.run(ParentRunner.java:290)
at org.junit.runners.ParentRunner$1.schedule(ParentRunner.java:71)
at org.junit.runners.ParentRunner.runChildren(ParentRunner.java:288)
at org.junit.runners.ParentRunner.access$000(ParentRunner.java:58)
at org.junit.runners.ParentRunner$2.evaluate(ParentRunner.java:268)
at org.junit.runners.ParentRunner.run(ParentRunner.java:363)
at org.junit.runner.JUnitCore.run(JUnitCore.java:137)
at org.junit.runner.JUnitCore.run(JUnitCore.java:115)
at android.support.test.internal.runner.TestExecutor.execute(TestExecutor.java:54)
at android.support.test.runner.AndroidJUnitRunner.onStart(AndroidJUnitRunner.java:240)
at android.app.Instrumentation$InstrumentationThread.run(Instrumentation.java:1741)

INSTRUMENTATION_STATUS: current=1
INSTRUMENTATION_STATUS_CODE: -4
INSTRUMENTATION_STATUS: numtests=3
INSTRUMENTATION_STATUS: stream=
INSTRUMENTATION_STATUS: id=AndroidJUnitRunner
INSTRUMENTATION_STATUS: test=transcode1080pAvc
INSTRUMENTATION_STATUS: class=com.test.Test2
INSTRUMENTATION_STATUS: current=2
INSTRUMENTATION_STATUS_CODE: 1
INSTRUMENTATION_STATUS: numtests=3
INSTRUMENTATION_STATUS: stream=testing...
INSTRUMENTATION_STATUS: id=AndroidJUnitRunner
INSTRUMENTATION_STATUS: test=transcode1080pAvc
INSTRUMENTATION_STATUS: class=com.test.Test2
INSTRUMENTATION_STATUS: current=2
INSTRUMENTATION_STATUS_CODE: 0
INSTRUMENTATION_STATUS: numtests=3
INSTRUMENTATION_STATUS: stream=testing...
INSTRUMENTATION_STATUS: id=AndroidJUnitRunner
INSTRUMENTATION_STATUS: test=transcode2160pAvc
INSTRUMENTATION_STATUS: class=com.test.TestFailure
INSTRUMENTATION_STATUS: current=3
INSTRUMENTATION_STATUS_CODE: 1
INSTRUMENTATION_STATUS: numtests=3
INSTRUMENTATION_STATUS: stream=
INSTRUMENTATION_STATUS: id=AndroidJUnitRunner
INSTRUMENTATION_STATUS: test=transcode2160pAvc
INSTRUMENTATION_STATUS: class=com.test.TestFailure
INSTRUMENTATION_STATUS: stack=org.junit.AssumptionViolatedException: Device codec max capability does not meet resolution capability requirement
at com.linkedin.android.litr.utils.rules.CodecCapabilityTestRule.shouldIgnoreTest(CodecCapabilityTestRule.java:53)
at com.linkedin.android.litr.utils.rules.CodecCapabilityTestRule.evaluate(CodecCapabilityTestRule.java:34)
at com.linkedin.android.litr.utils.rules.CodecCapabilityTestRule.access$000(CodecCapabilityTestRule.java:12)
at com.linkedin.android.litr.utils.rules.CodecCapabilityTestRule$1.evaluate(CodecCapabilityTestRule.java:28)
at org.junit.rules.RunRules.evaluate(RunRules.java:20)
at org.junit.runners.ParentRunner.runLeaf(ParentRunner.java:325)
at org.junit.runners.BlockJUnit4ClassRunner.runChild(BlockJUnit4ClassRunner.java:78)
at org.junit.runners.BlockJUnit4ClassRunner.runChild(BlockJUnit4ClassRunner.java:57)
at org.junit.runners.ParentRunner$3.run(ParentRunner.java:290)
at org.junit.runners.ParentRunner$1.schedule(ParentRunner.java:71)
at org.junit.runners.ParentRunner.runChildren(ParentRunner.java:288)
at org.junit.runners.ParentRunner.access$000(ParentRunner.java:58)
at org.junit.runners.ParentRunner$2.evaluate(ParentRunner.java:268)
at org.junit.internal.runners.statements.RunBefores.evaluate(RunBefores.java:26)
at org.junit.internal.runners.statements.RunAfters.evaluate(RunAfters.java:27)
at org.junit.runners.ParentRunner.run(ParentRunner.java:363)
at org.junit.runners.Suite.runChild(Suite.java:128)
at org.junit.runners.Suite.runChild(Suite.java:27)
at org.junit.runners.ParentRunner$3.run(ParentRunner.java:290)
at org.junit.runners.ParentRunner$1.schedule(ParentRunner.java:71)
at org.junit.runners.ParentRunner.runChildren(ParentRunner.java:288)
at org.junit.runners.ParentRunner.access$000(ParentRunner.java:58)
at org.junit.runners.ParentRunner$2.evaluate(ParentRunner.java:268)
at org.junit.runners.ParentRunner.run(ParentRunner.java:363)
at org.junit.runner.JUnitCore.run(JUnitCore.java:137)
at org.junit.runner.JUnitCore.run(JUnitCore.java:115)
at android.support.test.internal.runner.TestExecutor.execute(TestExecutor.java:54)
at android.support.test.runner.AndroidJUnitRunner.onStart(AndroidJUnitRunner.java:240)
at android.app.Instrumentation$InstrumentationThread.run(Instrumentation.java:1741)

INSTRUMENTATION_STATUS: current=3
INSTRUMENTATION_STATUS_CODE: -2
INSTRUMENTATION_RESULT: stream=

Time: 9.387

OK (3 tests)


INSTRUMENTATION_CODE: -1
    """

    EXPECTED_STACK_TRACE = """org.junit.AssumptionViolatedException: Device codec max capability does not meet resolution capability requirement
at com.linkedin.android.litr.utils.rules.CodecCapabilityTestRule.shouldIgnoreTest(CodecCapabilityTestRule.java:53)
at com.linkedin.android.litr.utils.rules.CodecCapabilityTestRule.evaluate(CodecCapabilityTestRule.java:34)
at com.linkedin.android.litr.utils.rules.CodecCapabilityTestRule.access$000(CodecCapabilityTestRule.java:12)
at com.linkedin.android.litr.utils.rules.CodecCapabilityTestRule$1.evaluate(CodecCapabilityTestRule.java:28)
at org.junit.rules.RunRules.evaluate(RunRules.java:20)
at org.junit.runners.ParentRunner.runLeaf(ParentRunner.java:325)
at org.junit.runners.BlockJUnit4ClassRunner.runChild(BlockJUnit4ClassRunner.java:78)
at org.junit.runners.BlockJUnit4ClassRunner.runChild(BlockJUnit4ClassRunner.java:57)
at org.junit.runners.ParentRunner$3.run(ParentRunner.java:290)
at org.junit.runners.ParentRunner$1.schedule(ParentRunner.java:71)
at org.junit.runners.ParentRunner.runChildren(ParentRunner.java:288)
at org.junit.runners.ParentRunner.access$000(ParentRunner.java:58)
at org.junit.runners.ParentRunner$2.evaluate(ParentRunner.java:268)
at org.junit.internal.runners.statements.RunBefores.evaluate(RunBefores.java:26)
at org.junit.internal.runners.statements.RunAfters.evaluate(RunAfters.java:27)
at org.junit.runners.ParentRunner.run(ParentRunner.java:363)
at org.junit.runners.Suite.runChild(Suite.java:128)
at org.junit.runners.Suite.runChild(Suite.java:27)
at org.junit.runners.ParentRunner$3.run(ParentRunner.java:290)
at org.junit.runners.ParentRunner$1.schedule(ParentRunner.java:71)
at org.junit.runners.ParentRunner.runChildren(ParentRunner.java:288)
at org.junit.runners.ParentRunner.access$000(ParentRunner.java:58)
at org.junit.runners.ParentRunner$2.evaluate(ParentRunner.java:268)
at org.junit.runners.ParentRunner.run(ParentRunner.java:363)
at org.junit.runner.JUnitCore.run(JUnitCore.java:137)
at org.junit.runner.JUnitCore.run(JUnitCore.java:115)
at android.support.test.internal.runner.TestExecutor.execute(TestExecutor.java:54)
at android.support.test.runner.AndroidJUnitRunner.onStart(AndroidJUnitRunner.java:240)
at android.app.Instrumentation$InstrumentationThread.run(Instrumentation.java:1741)""".strip()

    def test_parse_lines(self, device: Device, tmpdir):
        tmpdir = str(tmpdir)
        with DeviceLog(device).capture_to_file(os.path.join(tmpdir, "test_output.log")) as logcat_marker:

            got_test_passed = False
            got_test_ignored = False
            got_test_failed = False
            got_test_assumption_failure = False

            class Listener(TestRunListener):

                def test_run_failed(self, error_message: str):
                    pass

                def test_assumption_failure(self, class_name: str, test_name: str, stack_trace: str):
                    nonlocal got_test_assumption_failure
                    got_test_assumption_failure = True

                def test_run_started(self, test_run_name: str):
                    pass

                def test_run_ended(self, duration: float):
                    pass

                def test_started(self, class_name: str, test_name: str):
                    pass

                def test_ended(self, class_name: str, test_name: str, test_no: int, duration: float, msg: str = ""):
                    nonlocal got_test_passed
                    got_test_passed = True
                    assert test_name == "transcode1080pAvc"
                    assert class_name == "com.test.Test2"
                    assert test_no == 2
                    assert msg == "testing..."

                def test_ignored(self, class_name: str, test_name: str):
                    nonlocal got_test_ignored
                    got_test_ignored = True
                    assert test_name == "transcode1440pAvc"
                    assert class_name == "com.test.TestSkipped"

                def test_failed(self, class_name: str, test_name: str, stack_trace: str):
                    nonlocal got_test_failed
                    got_test_failed = True
                    assert test_name == "transcode2160pAvc"
                    assert class_name == "com.test.TestFailure"
                    assert stack_trace.strip() == TestInstrumentationOutputParser.EXPECTED_STACK_TRACE

            parser = InstrumentationOutputParser(test_listeners=[Listener()])

            for line in self.example_output.splitlines():
               parser.parse_line(line)

            assert got_test_passed is True
            assert got_test_assumption_failure is True
            assert got_test_failed is True
            assert got_test_ignored is False

    def test__process_test_code(self):
        got_test_ignored = False

        class Listener(TestRunListener):

            def test_run_started(self, test_run_name: str) -> None:
                pass

            def test_run_ended(self, duration: float) -> None:
                pass

            def test_run_failed(self, error_message: str) -> None:
                pass

            def test_failed(self, class_name: str, test_name: str, stack_trace: str) -> None:
                pass

            def test_ignored(self, class_name: str, test_name: str) -> None:
                nonlocal got_test_ignored
                got_test_ignored = True

            def test_assumption_failure(self, class_name: str, test_name: str, stack_trace: str) -> None:
                pass

            def test_started(self, class_name: str, test_name: str) -> None:
                pass

            def test_ended(self, class_name: str, test_name: str, test_no: int, duration: float, msg: str = "") -> None:
                pass

        parser = InstrumentationOutputParser(test_listeners=[Listener()])
        parser._test_result = InstrumentationOutputParser.InstrumentTestResult()
        parser._process_test_code(parser.CODE_SKIPPED)
        assert got_test_ignored, "Failed to report skipped test"
        with pytest.raises(Exception):
            parser._process_test_code(42)  # unknown code raises exception