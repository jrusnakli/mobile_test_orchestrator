import asyncio
import multiprocessing

import getpass
import os
import shutil
import tempfile
from contextlib import suppress
from multiprocessing.managers import BaseManager
from threading import Semaphore

import pytest_mproc.utils
from pathlib import Path

import pytest
from typing import Optional, Tuple, List

from androidtestorchestrator.application import Application, TestApplication, ServiceApplication
from androidtestorchestrator.device import Device
from androidtestorchestrator.emulators import EmulatorBundleConfiguration, Emulator
from androidtestorchestrator.devicequeues import AsyncEmulatorQueue
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


def _start_queues() -> Tuple[str, str]:
    """
    start the emulator queue and the queues for the app/test app compiles running in parallel

    :return: Tuple of Emulator(if only one in queue)/EmulatorQueue and two string names of app & test_app apks
    TODO: just return Application and TestApplication and do the install here
    """
    app_queue, test_app_queue = support.compile_all()
    return app_queue, test_app_queue


@pytest_mproc.utils.global_session_context("devices", "device", "device_list")
class ParallelizedTestManager:

    _app_queue, _test_app_queue = _start_queues()
    # place to cache the app and test app once they are gotten from the Queue
    _app: Optional[str] = None
    _test_app: Optional[str] = None
    _queue = multiprocessing.Queue()

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

    def serve(self, count: int):
        support.ensure_avd(str(self.CONFIG.sdk), self.AVD)
        emulators: List[Emulator] = []
        if IS_CIRCLECI or TAG_MTO_DEVICE_ID in os.environ:
            self.ARGS.append("-no-accel")

        async def launch_one(index: int):
            if index:
                await asyncio.sleep(index*3)
            return await Emulator.launch(Emulator.PORTS[index], self.AVD, self.CONFIG, *self.ARGS)

        async def launch_emulators():
            pending = [launch_one(index) for index in range(count)]
            while pending:
                completed, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in completed:
                    emulator = task.result()
                    self._queue.put(emulator)
                    emulators.append(emulator)
            return emulators

        return asyncio.get_event_loop().run_until_complete(asyncio.wait_for(launch_emulators(), timeout=5*60))

    def __enter__(self):
        self._emulators = self.serve(self.count())
        return self

    def __exit__(self, *args, **kargs):
        for em in self._emulators:
            with suppress(Exception):
                em.kill()

    @staticmethod
    def count():
        if IS_CIRCLECI or TAG_MTO_DEVICE_ID in os.environ:
            Device.TIMEOUT_ADB_CMD *= 10  # slow machine
            count = 1
        else:
            max_count = min(multiprocessing.cpu_count(), 2)
            count = int(os.environ.get("MTO_EMULATOR_COUNT", f"{max_count}"))
        return count

    @classmethod
    def reserve(cls) -> Device:
        return cls._queue.get(timeout=2*60)

    @classmethod
    def relinquish(cls, device: Device):
        cls._queue.put(device, timeout=10)

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
@pytest.mark.asyncio
async def devices():
    # convert queue to an async queue
    count = min(ParallelizedTestManager.count(), 2)
    devs = [ParallelizedTestManager.reserve() for _ in range(count)]
    try:
        async_q = asyncio.Queue()
        for dev in devs:
            await async_q.put(dev)
        yield AsyncEmulatorQueue(async_q)
    finally:
        for dev in devs:
            ParallelizedTestManager.relinquish(dev)


@pytest.fixture
def device_list():
    count = min(ParallelizedTestManager.count(), 2)
    try:
        devs = [ParallelizedTestManager.reserve() for _ in range(count)]
        yield devs
    finally:
        for dev in devs:
            ParallelizedTestManager.relinquish(dev)


@pytest.fixture()
async def device():
    dev = ParallelizedTestManager.reserve()
    try:
        yield dev
    finally:
        ParallelizedTestManager.relinquish(dev)


# noinspection PyShadowingNames
@pytest.fixture()
@pytest.mark.asyncio
def android_test_app(device,
                     support_app: str,
                     support_test_app: str):
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
@pytest.mark.asyncio
def android_test_app2(device2,
                      support_app: str,
                      support_test_app: str):
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
@pytest.mark.asyncio
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
    test_app = ParallelizedTestManager.test_app()
    if test_app is None:
        raise Exception("Failed to build test app")
    return test_app


@pytest.fixture(scope='session')
def support_app():
    support_app = ParallelizedTestManager.app()
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


@pytest_mproc.utils.global_session_context("temp_dir")
class TempDir:

    class Manager(BaseManager):

        def __init__(self, tmp_root_dir: Optional[str] = None):
            address = ('127.0.0.1', 32453)
            if tmp_root_dir is not None:
                # server:
                TempDir.Manager.register("tmp_root_dir", lambda: tmp_root_dir)
                super().__init__(address, b'pass')
                super().start()
            else:
                # client
                TempDir.Manager.register("tmp_root_dir")
                super().__init__(address, b'pass')
                super().connect()

    _manager = None

    @staticmethod
    def manager(tmp_root_dir: Optional[str] = None):
        if not TempDir._manager:
            TempDir._manager = TempDir.Manager(tmp_root_dir)
        return TempDir._manager

    def __enter__(self):
        self._tmp_root_dir = tempfile.mkdtemp(f"pytest-{getpass.getuser()}")
        TempDir._manager = self.manager(self._tmp_root_dir)

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.manager().shutdown()
        shutil.rmtree(self._tmp_root_dir)


@pytest.fixture()
def temp_dir():
    # tmpdir is not thread safe and can fail on test setup when running on a highly loaded very parallelized system
    # so use this instead
    tmp_root = TempDir.manager().tmp_root_dir()
    tmp_dir = tempfile.mkdtemp(dir=tmp_root.strip())
    return tmp_dir
