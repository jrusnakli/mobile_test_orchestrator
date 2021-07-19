from typing import Any, Optional

from mobiletestorchestrator.parsing import InstrumentationOutputParser
from mobiletestorchestrator.reporting import TestExecutionListener


class TestInstrumentationOutputParser(object):
    class EmptyListener(TestExecutionListener):

        def test_suite_started(self, test_run_name: str, count: int = 0) -> None:
            pass

        def test_suite_ended(self, test_run_name: str, duration: float = -1.0, **kwargs: Optional[Any]) -> None:
            pass

        def test_suite_failed(self, test_run_name: str, error_message: str) -> None:
            pass

        def test_failed(self, test_run_name: str, class_name: str, test_name: str, stack_trace: str) -> None:
            pass

        def test_ignored(self, test_run_name: str, class_name: str, test_name: str) -> None:
            pass

        def test_assumption_failure(self, test_run_name: str, class_name: str, test_name: str,
                                    stack_trace: str) -> None:
            pass

        def test_started(self, test_run_name: str, class_name: str, test_name: str) -> None:
            pass

        def test_ended(self, test_run_name: str, class_name: str, test_name: str, **kwargs: Optional[Any]) -> None:
            pass

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

    def test_parse_lines(self):
        got_test_passed = False
        got_test_ignored = False
        got_test_failed = False
        got_test_assumption_failure = False

        class Listener(self.EmptyListener):

            def test_assumption_failure(self, test_run_name: str, class_name: str, test_name: str, stack_trace: str):
                nonlocal got_test_assumption_failure
                got_test_assumption_failure = True

            def test_ended(self, test_run_name: str, class_name: str, test_name: str, **kwargs: Optional[Any]):
                nonlocal got_test_passed
                got_test_passed = True
                if not got_test_failed and not got_test_ignored and not got_test_assumption_failure:
                    assert test_name == "transcode1080pAvc"
                    assert class_name == "com.test.Test2"

            def test_ignored(self, test_run_name: str, class_name: str, test_name: str):
                nonlocal got_test_ignored
                got_test_ignored = True
                assert test_name == "transcode1440pAvc"
                assert class_name == "com.test.TestSkipped"

            def test_failed(self, test_run_name: str, class_name: str, test_name: str, stack_trace: str):
                nonlocal got_test_failed
                got_test_failed = True
                assert test_name == "transcode2160pAvc"
                assert class_name == "com.test.TestFailure"
                assert stack_trace.strip() == TestInstrumentationOutputParser.EXPECTED_STACK_TRACE

        parser = InstrumentationOutputParser("test_run")
        parser.add_execution_listener(Listener())

        for line in self.example_output.splitlines():
            parser.parse_line(line)

        assert got_test_passed is True
        assert got_test_assumption_failure is True
        assert got_test_failed is True
        assert got_test_ignored is False

    def test__process_test_code(self):
        got_test_assumption_failure = False
        got_test_error = False

        class Listener(self.EmptyListener):

            def test_assumption_failure(self, test_run_name: str, class_name: str, test_name: str, stack_trace: str):
                nonlocal got_test_assumption_failure
                got_test_assumption_failure = True
                assert test_run_name == "test_run"
                assert test_name == "some_test"
                assert class_name == "TestClass"

            def test_failed(self, test_run_name: str, class_name: str, test_name: str, *args, **kargs):
                nonlocal got_test_error
                got_test_error = True
                assert test_run_name == "test_run"
                assert test_name == "some_test2"
                assert class_name == "TestClass2"

        parser = InstrumentationOutputParser("test_run", Listener())
        parser._current_test = InstrumentationOutputParser.TestParsingResult()
        parser._current_test.test_name = "some_test"
        parser._current_test.test_class = "TestClass"
        parser._parse_status_code(str(parser.CODE_ASSUMPTION_VIOLATION))
        assert got_test_assumption_failure, "Failed to report skipped test"
        assert not got_test_error, "Got unexpected test error"
        parser._current_test = InstrumentationOutputParser.TestParsingResult()
        parser._current_test.test_name = "some_test2"
        parser._current_test.test_class = "TestClass2"
        parser._parse_status_code("42")  # unknown code raises exception
        assert got_test_error, "Failed to detect test failure/error"

    def test_add_test_listeners(self):
        parser = InstrumentationOutputParser("test_run")
        listeners = [self.EmptyListener(), self.EmptyListener()]
        parser.add_execution_listeners(listeners)
        assert parser._execution_listeners == listeners

    def test_non_int_status_code(self):
        parser = InstrumentationOutputParser("test_run")
        test = parser._get_current_test()
        parser._parse_status_code("not_an_int")
        assert test.code == parser.CODE_ERROR
