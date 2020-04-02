import os
import pytest_mproc
from pathlib import Path

import pytest
from typing import Optional

from androidtestorchestrator.application import Application, TestApplication, ServiceApplication
from androidtestorchestrator.device import Device
from androidtestorchestrator.emulators import EmulatorQueue, EmulatorBundleConfiguration
from . import support
from .support import uninstall_apk

TAG_MTO_DEVICE_ID = "MTO_DEVICE_ID"

# Run a bunch of stuff in the background, such as compiling depenent apks for test and launching emulators
# This allows tests to potentially run in parallel (if not dependent on output of these tasks), parallelizes
# these dependent tasks. The tasks populate results out to Queue's that test fixtures then use as needed
# (hence once a test needs that fixture, it would block until the dependent task(s) are complete, but only then)


def _start_queue():
    AVD = "MTO_emulator"
    CONFIG = EmulatorBundleConfiguration(
        sdk=Path(support.find_sdk()),
        boot_timeout=10 * 60  # seconds
    )
    support.ensure_avd(str(CONFIG.sdk))
    if "CIRCLECI" in os.environ or TAG_MTO_DEVICE_ID in os.environ:
        count = 1
    else:
        count = int(os.environ.get("MTO_EMULATOR_COUNT", "4"))
    if TAG_MTO_DEVICE_ID in os.environ:
        queue = EmulatorQueue(count)
    else:
        queue = EmulatorQueue.start(count, AVD, CONFIG,
                                    "-no-window",
                                    "-wipe-data",
                                    "-gpu", "off",
                                    "-no-boot-anim",
                                    "-skin", "320x640",
                                    "-partition-size", "1024"
                                    )
    return queue


@pytest_mproc.utils.global_session_context()
class TestEmulatorQueue:
    _queue: EmulatorQueue = _start_queue()
    _app_queue, _test_app_queue = support.compile_all()
    _app: Optional[str] = None
    _test_app: Optional[str] = None

    def __enter__(self):
        self._queue.__enter__()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._queue.__exit__(exc_type, exc_val, exc_tb)

    @classmethod
    def test_app(cls):
        if cls._test_app is None:
            cls._test_app = cls._test_app_queue.get()
        return cls._test_app

    @classmethod
    def app(cls):
        if cls._app is None:
            cls._app = cls._app_queue.get()
        return cls._app


@pytest.fixture()
def device(request):
    if TAG_MTO_DEVICE_ID in os.environ:
        deviceid = os.environ[TAG_MTO_DEVICE_ID]
        print(f"Using user-specified device id: {deviceid}")
        # force this into the underlying queue as it is a one-off path:
        TestEmulatorQueue.queue._q.put(Device(deviceid,
                                              str(TestEmulatorQueue.CONFIG.adb_path())))
    emulator = TestEmulatorQueue._queue.reserve(timeout=5*30)

    def finalizer():
        TestEmulatorQueue._queue.relinquish(emulator)

    request.addfinalizer(finalizer)
    return emulator

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
    test_app = TestEmulatorQueue.test_app()
    if test_app is None:
        raise Exception("Failed to build test app")
    return test_app


@pytest.fixture(scope='session')
def support_app():
    support_app = TestEmulatorQueue.app()
    if isinstance(support_app, Exception) or support_app is None:
        raise Exception("Failed to build support app")
    return support_app


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
