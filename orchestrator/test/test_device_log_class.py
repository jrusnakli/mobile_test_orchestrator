import asyncio
import os
import time

import pytest

from androidtestorchestrator import ServiceApplication
from androidtestorchestrator.device import Device
from androidtestorchestrator.devicelog import DeviceLog


# noinspection PyShadowingNames
class TestDeviceLog:

    def test_set_get_logcat_buffer_size(self, device: Device):
        log = DeviceLog(device)
        log.set_logcat_buffer_size("20M")
        assert log.logcat_buffer_size == '20Mb'
        log.set_logcat_buffer_size("10M")
        assert log.logcat_buffer_size == '10Mb'

    def test_logcat_and_clear(self, device: Device, test_butler_service):
        service = ServiceApplication.from_apk(test_butler_service, device)
        try:
            output = []
            # call here waits for emulator startup, allowing other fixtures to complete in parallel
            device_log = DeviceLog(device)
            length = 25

            async def parse_logcat():
                async with await device_log.logcat() as lines:
                    async for line in lines:
                        nonlocal output
                        if line.startswith("----"):
                            continue
                        output.append(line)
                        if len(output) >= length:
                            break

            async def timer():
                await asyncio.wait_for(parse_logcat(), timeout=30)

            for _ in range(length+5):
                # use test butler to introduce log cat messages:
                service.start(".ButlerService", "--es", "command", "just for logcat",
                              intent="com.linkedin.android.testbutler.FOR_TEST_ONLY_SEND_CMD")
            asyncio.get_event_loop().run_until_complete(timer())
            assert len(output) >= length

            for _ in range(length+5):
                # use test butler to introduce log cat messages:
                service.start(".ButlerService", "--es", "command", "just for logcat",
                              intent="com.linkedin.android.testbutler.FOR_TEST_ONLY_SEND_CMD")

            output_before = output[:]
            retries = 3
            try:
                device_log.clear()
            except Device.CommandExecutionFailureException as e:
                if retries > 0 and "Failed to clear" in str(e):
                    retries -= 1
                else:
                    raise

            # capture more lines of output and make sure they don't match any in previous capture
            output = []

            asyncio.get_event_loop().run_until_complete(timer())
            assert len(output) >= length
            for line in output[10:]:
                assert line not in output_before
        finally:
            service.uninstall()

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
        assert "Path %s already exists; will not overwrite" % tmpfile in str(exc_info)

        with pytest.raises(Exception):
            logcap = DeviceLog.LogCapture(device, os.path.join(tmpdir, "newfile"))
            logcap.mark_end("proc_not_started_so_throw_exception")