# flake8: noqay: F811
##########
# Tests the lower level TestApplication class against a running emulator.  These tests may
# be better server in mdl-integration-server directory, but we cannot start up an emulator
# from there
##########

import asyncio
import logging

import pytest

from mobiletestorchestrator.application import Application, TestApplication, ServiceApplication

log = logging.getLogger(__name__)


# noinspection PyShadowingNames
class TestTestApplication(object):

    def test_run(self, install_app, support_app: str, support_test_app: str):
        install_app(Application, support_app)
        test_app = install_app(TestApplication, support_test_app)

        # More robust testing of this is done in test of AndroidTestOrchestrator
        async def parse_output():
            async with test_app.run("-e", "class", "com.linkedin.mtotestapp.InstrumentedTestAllSuccess#useAppContext") \
                    as proc:
                async for line in proc.output(unresponsive_timeout=120):
                    log.debug(line)

        async def timer():
            await asyncio.wait_for(parse_output(), timeout=30)

        asyncio.get_event_loop().run_until_complete(timer())  # no Exception thrown

    def test_list_runners(self, install_app, support_test_app):
        test_app = install_app(TestApplication, support_test_app)
        instrumentation = test_app.list_runners()
        for instr in instrumentation:
            if "Runner" in instr:
                return
        assert False, "failed to get instrumentation runner"

    def test_invalid_apk_has_no_test_app(self, support_app, device):
        with pytest.raises(Exception) as exc_info:
            TestApplication.from_apk(support_app, device)
        assert "Test application's manifest does not specify proper instrumentation element" in str(exc_info.value)
