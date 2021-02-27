from typing import Mapping, Any

import os

from mobiletestorchestrator.device import Device
from mobiletestorchestrator.devicelog import DeviceLog
from mobiletestorchestrator.parsing import InstrumentationOutputParser
from mobiletestorchestrator.reporting import TestRunListener
from mobiletestorchestrator.timing import StopWatch


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

    def test_parse_lines(self, device: Device, mp_tmp_dir):
        mp_tmp_dir = str(mp_tmp_dir)
        with DeviceLog(device).capture_to_file(os.path.join(mp_tmp_dir, "test_output.log")) as logcat_marker:

            got_test_ignored = False
            got_test_failed = False
            got_test_assumption_failure = False

            class ExecutionListener(StopWatch):

                def mark_start(self, name: str) -> None:
                    assert name in ['com.test.TestSkipped', 'com.test.Test2', 'com.test.TestFailure']
                    self._name = name

                def mark_end(self, name: str) -> None:
                    assert name == self._name

            class Listener(TestRunListener):

                def test_run_failed(self, error_message: str):
                    pass

                def test_assumption_failure(self, class_name: str, test_name: str, stack_trace: str):
                    nonlocal got_test_assumption_failure
                    got_test_assumption_failure = True

                def test_run_started(self, test_run_name: str):
                    assert test_run_name in ['com.test.TestSkipped', 'com.test.Test2', 'com.test.TestFailure']

                def test_run_ended(self, duration: float, **kwargs):
                    pass

                def test_started(self, class_name: str, test_name: str):
                    assert class_name in ['com.test.TestSkipped', 'com.test.Test2', 'com.test.TestFailure']

                def test_ended(self, class_name: str, test_name: str, **kwargs: Mapping[Any, Any]):
                    assert test_name in ["transcode1080pAvc", "transcode1440pAvc", "transcode2160pAvc"]
                    assert class_name in ["com.test.Test2", "com.test.TestSkipped", "com.test.TestFailure"]

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

            parser = InstrumentationOutputParser(test_run_listener=Listener())
            parser.add_test_execution_listener(ExecutionListener())
            parser.add_test_execution_listener(logcat_marker)
            for line in self.example_output.splitlines():
                parser.parse_line(line)

            assert got_test_assumption_failure is True
            assert got_test_failed is True
            assert got_test_ignored is False
            assert parser.total_test_count == 3
            assert parser.num_tests_expected == 3
            assert abs(parser.execution_time - 9.387) < 0.0001

    def test_non_int_status_code(self):
        parser = InstrumentationOutputParser()
        test = parser._get_current_test()
        parser._parse_status_code("not_an_int")
        assert test.code == parser.CODE_ERROR
