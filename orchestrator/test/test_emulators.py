import sys

import getpass
from queue import Empty

import asyncio
import logging
import os
import pytest

from pathlib import Path

from mobiletestorchestrator.device import Device
from mobiletestorchestrator.device_pool import AsyncEmulatorPool
from mobiletestorchestrator.emulators import EmulatorBundleConfiguration, Emulator

log = logging.getLogger("MTO")
log.setLevel(logging.INFO)


def find_sdk():
    """
    :return: android sdk location

    :rasise: Exception if sdk not found through environ vars or in standard user-home location per platform
    """
    if os.environ.get("ANDROID_HOME"):
        log.info("Please use ANDROID_SDK_ROOT over ANDROID_HOME")
        os.environ["ANDROID_SDK_ROOT"] = os.environ["ANDROID_HOME"]
        del os.environ["ANDROID_HOME"]
    if os.environ.get("ANDROID_SDK_ROOT"):
        os.environ["ANDROID_HOME"] = os.environ["ANDROID_SDK_ROOT"]  # some android tools still expecte this
        return os.environ["ANDROID_SDK_ROOT"]

    if sys.platform == 'win32':
        android_sdk = os.path.join(os.path.expanduser("~"), "AppData", "Local", "Android", "Sdk")
    elif sys.platform == 'darwin':
        android_sdk = os.path.join(os.path.expanduser("~"), "Library", "Android", "Sdk")
    else:
        android_sdk = os.path.join(os.path.expanduser("~"), "Android", "Sdk")
    if not os.path.exists(android_sdk):
        raise Exception("Please set ANDROID_SDK_ROOT")
    os.environ["ANDROID_SDK_ROOT"] = android_sdk
    os.environ["ANDROID_HOME"] = android_sdk  # some android tools still expecte this
    return android_sdk


class TestEmulator:
    ARGS = [
        "-no-window",
        "-no-audio",
        # "-wipe-data",
        "-gpu", "off",
        "-no-boot-anim",
        "-skin", "320x640",
        "-partition-size", "1024"
    ]

    @pytest.mark.skipif(True,
                        reason="Must be run standalone as it can conflict with session level emultor fixtures")
    @pytest.mark.asyncio
    async def test_launch(self, emulator_config):
        emulator = await Emulator.launch(5584, emulator_config.AVD, emulator_config.EMULATOR_CONFIG,
                                         *self.ARGS)
        assert emulator.is_alive
        emulator.kill()
        if emulator.is_alive:
            # adb command to kill emulator is asynchronous, so may have to wait
            await asyncio.sleep(5)
        assert not emulator.is_alive

    @pytest.mark.asyncio
    async def test_launch_bad_port(self, emulator_config: EmulatorBundleConfiguration):
        with pytest.raises(ValueError):
            await Emulator.launch(2345, "MTO_test_emaultor", emulator_config, *self.ARGS)


class TestEmulatorPool:

    @pytest.mark.asyncio
    @pytest.mark.skipif("STANDALONE_Q_TEST" not in os.environ,
                        reason="Can only run this standalone, as testing in the mainstream brings up emulators already")
    async def test_start_queue(self, emulator_config):
        async with AsyncEmulatorPool.create(2, emulator_config.AVD, emulator_config.EMULATOR_CONFIG,
                                            *emulator_config.ARGS) as queue:
            async with queue.reserve(timeout=10*60) as emulator1:
                # stagger the async-with's so that emulator2 is releinquished by itself
                async with queue.reserve(timeout=10*60) as emulator2:
                    assert emulator1 is not None
                    assert emulator2 is not None

                    with pytest.raises(Empty):
                        queue.reserve(timeout=1)

                # emulator 2 is now available again
                emulator_next = queue.reserve(timeout=5)
                assert emulator_next == emulator2  # only one left and avaialble
                with pytest.raises(Empty):
                    queue.reserve(timeout=1)


class TestLeasedEmulator:

    @pytest.mark.asyncio
    async def test_lease(self, device: Emulator):
        default_config = EmulatorBundleConfiguration(sdk=Path(find_sdk()))
        leased_emulator = AsyncEmulatorPool.LeasedEmulator(device.device_id)
        await leased_emulator.set_timer(expiry=1)
        await asyncio.sleep(3)
        with pytest.raises(Device.LeaseExpired):
            # access to any attribute should throw an exception (except device_id)
            leased_emulator.model
        assert leased_emulator.device_id == device.device_id  # device_id should be accessible always
