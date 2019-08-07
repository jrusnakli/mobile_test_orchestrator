import asyncio
import os
import time

import pytest

from androidtestorchestrator import ServiceApplication
from androidtestorchestrator.device import Device
from androidtestorchestrator.devicelog import DeviceLog


class TestDeviceLog:

    def test_set_get_logcat_buffer_size(self, device: Device):
        log = DeviceLog(device)
        log.set_logcat_buffer_size("20M")
        assert log.logcat_buffer_size == '20Mb'
        log.set_logcat_buffer_size(DeviceLog.DEFAULT_LOGCAT_BUFFER_SIZE)
        assert log.logcat_buffer_size == '5Mb'

    def test_logcat_and_clear(self, device: Device, android_service_app: ServiceApplication):
        output = []
        # call here waits for emulator startup, allowing other fixtures to complete in parallel
        device_log = DeviceLog(device)
        device_log.clear()  # ensure logcat is clean before the test
        counter = 5

        async def parse_logcat():
            async with await device_log.logcat("-v", "brief", "-s", "MTO-TEST") as lines:
                async for line in lines:
                    nonlocal output
                    if line.startswith("----"):
                        continue
                    output.append(line)
                    if len(output) >= counter:
                        break

        async def timer():
            await asyncio.wait_for(parse_logcat(), timeout=60)

        for _ in range(counter):
            android_service_app.broadcast(".MTOBroadcastReceiver", "--es", "command", "old_line",
                                       action="com.linkedin.mto.FOR_TEST_ONLY_SEND_CMD")
            time.sleep(1)
        asyncio.get_event_loop().run_until_complete(timer())
        for line in output:
            assert "old_line" in line

        output_before = output[:]
        retries = 3
        try:
            time.sleep(5) # give enough time for testapp to receive the intent and emmit log to logcat
            device_log.clear()
        except Device.CommandExecutionFailureException as e:
            if retries > 0 and "Failed to clear" in str(e):
                retries -= 1
            else:
                raise

        # capture more lines of output and make sure they don't match any in previous capture
        output = []
        # now emitting some new logs
        for _ in range(counter):
            android_service_app.broadcast(".MTOBroadcastReceiver", "--es", "command", "new_line",
                                       action="com.linkedin.mto.FOR_TEST_ONLY_SEND_CMD")
            time.sleep(1)

        asyncio.get_event_loop().run_until_complete(timer())
        for line in output:
            assert "new_line" in line
            assert line not in output_before

    def test_capture_mark_start_stop(self, device: Device, tmpdir):
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

    def test_invalid_output_path(self, fake_sdk, tmpdir):
        device = Device("fakeid", os.path.join(fake_sdk, "platform-tools", "adb"))
        tmpfile = os.path.join(str(tmpdir), "somefile")
        with open(tmpfile, 'w')as f:
            pass
        with pytest.raises(Exception) as exc_info:
            DeviceLog.LogCapture(device, tmpfile)
        assert "Path %s already exists; will not overwrite" % tmpfile in str(exc_info.value)

        with pytest.raises(Exception):
            logcap = DeviceLog.LogCapture(device, os.path.join(tmpdir, "newfile"))
            logcap.mark_end("proc_not_started_so_throw_exception")