import asyncio
import os
import threading

import pytest
import shutil

import sys

sys.path += [os.path.dirname(__file__), os.path.join(os.path.dirname(__file__), "..", "src")]

import support
from support import Config
from androidtestorchestrator.device import Device

TB_RESOURCES_DIR =os.path.abspath(os.path.join("..", "src", "androidtestorchestrator", "resources"))

# Run a bunch of stuff in the background, such as compiling depenent apks for test and launching emulators
# This allows tests to potentially run in parallel (if not dependent on output of these tasks), parallelizes
# these dependent tasks. The tasks populate results out to Queue's that test fixtures then use as needed
# (hence once a test needs that fixture, it would block until the dependent task(s) are complete, but only then)


class BackgroundThread(threading.Thread):
    def run(self):
        asyncio.set_event_loop(asyncio.new_event_loop())
        tasks = [
            support.compile_support_app(),
            support.compile_support_test_app(),
            support.compile_test_butler_app(),
        ]

        async def execute_parallel():
            await asyncio.wait(tasks)
            print("DONE")
        asyncio.get_event_loop().run_until_complete(
            execute_parallel()
        )

background_thread = BackgroundThread()
background_thread.start()


def pytest_sessionfinish(exitstatus):
    for proc in Config.procs():
        proc.kill()
        proc.poll()
    background_thread.join()


def add_ext(app):
    """
    if Windows, add ".exe" extension
    :param app: app path to add extension to
    :return: app with .exe extension if Windows, else app
    """
    if sys.platform == 'win32':
        return app + ".exe"
    return app


@pytest.fixture(scope='session')
def support_test_app():
    app = support.support_test_app_q.get()
    if app is None:
        raise Exception("Failed to build test app")
    return app


@pytest.fixture(scope='session')
def support_app():
    support_app = support.support_app_q.get()
    if isinstance(support_app, Exception) or support_app is None:
        raise Exception("Failed to build support app")
    return support_app


@pytest.fixture(scope='session')
def test_butler_service():
    app = support.test_butler_app_q.get()
    if app is None:
        raise Exception("Failed to build test butler service")
    else:
        path = os.path.join(TB_RESOURCES_DIR, "apks", "TestButlerLive.apk")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        shutil.copy(app, path)
    return app


@pytest.fixture(scope='session')
def emulator():
    port = 5554
    support.launch_emulator(port)
    return "emulator-%d" % port


@pytest.fixture(scope='session')
def sole_emulator(emulator):  # kicks off emulator launch
    android_sdk = support.find_sdk()
    Device.set_default_adb_timeout(30)  # emulator without accel can be slow
    Device.set_default_long_adb_timeout(240*4)
    return Device(adb_path=os.path.join(android_sdk, "platform-tools", add_ext("adb")),
                  device_id=emulator)


emulator_lock = threading.Semaphore(1)


@pytest.fixture
def device(sole_emulator, request):
    """
    test-specific fixture that allows other tests not dependent on this fixture to run in parallel,
    but forces dependent tests to run serially
    :param sole_emulator: the only emulator we test against
    :param request:
    :return: sole test emulator
    """
    emulator_lock.acquire()

    def unlock():
        emulator_lock.release()

    request.addfinalizer(unlock)
    return sole_emulator


@pytest.fixture
def fake_sdk(tmpdir_factory):
    tmpdir = tmpdir_factory.mktemp("sdk")
    os.makedirs(os.path.join(str(tmpdir), "platform-tools"))
    with open(os.path.join(str(tmpdir), "platform-tools", "adb"), 'w'):
        pass  # create a dummy file so that test of its existence as file passes
    return str(tmpdir)
