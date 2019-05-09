# flake8: noqay: F811
##########
# Tests the lower level TestApplication class against a running emulator.  These tests may
# be better server in mdl-integration-server directory, but we cannot start up an emulator
# from there
##########

import asyncio
import logging
import pytest

from testbutlerlive.application import TestApplication, Application, ServiceApplication


log = logging.getLogger(__name__)


# noinspection PyShadowingNames
@pytest.fixture(scope='class')
def test_app(adb, request, test_butler_app, test_butler_test_app, test_butler_service):
    device = adb()
    butler_app = Application.install(test_butler_app(), device)
    test_app = TestApplication.install(test_butler_test_app(), device)
    service = ServiceApplication.install(test_butler_service(), device)

    def fin():
        """
        cleanup after test
        """
        butler_app.uninstall()
        test_app.uninstall()
        service.uninstall()

    request.addfinalizer(fin)
    return test_app


# noinspection PyShadowingNames
class TestTestApplication(object):

    def test_run(self, test_app: TestApplication):
        # More robust testing of this is done in test of AndroidTestOrchestrator
        async def parse_output():
            async for line in test_app.run("-e", "class", "com.linkedin.mdctest.TestButlerTest#testTestButlerRotation"):
                log.debug(line)

        async def timer():
            await asyncio.wait_for(parse_output(), timeout=30)

        asyncio.get_event_loop().run_until_complete(timer())  # no Exception thrown

    def test_list_runners(self, test_app: TestApplication):
        instrumentation = test_app.list_runners()
        for instr in instrumentation:
            if "Runner" in instr:
                return
        assert False, "failed to get instrumentation runner"
