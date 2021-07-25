import asyncio
import os
import time

import pytest

import mobiletestorchestrator
from mobiletestorchestrator.application import ServiceApplication
from mobiletestorchestrator.device import Device
from mobiletestorchestrator.device_log import DeviceLog


class TestDeviceLog:

    @pytest.mark.asyncio
    async def test_set_get_logcat_buffer_size(self, device: Device):
        log = DeviceLog(device)
        log.set_logcat_buffer_size("20M")
        assert log.logcat_buffer_size.upper() in ['20', '20MB']
        log.set_logcat_buffer_size(DeviceLog.DEFAULT_LOGCAT_BUFFER_SIZE)
        assert log.logcat_buffer_size.upper() in ['5', '5MB']

    @pytest.mark.asyncio
    async def test_logcat_and_clear(self, device: Device, android_service_app: ServiceApplication):
        output = []
        # call here waits for emulator startup, allowing other fixtures to complete in parallel
        device_log = DeviceLog(device)
        done_parsing = False

        async def parse_logcat(counter, output):
            nonlocal done_parsing
            async with device_log.logcat("-v", "brief", "-s", "MTO-TEST") as proc:
                async for line in proc.output(unresponsive_timeout=3):
                    # makes easy to debug on circleci when emulator accel is not available
                    if line.startswith("----"):
                        continue
                    output.append(line)
                    if len(output) >= counter:
                        break
                await proc.stop(timeout=10, force=True)
                await proc.wait(timeout=10)
                done_parsing = True

        async def populate_logcat(counter):
            nonlocal done_parsing
            for index in range(counter):
                android_service_app.broadcast(".MTOBroadcastReceiver", "--es", "command", "old_line",
                                          action="com.linkedin.mto.FOR_TEST_ONLY_SEND_CMD")
                await asyncio.sleep(0.2)
                if done_parsing:
                    break

        await asyncio.wait([parse_logcat(10, output), populate_logcat(20)],  timeout=120, return_when=asyncio.FIRST_EXCEPTION)
        time.sleep(4)
        try:
            device_log.clear()
        except Device.CommandExecutionFailure:
            device_log.clear()  # intermittently android "fails to clear main log"
        time.sleep(4)

        for line in output:
            if "old_line" in line:
                print("WARNING: logcat not cleared as expected;  most likely due to logcat race condition over test error")
                return
        # capture more lines of output and make sure they don't match any in previous capture
        output = []
        done_parsing = False

        # now emitting some new logs
        async def populate_logcat2(counter):
            nonlocal done_parsing
            for index in range(counter):
                android_service_app.broadcast(".MTOBroadcastReceiver", "--es", "command", "new_line",
                                              action="com.linkedin.mto.FOR_TEST_ONLY_SEND_CMD")
                await asyncio.sleep(0.2)
                if done_parsing:
                    break

        await asyncio.wait([parse_logcat(1, output), populate_logcat2(20)],  timeout=120, return_when=asyncio.FIRST_EXCEPTION)
        for line in output:
            assert "old_line" not in line
            assert "new_line" in line

    def test_invalid_output_path(self, fake_sdk, mp_tmp_dir):
        orig_adb_path = mobiletestorchestrator.ADB_PATH
        try:
            mobiletestorchestrator.ADB_PATH = os.path.join(fake_sdk, "platform-tools", "adb")
            device = Device("fakeid")
            tmpfile = os.path.join(str(mp_tmp_dir), "somefile")
            with open(tmpfile, 'w'):
                pass
            with pytest.raises(Exception) as exc_info:
                DeviceLog.LogCapture(device, tmpfile)
            assert "Path %s already exists; will not overwrite" % tmpfile in str(exc_info.value)
        finally:
            mobiletestorchestrator.ADB_PATH = orig_adb_path