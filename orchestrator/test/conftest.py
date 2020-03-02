import os
import sys
import threading
from pathlib import Path

import pytest

from androidtestorchestrator.application import Application, TestApplication, ServiceApplication
from androidtestorchestrator.device import Device
from . import support
from .support import Config, uninstall_apk

TAG_MTO_DEVICE_ID = "MTO_DEVICE_ID"

# Run a bunch of stuff in the background, such as compiling depenent apks for test and launching emulators
# This allows tests to potentially run in parallel (if not dependent on output of these tasks), parallelizes
# these dependent tasks. The tasks populate results out to Queue's that test fixtures then use as needed
# (hence once a test needs that fixture, it would block until the dependent task(s) are complete, but only then)


class BackgroundThread(threading.Thread):
    def run(self):
        support.compile_all()


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


# noinspection PyShadowingNames
@pytest.fixture()
def android_test_app(device,
                     request,
                     support_app: str,
                     support_test_app: str):
    uninstall_apk(support_app, device)
    uninstall_apk(support_test_app, device)
    app_for_test = TestApplication.from_apk(support_test_app, device)
    support_app = Application.from_apk(support_app, device)

    def fin():
        """
        Leave the campground as clean as you found it:
        """
        app_for_test.uninstall()
        support_app.uninstall()
    request.addfinalizer(fin)
    return app_for_test


# noinspection PyShadowingNames
@pytest.fixture()
def android_test_app2(device2,
                      request,
                      support_app: str,
                      support_test_app: str):
    uninstall_apk(support_app, device2)
    uninstall_apk(support_test_app, device2)
    app_for_test = TestApplication.from_apk(support_test_app, device2)
    support_app = Application.from_apk(support_app, device2)

    def fin():
        """
        Leave the campground as clean as you found it:
        """
        app_for_test.uninstall()
        support_app.uninstall()
    request.addfinalizer(fin)
    return app_for_test



@pytest.fixture()
def android_service_app(device,
                        request,
                        support_app: str):
    # the support app is created to act as a service app as well
    uninstall_apk(support_app, device)
    service_app = ServiceApplication.from_apk(support_app, device)

    def fin():
        """
        Leave the campground as clean as you found it:
        """
        service_app.uninstall()

    request.addfinalizer(fin)
    return service_app


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
def emulator():
    if TAG_MTO_DEVICE_ID in os.environ:
        deviceid = os.environ[TAG_MTO_DEVICE_ID]
        print(f"Using user-specified device id: {deviceid}")
        return deviceid
    port = 5554
    support.launch_emulator(port)
    return "emulator-%d" % port


@pytest.fixture(scope='session')
def emulator2():
    if os.environ.get("CIRCLECI"):
        raise Exception("Invalid environment for running multiple emulators")
    if TAG_MTO_DEVICE_ID in os.environ:
        deviceid = os.environ[TAG_MTO_DEVICE_ID]
        print(f"Using user-specified device id: {deviceid}")
        return deviceid
    port = 5556
    support.launch_emulator(port)
    return "emulator-%d" % port


@pytest.fixture(scope='session')
def sole_emulator(emulator):  # kicks off emulator launch
    android_sdk = support.find_sdk()
    Device.set_default_adb_timeout(30)  # emulator without accel can be slow
    Device.set_default_long_adb_timeout(240*4)
    return Device(adb_path=os.path.join(android_sdk, "platform-tools", add_ext("adb")),
                  device_id=emulator)


@pytest.fixture(scope='session')
def device2(emulator2): # kicks off emulator launch
    android_sdk = support.find_sdk()
    Device.set_default_adb_timeout(30)  # emulator without accel can be slow
    Device.set_default_long_adb_timeout(240*4)
    return Device(adb_path=os.path.join(android_sdk, "platform-tools", add_ext("adb")),
                  device_id=emulator2)




@pytest.fixture
def device(sole_emulator):
    """
    test-specific fixture that allows other tests not dependent on this fixture to run in parallel,
    but forces dependent tests to run serially
    :param sole_emulator: the only emulator we test against
    :param request:
    :return: sole test emulator
    """
    yield sole_emulator


@pytest.fixture
def fake_sdk(tmpdir_factory):
    tmpdir = tmpdir_factory.mktemp("sdk")
    os.makedirs(os.path.join(str(tmpdir), "platform-tools"))
    with open(os.path.join(str(tmpdir), "platform-tools", "adb"), 'w'):
        pass  # create a dummy file so that test of its existence as file passes
    return str(tmpdir)


@pytest.fixture
def in_tmp_dir(tmp_path: Path) -> Path:
    cwd = os.getcwd()
    os.chdir(tmp_path)
    yield tmp_path
    os.chdir(cwd)


@pytest.fixture
def install_app(device: Device):
    apps = []

    def do_install(app_cls: Application, package_name: str):
        uninstall_apk(package_name, device)
        app = app_cls.from_apk(package_name, device)
        apps.append(app)
        return app

    yield do_install

    for app in apps:
        app.uninstall()
