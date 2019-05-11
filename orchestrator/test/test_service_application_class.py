# flake8: noqay: F811
##########
# Tests the lower level ServerApplication class against a running emulator.  These tests may
# be better server in mdl-integration-server directory, but we cannot start up an emulator
# from there
##########
import asyncio

from androidtestorchestrator import DeviceLog, Device
from androidtestorchestrator.application import ServiceApplication


class TestServiceApplication:

    @staticmethod
    def pidof(app):
        # an inconsistency that appears either on older emulators or perhaps our own custom emaulators
        # even if pidof fails due to it not being found, return code is 0, no exception is therefore
        # raised and worse, error is reported on stdout
        # Anpther inconsitency with our emulators: pidof not on the emulator?  And return code shows success :-*

        try:
            #Nomrally get an error code and an exception if package is not running:
            output = app.device.execute_remote_cmd("shell", "pidof", app.package_name)
            # however, LinkedIn-specific(?) or older emulators don't have this, and return no error code
            # so check output
            if not output:
                return False
            if "not found" in output:
                output = app.device.execute_remote_cmd("shell", "ps")
                return app.package_name in output
        except Exception as e:
            return False

    def test_start(self, device: Device, test_butler_service: str):
        app = ServiceApplication.install(test_butler_service, device)
        try:

            device_log = DeviceLog(device)
            device_log.clear()

            async def process_logcat():
                nonlocal  device_log
                async with await device_log.logcat("-s", "TestButler") as lines:
                    async for _ in lines:
                        # if we get one message from TestButler tag, it is started
                        break

            async def timer():
                await asyncio.wait_for(process_logcat(), timeout=5)

            app.start(".ButlerService", foreground=True)
            try:
                asyncio.get_event_loop().run_until_complete(timer())
            except asyncio.TimeoutError:
                assert False, "Failed to start test butler"
        finally:
            app.uninstall()
