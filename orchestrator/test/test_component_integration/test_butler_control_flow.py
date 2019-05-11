"""
This test file performs rudimentary tests on the control flow of TestButler running on the device
It uses a backdoor request to TestButler to have it initiate a given string command, and the response here is hard
coded.  This is a little hokey, but allows us to test the very basics before anything more elaborate.  More
elaborate tests are done elsewhere
"""
import logging
import pytest

from collections import deque
from androidtestorchestrator.device import Device
from androidtestorchestrator.devicelog import DeviceLog
from androidtestorchestrator.application import ServiceApplication
from androidtestorchestrator.parsing import TestButlerCommandParser
from typing import Callable


TAG = "TestButler"
log = logging.getLogger(__name__)


@pytest.fixture(scope='session')
def install_butler(device: Device, test_butler_service: str):
    """
    fixture to install test butler service, prepare logcat
    :return: logcat process and iterator over stdout
    """
    service = ServiceApplication.install(test_butler_service, device)
    DeviceLog(device).clear()
    return service


# noinspection PyShadowingNames,PyUnusedLocal
class TestButlerControlFlow:

    @pytest.mark.parametrize('execution_number', range(10))
    # @pytest.mark.skipif(True, reason="Only for manual checkout.  Other tests will stress test integrated system")
    def test_control_flow(self, device: Device, install_butler: ServiceApplication, execution_number: int):
        butler_service = install_butler
        import asyncio
        cmd = ["logcat", "-v", "brief", "-s", TAG]
        line_count = 0

        flow_seqence = deque(["awaiting_command",
                              "awaiting_response_confirmation",
                              "awaiting_response_confirmation_code"])

        class Parser(TestButlerCommandParser):
            @staticmethod
            def request_command(cmd: str):
                cmd = cmd.replace(" ", "\\ ")
                butler_service.start(".ButlerService", "--es", "command", cmd,
                                     intent="com.linkedin.android.testbutler.FOR_TEST_ONLY_SEND_CMD")
                print("SENT REQUEST FOR BUTLER COMMAND")

            def parse_line(self, line: str):
                nonlocal line_count

                if line_count == 0:
                    line_count += 1
                    if not line.startswith("---------"):
                        log.error("Logcat did not start with expected line of output. Line was %s" % line)
                    # use standard first line of output from logcat to
                    # kickoff sending backdoor to tell device to echo a test butler command back to this host
                    self.request_command("a test command")
                    return
                if line.startswith('----'):
                    return

                if flow_seqence[0] == "awaiting_command":
                    # process line and next informational logcat message should be command that was sent
                    preamble, line = line.split(":", 1)
                    line = line.strip()
                    priority, tag = preamble.split("(")[0].split('/', 1)
                    if priority == "D":
                        return
                    flow_seqence.popleft()
                    assert tag == TAG
                    cmd_id, cmd = line.replace("\\", '').split(" ", 1)
                    assert cmd == "TEST_ONLY a test command"

                    # send hard-coded response back based on received id,
                    # with next to-list item being to wait on the response confirmation back from device
                    cmd_id = int(cmd_id)
                    self._send_response(cmd_id=cmd_id, response_code=0, response_msg="Success")
                elif flow_seqence[0] == "awaiting_response_confirmation":
                    if line.startswith("-----"):
                        log.error("Received unexpected logcat message:\n  " + line)
                        if "crash" in line:
                            raise Exception("logcat crashed")
                        return
                    if "Error" in line:
                        raise Exception("Error processing response:  %s" % line)
                    try:
                        preamble, line = line.split(":", 1)
                        line = line.strip()
                        priority, tag = preamble.split("(")[0].split('/', 1)
                    except ValueError:
                        log.error("Received unexpected logcat message:\n  " + line)
                        return
                    if not line.startswith("<FOR_TEST>"):
                        return
                    if priority != 'D':
                        return
                    flow_seqence.popleft()
                    assert tag == TAG
                    assert line == "<FOR_TEST> CMD RESPONSE MSG: Success", "Sending command" in line
                elif flow_seqence[0] == "awaiting_response_confirmation_code":
                    try:
                        preamble, line = line.split(":", 1)
                        line = line.strip()
                        priority, tag = preamble.split("(")[0].split('/', 1)
                    except:
                        log.error("Recieved unexpected logcat message:\n  " + line)
                        return
                    if not line.startswith("<FOR_TEST>") or priority != 'D':
                        return
                    flow_seqence.popleft()
                    assert tag == TAG
                    assert line == "<FOR_TEST> CMD RESPONSE STATUS: 0"

        async def parse_logcat():
            line_parser = Parser(app_under_test=None, service=butler_service)
            async for line in DeviceLog(device).logcat("-v", "brief", "-s", TAG):
                try:
                    print(line)
                    line_parser.parse_line(line)
                except Exception as e:
                    log.error("Exception in processing line: %s\n %s" % (line, str(e)))
                    asyncio.get_event_loop().stop()
                if len(flow_seqence) == 0:
                    break

        async def timer():
            await asyncio.wait_for(parse_logcat(), timeout=3*60)

        asyncio.get_event_loop().run_until_complete(timer())
        assert len(flow_seqence) == 0, "Transactions were incomplete"
