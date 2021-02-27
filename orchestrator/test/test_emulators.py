import sys

import getpass
import pytest_mproc
from queue import Empty

import asyncio
import logging
import os
import pytest

from pathlib import Path
from mobiletestorchestrator.emulators import EmulatorBundleConfiguration, Emulator, EmulatorQueue

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
        "-wipe-data",
        "-gpu", "off",
        "-no-boot-anim",
        "-skin", "320x640",
        "-no-window",
        "-no-audio",
        "-partition-size", "1024"
    ]
    if "CIRCLECI" in os.environ:
        ARGS.append("-no-accel")
    EMULATOR_CONFIG = EmulatorBundleConfiguration(
        sdk=Path(find_sdk()),
        boot_timeout=10 * 60  # seconds
    )
    AVD = "MTO_emulator"  # set up before tests execute

    @pytest.mark.skipif(getpass.getuser() == 'circleci',
                        reason="Unable to run multiple emulators in circleci without upgrading machine")
    @pytest_mproc.group("EMULATOR_LAUNCH")
    def test_launch(self):
        async def launch():
            emulator = await Emulator.launch(5584, self.AVD, self.EMULATOR_CONFIG, *self.ARGS)
            assert emulator.is_alive()
            emulator.kill()
            if emulator.is_alive():
                # adb command to kill emulator is asynchronous, so may have to wait
                await asyncio.sleep(5)
            assert not emulator.is_alive()
        asyncio.get_event_loop().run_until_complete(launch())

    def test_launch_bad_port(self):
        async def launch():
            await Emulator.launch(2345, self.AVD, self.EMULATOR_CONFIG, *self.ARGS)

        with pytest.raises(ValueError):
            asyncio.get_event_loop().run_until_complete(launch())


class TestEmulatorQueue:

    @pytest.mark.skipif("STANDALONE_Q_TEST" not in os.environ,
                        reason="Can only run this standalone, as testing itself is using an EmulatorQueue")
    def test_start_queue(self):
        with EmulatorQueue.start(2, TestEmulator.AVD, TestEmulator.EMULATOR_CONFIG, *self.ARGS) as queue:
            emulator1 = queue.reserve(timeout=10*60)
            emulator2 = queue.reserve(timeout=10*60)
            with pytest.raises(Empty):
                queue.reserve(timeout=5)

            assert emulator1 is not None
            assert emulator2 is not None
            queue.relinquish(emulator1)
            emulator_next = queue.reserve(timeout=5)
            assert emulator_next == emulator1  # only one left and avaialble
            with pytest.raises(Empty):
                queue.reserve(timeout=5)
            queue.relinquish(emulator_next)
            queue.relinquish(emulator2)
