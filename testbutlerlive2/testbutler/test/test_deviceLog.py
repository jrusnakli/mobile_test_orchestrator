import asyncio
import os
import time

from testbutlerlive.device import Device
from testbutlerlive.devicelog import DeviceLog


# noinspection PyShadowingNames
class TestDeviceLog:

    def test_logcat_and_clear(self, adb):
        output = []
        # call here waits for emulator startup, allowing other fixtures to complete in parallel
        device_log = DeviceLog(adb())
        length = 15

        async def parse_logcat():
            async for line in device_log.logcat():
                nonlocal output
                if line.startswith("----"):
                    continue
                output.append(line)
                if len(output) >= length:
                    break

        async def timer():
            await asyncio.wait_for(parse_logcat(), timeout=30)

        asyncio.get_event_loop().run_until_complete(timer())
        assert len(output) >= length

        output_before = output[:]
        device_log.clear()

        # capture more lines of output and make sure they don't match any in previous capture
        output = []

        asyncio.get_event_loop().run_until_complete(timer())
        assert len(output) >= length
        for line in output[10:]:
            assert line not in output_before

    def test_capture_mark_start_stop(self, adb, tmpdir):
        device: Device = adb()
        device_log = DeviceLog(device)
        output_path = os.path.join(str(tmpdir), "logcat.txt")
        with device_log.capture_to_file(output_path) as log_capture:
            time.sleep(2)
            log_capture.mark_start("test1")
            time.sleep(5)
            log_capture.mark_end("test1")
            time.sleep(2)
            assert "test1.start" in log_capture.markers
            assert "test1.end" in log_capture.markers
            assert log_capture.markers["test1.start"] < log_capture.markers["test1.end"]

