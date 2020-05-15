import asyncio
import multiprocessing

import getpass
import os
import shutil
import socket
import tempfile
from contextlib import suppress, closing, contextmanager
from multiprocessing.managers import BaseManager

from pathlib import Path

import pytest
from typing import Optional, Tuple, List

from androidtestorchestrator.application import Application, TestApplication, ServiceApplication
from androidtestorchestrator.device import Device, DeviceLock
from androidtestorchestrator.emulators import EmulatorBundleConfiguration, Emulator
from androidtestorchestrator.devicepool import AsyncEmulatorPool, AsyncQueueAdapter
from . import support
from .support import uninstall_apk

TAG_MTO_DEVICE_ID = "MTO_DEVICE_ID"
IS_CIRCLECI = getpass.getuser() == 'circleci' or "CIRCLECI" in os.environ


if IS_CIRCLECI:
    print(">>>> Running in Circleci environment.  Not using parallelized testing")
else:
    print(">>>> Parallelized testing is enabled for this run.")

# Run a bunch of stuff in the background, such as compiling depenent apks for test and launching emulators
# This allows tests to potentially run in parallel (if not dependent on output of these tasks), parallelizes
# these dependent tasks. The tasks populate results out to Queue's that test fixtures then use as needed
# (hence once a test needs that fixture, it would block until the dependent task(s) are complete, but only then)

#############
# Device related fixtures
############


class DeviceManager():

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

    _process: multiprocessing.Process = None
    _reservation_gate = multiprocessing.Semaphore(1)

    @staticmethod
    def count():
        """
        :return: a max number of emulators that can be launched on the platform.  Users can set this via the
           MTO_MAX_EMULATORS environment variable, and must if their platform cannot support 4 simultaneous emulators
        """
        if IS_CIRCLECI or TAG_MTO_DEVICE_ID in os.environ:
            Device.TIMEOUT_ADB_CMD *= 10  # slow machine
            count = 1
        else:
            count = min(multiprocessing.cpu_count(), int(os.environ.get("MTO_MAX_EMULATORS", "4")))
        return count


@pytest.fixture(scope='node')
async def device_pool():
    m = multiprocessing.Manager()
    queue = AsyncQueueAdapter(q=m.Queue())
    async with AsyncEmulatorPool.create(DeviceManager.count(),
                                        DeviceManager.AVD,
                                        DeviceManager.CONFIG,
                                        *DeviceManager.ARGS,
                                        external_queue=queue) as pool:
        yield pool


@pytest.fixture()
async def devices(device_pool: AsyncEmulatorPool, apps, event_loop):
    # convert queue to an async queue.  We specifially want to test with AsyncEmulatorPool,
    # so will not ust the AsynQueueAdapter class.
    count = min(DeviceManager.count(), 2)
    async with device_pool.reserve_many(count) as devs:
        for dev in devs:
            uninstall_apk(apps[0], dev)
            uninstall_apk(apps[1], dev)
        yield devs


@pytest.fixture()
async def device(device_pool: AsyncEmulatorPool, apps):
    """
    return a single reserved device
    """
    async with device_pool.reserve() as device:
        uninstall_apk(apps[0], device)
        uninstall_apk(apps[1], device)
        yield device


#################
# App-related fixtures;  TODO: logic could be cleaned up overall here
#################


class AppManager:
    """
    For managing compilation of apps used as test resources and providing them through fixtures
    """

    _proc, _app_queue, _test_app_queue = None, None, None

    def __init__(self):
        AppManager._app_queue = multiprocessing.Queue(1)
        AppManager._test_app_queue = multiprocessing.Queue(1)
        self._app = None
        self._test_app = None
        AppManager._proc =support.compile_all(self._app_queue, self._test_app_queue)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self._proc.join(timeout=10)
        except TimeoutError:
            # shouldn't really get here as the process should legit end on its own(?)
            self._proc.terminate()

    def test_app(self):
        """
        :return: the string path to the test apk that was compiled
        """
        if self._test_app is None:
            self._test_app = self._test_app_queue.get()
        return self._test_app

    def app(self):
        """
        :return: the string path to the target apk that was compiled
        """
        if self._app is None:
            self._app = self._app_queue.get()
        return self._app


# noinspection PyShadowingNames
@pytest.fixture()
async def android_app(device: Device, support_app: str, event_loop):
    """
    :return: installed app
    """
    uninstall_apk(support_app, device)
    app = Application.from_apk(support_app, device)
    yield app
    """
    Leave the campground as clean as you found it:
    """
    app.uninstall()


# noinspection PyShadowingNames
@pytest.fixture()
async def android_test_app(device,
                           support_app: str,
                           support_test_app: str,
                           event_loop):
    """
    :return: installed test app
    """
    uninstall_apk(support_app, device)
    uninstall_apk(support_test_app, device)
    app_for_test = TestApplication.from_apk(support_test_app, device)
    support_app = Application.from_apk(support_app, device)
    yield app_for_test
    """
    Leave the campground as clean as you found it:
    """
    app_for_test.uninstall()
    support_app.uninstall()


# noinspection PyShadowingNames
@pytest.fixture()
async def android_test_app2(device2,
                            support_app: str,
                            support_test_app: str,
                            event_loop):
    uninstall_apk(support_app, device2)
    uninstall_apk(support_test_app, device2)
    app_for_test = TestApplication.from_apk(support_test_app, device2)
    support_app = Application.from_apk(support_app, device2)
    yield app_for_test
    """
    Leave the campground as clean as you found it:
    """
    app_for_test.uninstall()
    support_app.uninstall()


@pytest.fixture()
async def android_service_app(device, support_app: str):
    # the support app is created to act as a service app as well
    uninstall_apk(support_app, device)
    service_app = ServiceApplication.from_apk(support_app, device)
    try:
        yield service_app
    finally:
        service_app.uninstall()


@pytest.fixture(scope='node')
def apps():
    with AppManager() as app_manager:
        app = app_manager.app()
        test_app = app_manager.test_app()
        yield app, test_app


@pytest.fixture(scope='session')
def support_app(apps: Tuple[Application, TestApplication]):
    return apps[0]


@pytest.fixture(scope='session')
def support_test_app(apps: Tuple[Application, TestApplication]):
    return apps[1]


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

################
# Process-safe temp dir
###############


class TempDirFactory:
    """
    tmpdir is not process/thread safe when used in a multiprocessing environment.  Failures on setup can
    occur (even if infrequently) under certain rae conditoins.  This provides a safe mechanism for
    creating temporary directories utilizng s a global-scope fixture
    """

    class Manager(BaseManager):

        def __init__(self, tmp_root_dir: Optional[str] = None):

            def find_free_port():
                with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
                    s.bind(('', 0))
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    return s.getsockname()[1]

            address = ('127.0.0.1', find_free_port())
            if tmp_root_dir is not None:
                # server:
                TempDirFactory.Manager.register("tmp_root_dir", lambda: tmp_root_dir)
                super().__init__(address, b'pass')
                super().start()
            else:
                # client
                TempDirFactory.Manager.register("tmp_root_dir")
                super().__init__(address, b'pass')
                super().connect()

    def __init__(self):
        self._tmp_root_dir = tempfile.mkdtemp(f"pytest-{getpass.getuser()}")
        self._manager = TempDirFactory.Manager(self._tmp_root_dir)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._manager.shutdown()
        shutil.rmtree(self._tmp_root_dir)

    def root_tmp_dir(self):
        return self._tmp_root_dir


@pytest.fixture(scope='global')
def root_temp_dir():
    with TempDirFactory() as factory:
        yield factory.root_tmp_dir()


@pytest.fixture()
def temp_dir(root_temp_dir: str):
    # tmpdir is not thread safe and can fail on test setup when running on a highly loaded very parallelized system
    # so use this instead
    tmp_dir = tempfile.mkdtemp(dir=root_temp_dir)
    return tmp_dir
