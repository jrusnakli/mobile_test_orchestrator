import multiprocessing

import asyncio
import getpass
import os
from pathlib import Path

import pytest
from pytest_mproc.plugin import TmpDirFactory
from typing import Optional

from mobiletestorchestrator.application import Application, TestApplication, ServiceApplication
from mobiletestorchestrator.device import Device
from mobiletestorchestrator.emulators import EmulatorBundleConfiguration, Emulator
from . import support
from .support import uninstall_apk, find_sdk

TAG_MTO_DEVICE_ID = "MTO_DEVICE_ID"
IS_CIRCLECI = getpass.getuser() == 'circleci' or "CIRCLECI" in os.environ
Device.TIMEOUT_LONG_ADB_CMD = 10*60  # circleci may need more time

if IS_CIRCLECI:
    print(">>>> Running in Circleci environment.  Not using parallelized testing")
else:
    print(">>>> Parallelized testing is enabled for this run.")

# Run a bunch of stuff in the background, such as compiling depenent apks for test and launching emulators
# This allows tests to potentially run in parallel (if not dependent on output of these tasks), parallelizes
# these dependent tasks. The tasks populate results out to Queue's that test fixtures then use as needed
# (hence once a test needs that fixture, it would block until the dependent task(s) are complete, but only then)


class TestAppManager:
    _app_queue, _test_app_queue = support.compile_all()
    # place to cache the app and test app once they are gotten from the Queue
    _app: Optional[str] = None
    _test_app: Optional[str] = None

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


@pytest.fixture(scope='node')
def device_queue():
    m = multiprocessing.Manager()
    AVD = "MTO_emulator"
    if IS_CIRCLECI:
        CONFIG = EmulatorBundleConfiguration(
            sdk=Path(support.find_sdk()),
            avd_dir=Path("/home/circleci/avd"),
            boot_timeout=10 * 60  # seconds
        )
    else:
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
        "-partition-size", "1024",
    ]
    support.ensure_avd(str(CONFIG.sdk), AVD)
    if TAG_MTO_DEVICE_ID in os.environ:
        queue = m.Queue(1)
        queue.put(Device(TAG_MTO_DEVICE_ID, adb_path=find_sdk()))
    else:
        count = 1
        if IS_CIRCLECI:
            Device.TIMEOUT_ADB_CMD *= 10  # slow machine
            # ARGS.append("-no-accel")
            # on circleci, do build first to not take up too much
            # memory if emulator were started first
            count = 1
        elif "MTO_EMULATOR_COUNT" in os.environ:
            max_count = min(multiprocessing.cpu_count(), 6)
            count = int(os.environ.get("MTO_EMULATOR_COUNT", f"{max_count}"))

        queue = m.Queue(count)

        # launch emulators in parallel and wait for all to boot:
        async def launch(index: int):
            if index:
                await asyncio.sleep(index*2)  # stabilizes the launches spacing them out (otherwise, intermittend fail to boot)
            return await Emulator.launch(Emulator.PORTS[index], AVD, CONFIG,*ARGS)

        ems = asyncio.get_event_loop().run_until_complete(
            asyncio.gather(*[launch(index) for index in range(count)]))
        for em in ems:
            queue.put(em)
    try:
        yield queue
    finally:
        for em in ems:
            em.kill()


@pytest.fixture()
def device(device_queue: multiprocessing.Queue) -> Device:
    emulator = device_queue.get(timeout=10*60)
    try:
        yield emulator
    finally:
        device_queue.put(emulator)
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


@pytest.fixture(scope='node')
async def device_pool():
    m = multiprocessing.Manager()
    queue = AsyncQueueAdapter(q=m.Queue())
    print(f">>>>>>> CREATING EMULATOR POOL of {DeviceManager.count()} emulators\n     {DeviceManager.ARGS}")
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
        self._m = multiprocessing.Manager()
        self._app_queue = self._m.Queue(1)
        self._test_app_queue = self._m.Queue(1)
        self._service_app_queue = self._m.Queue(1)
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

    def service_app(self):
        """
        :return: the string path to the service apk that was compiled
        """
        if self._service_app is None:
            self._service_app = self._service_app_queue.get()
        return self._service_app

    def app(self):
        """
        :return: the string path to the target apk that was compiled
        """
        if self._app is None:
            self._app = self._app_queue.get()
        return self._app

    _singleton: Optional["AppManager"] = None

    @staticmethod
    def singleton():
        if AppManager._singleton is None:
            AppManager._singleton = AppManager()
        return AppManager._singleton


@pytest.fixture(scope='node')
async def device_pool():
    if IS_CIRCLECI:
        AppManager.singleton()  # force build to happen fist, in serial
    m = multiprocessing.Manager()
    queue = AsyncQueueAdapter(q=m.Queue(DeviceManager.count()))
    if IS_CIRCLECI:
        DeviceManager.ARGS.append("-no-accel")
        DeviceManager.ARGS.append("-no-snapshot")
        DeviceManager.ARGS.append("-no-snapshot-save")
    async with AsyncEmulatorPool.create(DeviceManager.count(),
                                        DeviceManager.AVD,
                                        DeviceManager.CONFIG,
                                        *DeviceManager.ARGS,
                                        external_queue=queue) as pool:
        yield pool


@pytest.fixture()
async def devices(device_pool: AsyncEmulatorPool, app_manager: AppManager, event_loop):
    # convert queue to an async queue.  We specifially want to test with AsyncEmulatorPool,
    # so will not ust the AsynQueueAdapter class.
    count = min(DeviceManager.count(), 2)
    async with device_pool.reserve_many(count) as devs:
        for dev in devs:
            uninstall_apk(app_manager.app(), dev)
            uninstall_apk(app_manager.test_app(), dev)
            uninstall_apk(app_manager.service_app(), dev)
        yield devs


@pytest.fixture()
async def device(device_pool: AsyncEmulatorPool, app_manager):
    """
    return a single reserved device
    """
    async with device_pool.reserve() as device:
        uninstall_apk(app_manager.app(), device)
        uninstall_apk(app_manager.test_app(), device)
        uninstall_apk(app_manager.service_app(), device)
        yield device


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
    try:
        yield app_for_test
    finally:
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

    def fin():
        """
        Leave the campground as clean as you found it:
        """
        app_for_test.uninstall()
        support_app.uninstall()
    request.addfinalizer(fin)
    return app_for_test



@pytest.fixture()
async def android_service_app(device, support_app: str):
    # the support app is created to act as a service app as well
    uninstall_apk(support_app, device)
    service_app = ServiceApplication.from_apk(support_app, device)
    try:
        yield service_app
    finally:
        service_app.uninstall()

@pytest.fixture(scope='global')
def apps():
    with AppManager() as app_manager:
        app = app_manager.app()
        test_app = app_manager.test_app()
        yield app, test_app


@pytest.fixture(scope='session')
def support_test_app():
    test_app = TestAppManager.test_app()
    if test_app is None:
        raise Exception("Failed to build test app")
    return test_app


@pytest.fixture(scope='session')
def support_app():
    support_app = TestAppManager.app()
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



emulator_lock = threading.Semaphore(1)


@pytest.fixture
def fake_sdk(mp_tmp_dir_factory: TmpDirFactory):
    tmpdir = mp_tmp_dir_factory.create_tmp_dir("sdk")
@pytest.fixture
def fake_sdk(tmpdir_factory):
    tmpdir = tmpdir_factory.mktemp("sdk")
    os.makedirs(os.path.join(str(tmpdir), "platform-tools"))
    with open(os.path.join(str(tmpdir), "platform-tools", "adb"), 'w'):
        pass  # create a dummy file so that test of its existence as file passes
    return str(tmpdir)


@pytest.fixture
def in_tmp_dir(mp_tmp_dir) -> Path:
    cwd = os.getcwd()
    os.chdir(str(mp_tmp_dir))
    yield Path(str(mp_tmp_dir))
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
