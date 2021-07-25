# flake8: noqay: F811
##########
# Tests the lower level TestApplication class against a running emulator.  These tests may
# be better server in mdl-integration-server directory, but we cannot start up an emulator
# from there
##########

import logging

import pytest

from mobiletestorchestrator.application import Application, TestApplication, AsyncTestApplication, AsyncApplication
from mobiletestorchestrator.device import Device
from .support import uninstall_apk

log = logging.getLogger(__name__)


# noinspection PyShadowingNames
class TestTestApplication:

    @pytest.mark.asyncio
    async def test_run(self, install_app, support_app: str, support_test_app: str):
        install_app(Application, support_app)
        test_app = install_app(TestApplication, support_test_app)

        # More robust testing of this is done in test of AndroidTestOrchestrator
        async with await test_app.run("-e", "class", "com.linkedin.mtotestapp.InstrumentedTestAllSuccess#useAppContext") \
                as proc:
            async for line in proc.output(unresponsive_timeout=120):
                log.debug(line)

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


# noinspection PyShadowingNames
class TestTestApplicationAsync:

    @pytest.mark.asyncio
    async def test_run(self, device: Device, support_app: str, support_test_app: str):
        uninstall_apk(support_app, device)
        uninstall_apk(support_test_app, device)
        app = await AsyncApplication.from_apk(support_app, device)
        test_app = await AsyncTestApplication.from_apk(support_test_app, device)

        # More robust testing of this is done in test of AndroidTestOrchestrator
        async with await test_app.run("-e", "class", "com.linkedin.mtotestapp.InstrumentedTestAllSuccess#useAppContext") \
                as proc:
            async for line in proc.output(unresponsive_timeout=120):
                log.debug(line)
        await app.uninstall()
        await test_app.uninstall()

    @pytest.mark.asyncio
    async def test_list_runners(self, device: Device, support_test_app):
        uninstall_apk(support_test_app, device)
        test_app = await AsyncTestApplication.from_apk(support_test_app, device)
        async for runner in test_app.iterate_runners():
            if "Runner" in runner:
                await test_app.uninstall()
                return
        await test_app.uninstall()
        assert False, "failed to get instrumentation runner"

    @pytest.mark.asyncio
    async def test_invalid_apk_has_no_test_app(self, support_app, device):
        with pytest.raises(Exception) as exc_info:
            await AsyncTestApplication.from_apk(support_app, device)
        assert "Test application's manifest does not specify proper instrumentation element" in str(exc_info.value)
