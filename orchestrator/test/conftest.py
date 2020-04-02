import asyncio
import getpass
import os
import pytest_mproc
from pathlib import Path

import pytest
from typing import Optional, Union

from androidtestorchestrator.application import Application, TestApplication, ServiceApplication
from androidtestorchestrator.device import Device
from androidtestorchestrator.emulators import EmulatorQueue, EmulatorBundleConfiguration, Emulator
from . import support
from .support import uninstall_apk

TAG_MTO_DEVICE_ID = "MTO_DEVICE_ID"
IS_CIRCLECI = getpass.getuser() == 'circleci'


if IS_CIRCLECI:
    print(">>>> Running in Circleci environment.  Not using parallelized testing")
else:
    print(">>>> Parallelized testing is enabled for this run.")

# Run a bunch of stuff in the background, such as compiling depenent apks for test and launching emulators
# This allows tests to potentially run in parallel (if not dependent on output of these tasks), parallelizes
# these dependent tasks. The tasks populate results out to Queue's that test fixtures then use as needed
# (hence once a test needs that fixture, it would block until the dependent task(s) are complete, but only then)


def _start_queue() -> Union[Emulator, EmulatorQueue]:
    AVD = "MTO_emulator"
    CONFIG = EmulatorBundleConfiguration(
        sdk=Path(support.find_sdk()),
        boot_timeout=10 * 60  # seconds
    )
    ARGS = [
        "-no-window",
        "-no-audio",
        "-wipe-data",
        "-gpu", "off",
        "-no-boot-anim",
        "-skin", "320x640",
        "-partition-size", "1024"
    ]
    support.ensure_avd(str(CONFIG.sdk), AVD)
    if IS_CIRCLECI or TAG_MTO_DEVICE_ID in os.environ:
        ARGS.append("-no-accel")
        emulator = asyncio.get_event_loop().run_until_complete(Emulator.launch(Emulator.PORTS[0], AVD, CONFIG, *ARGS))
        return emulator
    count = int(os.environ.get("MTO_EMULATOR_COUNT", "4"))
    queue = EmulatorQueue.start(count, AVD, CONFIG, *ARGS)
    return queue


@pytest_mproc.utils.global_session_context("device")  # only use if device fixture is needed
class TestEmulatorQueue:
    _queue: Union[Emulator, EmulatorQueue] = _start_queue()
    _app_queue, _test_app_queue = support.compile_all()
    _app: Optional[str] = None
    _test_app: Optional[str] = None

    def __enter__(self):
        if isinstance(self._queue, EmulatorQueue):
            self._queue.__enter__()

    def __exit__(self, exc_type, exc_val, exc_tb):
        if isinstance(self._queue, EmulatorQueue):
            self._queue.__exit__(exc_type, exc_val, exc_tb)
        else:
            self._queue.kill()

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
    if isinstance(TestEmulatorQueue._queue, Emulator):
        emulator = TestEmulatorQueue._queue  # queue of 1 == an emulator
        assert emulator.get_state() == 'device'
        return emulator
    else:
        queue = TestEmulatorQueue._queue
        emulator = queue.reserve(timeout=10*60)

        def finalizer():
            queue.relinquish(emulator)

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
    os.chdir(str(tmp_path))
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
