import os
from multiprocessing import Queue

import pytest
import shutil

import sys

sys.path += [os.path.dirname(__file__), os.path.join(os.path.dirname(__file__), "..", "src")]

import support
from support import Config
from androidtestorchestrator.device import Device

TB_RESOURCES_DIR=os.path.abspath(os.path.join("..", "src", "androidtestorchestrator", "resources"))


configured = Queue()


@pytest.mark.try_last
def pytest_configure(config):
    global configured
    if not configured.empty():
        return
    configured.put(True)
    numcores = getattr(config.option, "numcores", None) or 1
    support.compile_support_app(numcores)
    support.compile_support_test_app(numcores)
    support.compile_test_butler_app(numcores)
    support.launch_emulators(numcores)


def pytest_sessionfinish(exitstatus):
    for proc in Config.procs():
        proc.kill()


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
        path = os.path.join(TB_RESOURCES_DIR, "apks", "debug", "TestButlerLive.apk")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        shutil.copy(app, path)
    return app


@pytest.fixture(scope='session')
def emulator():
    port = support.emulator_port_pool_q.get(timeout=400)
    if port is None:
        raise Exception("Failed to launch or boot emulator")
    return "emulator-%d" % port


@pytest.fixture(scope='session')
def device(emulator):  # kicks off emulator launch
    android_sdk = support.find_sdk()
    return Device(adb_path=os.path.join(android_sdk, "platform-tools", add_ext("adb")),
                  device_id=emulator)


@pytest.fixture
def fake_sdk(tmpdir_factory):
    tmpdir = tmpdir_factory.mktemp("sdk")
    os.makedirs(os.path.join(str(tmpdir), "platform-tools"))
    with open(os.path.join(str(tmpdir), "platform-tools", "adb"), 'w'):
        pass  # create a dummy file so that test of its existence as file passes
    return str(tmpdir)
