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

#############
# Device related fixtures
############


class DeviceManager:

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

    _queue = multiprocessing.Queue()
    _process: multiprocessing.Process = None
    _reservation_gate = multiprocessing.Semaphore(1)

    def serve(self, count: int, queue: multiprocessing.Queue):
        """
        Launch the given number of emulators and make available through the provided queue.
        This method is started in a separate `multiprocessing.Process` so as not to block other activities.
        emulators become avilable in the queue as soon as each one is booted.

        :param count: Number of emulators to start
        :param queue: queue to hold the emulators
        """
        support.ensure_avd(str(self.CONFIG.sdk), self.AVD)
        if IS_CIRCLECI or TAG_MTO_DEVICE_ID in os.environ:
            self.ARGS.append("-no-accel")

        async def launch_one(index: int) -> Emulator:
            """
            Launch the nth (per index) emulator

            :returns: the fully booted emulator
            """
            if index:
                await asyncio.sleep(index*3)
            return await Emulator.launch(Emulator.PORTS[index], self.AVD, self.CONFIG, *self.ARGS)

        async def launch_emulators() -> None:
            """
            Launch the requested emulators, monitoring their boot status concurrently via asyncio
            """
            pending = [launch_one(index) for index in range(count)]
            while pending:
                completed, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in completed:
                    emulator = task.result()
                    queue.put(emulator)

        asyncio.get_event_loop().run_until_complete(asyncio.wait_for(launch_emulators(), timeout=5*60))

    def __enter__(self):
        assert DeviceManager._process is None, "DeviceManager is a singleton and should only be instantiated once"
        # at the class level as they shouldn't have to be pickled since we want to use this
        # object in a global multiprocess-safe fixture
        DeviceManager._process = multiprocessing.Process(target=self.serve, args=(self.count(), self._queue))
        DeviceManager._process.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            for _ in range(self.count()):
                em = self._queue.get(timeout=1)
                with suppress(Exception):
                    em.kill()
            DeviceManager._process.join(timeout=10)
        except Exception:
            print(">>> ERROR Stopping emulators.  Terminating process directly...")
            DeviceManager._process.terminate()

    @contextmanager
    def reserve(self, count: int = 1):
        """
        Reserve the given number of emulators, relinqushing them on exit of context manager back to the queue

        :param count: number of emulators to reserve
        :return: List of reserved emulators
        :raises: ValueError if the count is larger than the number of emulators available
        """
        # do not allow those reserving 1 to block those that first requested more than 1:
        if count > self.count():
            raise ValueError(f"Cannot resesrve more than the number of emulators launched ({count} > {self.count()}")
        self._reservation_gate.acquire()
        try:
            devs = [self._queue.get(timeout=5 * 60) for _ in range(count)]
        finally:
            self._reservation_gate.release()
        try:
            yield devs
        finally:
            for dev in devs:
                self._queue.put(dev)

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


@pytest.fixture(scope='global')
def device_manager():
    with DeviceManager() as device_manager:
        yield device_manager


@pytest.fixture()
@pytest.mark.asyncio
async def devices(device_manager):
    # convert queue to an async queue.  We specifially want to test with AsyncEmulatorQueue,
    # so will not ust the AsynQueueAdapter class.
    count = min(device_manager.count(), 2)
    with device_manager.reserve(count) as devs:
        async_q = asyncio.Queue()
        for dev in devs:
            await async_q.put(dev)
        yield AsyncEmulatorQueue(async_q)


@pytest.fixture
def device_list(device_manager):
    # return a list of at most 2 emulators (and only 1 if on a constrained system such as Circlci free)
    count = min(device_manager.count(), 2)
    with device_manager.reserve(count) as devs:
        yield devs


@pytest.fixture()
async def device(device_manager):
    """
    return a single reserved device
    """
    with device_manager.reserve() as devs:
        yield devs[0]


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
@pytest.mark.asyncio
def android_test_app(device,
                     support_app: str,
                     support_test_app: str):
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


@pytest.fixture(scope='global')
def apps():
    with AppManager() as app_manager:
        yield app_manager.app(), app_manager.test_app()


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
