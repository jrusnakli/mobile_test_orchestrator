import os
import shutil

from typing import Callable

import pytest

from testbutlerlive.devicelog import DeviceLog
from testbutlerlive.device import Device
from testbutlerlive.parsing import InstrumentationOutputParser
from testbutlerlive.reporting import TestListener, TestStatus


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

    def test_parse_lines(self, adb: Callable[[], Device], tmpdir):
        device = adb()
        tmpdir = str(tmpdir)
        with DeviceLog(device).capture_to_file(os.path.join(tmpdir, "test_output.log")) as logcat_marker:

            got_test_passed = False
            got_test_skipped = False
            got_test_failed = False
            got_test_assumption_violated = False

            try:
                class Listener(TestListener):

                    def test_suite_errored(self, test_suite_name: str, status_code: int):
                        pass

                    def test_assumption_violated(self, test_name: str, test_class: str, test_no: int, reason: str):
                        nonlocal got_test_assumption_violated
                        got_test_assumption_violated = True

                    def test_suite_started(self):
                        pass

                    def test_suite_ended(self, test_suite_name: str, test_count: int, execution_time: float):
                        pass

                    def test_ended(self, test_name: str, test_class: str, test_no: int, duration: float, msg: str = ""):
                        nonlocal got_test_passed
                        got_test_passed = True
                        assert test_name == "transcode1080pAvc"
                        assert test_class == "com.test.Test2"
                        assert test_no == 2
                        assert msg == "testing..."

                    def test_ignored(self, test_name: str, test_class: str, test_no: int, msg: str = ""):
                        nonlocal got_test_skipped
                        got_test_skipped = True
                        assert test_name == "transcode1440pAvc"
                        assert test_class == "com.test.TestSkipped"
                        assert test_no == 1
                        assert msg == "com.test.TestSkipped\n\ncontinuation line"

                    def test_failed(self, test_name: str, test_class: str, test_no: int, stack: str, msg: str = ""):
                        nonlocal got_test_failed
                        got_test_failed = True
                        assert test_name == "transcode2160pAvc"
                        assert test_class == "com.test.TestFailure"
                        assert test_no == 3
                        assert stack.strip() == TestInstrumentationOutputParser.EXPECTED_STACK_TRACE

                parser = InstrumentationOutputParser(test_reporter=Listener())
                parser.add_test_execution_listener(logcat_marker)  # TODO: not yet tested other than to exercise interface

                for line in self.example_output.splitlines():
                   parser.parse_line(line)

                assert got_test_passed is True
                assert got_test_assumption_violated is True
                assert got_test_failed is True
                assert got_test_skipped is False
            finally:
                shutil.rmtree(tmpdir)

    def test__process_test_code(self):
        got_test_ignored = False

        class Listener(TestListener):

            def test_suite_started(self, test_suite_name: str) -> None:
                pass

            def test_suite_ended(self, test_suite_name: str, test_count: int, execution_time: float) -> None:
                pass

            def test_suite_errored(self, test_suite_name: str, status_code: int, exc_message: str = "") -> None:
                pass

            def test_failed(self, test_name: str, test_class: str, test_no: int, stack: str, msg: str = "") -> None:
                pass

            def test_ignored(self, test_name: str, test_class: str, test_no: int, msg: str = "") -> None:
                nonlocal got_test_ignored
                got_test_ignored = True

            def test_assumption_violated(self, test_name: str, test_class: str, test_no: int, reason: str) -> None:
                pass

            def test_ended(self, test_name: str, test_class: str, test_no: int, duration: float, msg: str = "") -> None:
                pass

        parser = InstrumentationOutputParser(test_reporter=Listener())
        parser._test_result = InstrumentationOutputParser.InstrumentTestResult()
        parser._process_test_code(parser.CODE_SKIPPED)
        assert got_test_ignored, "Failed to report skipped test"
        with pytest.raises(Exception):
            parser._process_test_code(42)  # unknown code raises exception