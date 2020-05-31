import _queue
import asyncio
import concurrent.futures
import multiprocessing

import getpass
import os
import shutil
import tempfile

from pathlib import Path
from queue import Queue

import pytest
from typing import Optional

from androidtestorchestrator.application import Application, TestApplication, ServiceApplication
from androidtestorchestrator.device import Device
from androidtestorchestrator.emulators import EmulatorBundleConfiguration
from androidtestorchestrator.devicepool import AsyncEmulatorPool, AsyncQueueAdapter
from androidtestorchestrator.tooling.sdkmanager import SdkManager
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
if IS_CIRCLECI:
    Device.TIMEOUT_ADB_CMD *= 10  # slow machine
    Device.TIMEOUT_LONG_ADB_CMD = 10*60  # circleci may need more time


class DeviceManager:

    AVD = "MTO_test_emulator"
    TMP_DIR = str(tempfile.mkdtemp(suffix="-ANDROID"))
    TMP_SDK_DIR = os.path.join(TMP_DIR, "SDK")
    os.mkdir(TMP_SDK_DIR)
    TMP_AVD_DIR = os.path.join(TMP_DIR, "AVD")
    os.mkdir(TMP_AVD_DIR)
    AVD_PATH = os.environ.get("ANDROID_AVD_HOME", TMP_AVD_DIR)
    SDK_PATH = os.environ.get("ANDROID_SDK_ROOT", TMP_SDK_DIR)
    CONFIG = EmulatorBundleConfiguration(
        sdk=Path(SDK_PATH),
        avd_dir=Path(AVD_PATH),
        boot_timeout=10 * 60  # seconds
    )
    ARGS = [
        "-no-window",
        "-no-audio",
        # "-wipe-data",
        "-gpu", "off",
        "-no-boot-anim",
        "-skin", "320x640",
        "-partition-size", "1024"
    ]

    _process: multiprocessing.Process = None

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


#################
# App-related fixtures;  TODO: logic could be cleaned up overall here
#################


class AppManager:
    """
    For managing compilation of apps used as test resources and providing them through fixtures
    """

    _proc, _app_queue, _test_app_queue, _service_app_queue = None, None, None, None

    def __init__(self):
        self._m = multiprocessing.Manager()
        self._app_queue = self._m.Queue(1)
        self._test_app_queue = self._m.Queue(1)
        self._service_app_queue = self._m.Queue(1)
        self._app = None
        self._test_app = None
        self._service_app = None
        AppManager._proc = support.compile_all(self._app_queue, self._test_app_queue,
                                               self._service_app_queue, wait=IS_CIRCLECI)

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
def device_pool():
    sdk_manager = SdkManager(DeviceManager.CONFIG.sdk, bootstrap=IS_CIRCLECI)
    if not "ANDROID_SDK_ROOT" in os.environ:
        print(">>> Bootstrapping Android SDK platform tools...")
        sdk_manager.bootstrap_platform_tools()
        print(">>> Bootstrapping Android SDK emulator...")
        sdk_manager.bootstrap_emulator()
    os.environ["ANDROID_SDK_ROOT"] = str(DeviceManager.CONFIG.sdk)
    os.environ["ANDROID_HOME"] = str(DeviceManager.CONFIG.sdk)
    if IS_CIRCLECI:
        AppManager.singleton()  # force build to happen fist, in serial
    print(">>> Creating Android emulator AVD...")
    image = "android-28;default;x86_64"
    sdk_manager.create_avd(DeviceManager.CONFIG.avd_dir, DeviceManager.AVD, image,
                           "pixel_xl", "--force")
    m = multiprocessing.Manager()
    queue = m.Queue(DeviceManager.count())
    pool_q = m.Queue(2)
    done_q = m.Queue(2)

    def em_pool():
        asyncio.run(create_device_pool(DeviceManager.CONFIG, DeviceManager.AVD, queue, pool_q, done_q,
                                       *DeviceManager.ARGS))

    p = multiprocessing.Process(target=em_pool)
    try:
        p.start()
        print(">>>>> WAITING FOR POOL")
        if pool_q.get() is not True:
            print(">>>> ERROR GETTING POOL")
            raise Exception("Error creating emulator pool")
        print(">>>> POOL CREATED")
        yield AsyncEmulatorPool(AsyncQueueAdapter(queue))
        done_q.put(True)
    finally:
        done_q.put(False)


async def create_device_pool(config: EmulatorBundleConfiguration,
                             avd: str,
                             queue: multiprocessing.Queue,
                             pool_q: multiprocessing.Queue, done_q: multiprocessing.Queue,
                             *config_args: str):
    queue = AsyncQueueAdapter(queue)
    os.environ["ANDROID_SDK_ROOT"] = str(config.sdk)
    os.environ["ANDROID_HOME"] = str(config.sdk)
    try:
        if IS_CIRCLECI:
            config_args = list(config_args) + ["-no-accel"]

        async with AsyncEmulatorPool.create(DeviceManager.count(),
                                            avd,
                                            config,
                                            *config_args,
                                            external_queue=queue) as pool:
            if IS_CIRCLECI:
                print(">>> No hw accel, so waiting for emulator to settle in...")
                await asyncio.sleep(30)
            pool_q.put(True)
            while True:
                # have to loop/poll to not block emaultor-launch asyncio tasks
                try:
                    done_q.get_nowait()
                    break
                except _queue.Empty:
                    await asyncio.sleep(1)
    finally:
        shutil.rmtree(DeviceManager.TMP_DIR)
        pool_q.put(False)


@pytest.fixture(scope='node')
def emulator_config() -> EmulatorBundleConfiguration:
    return DeviceManager.CONFIG


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
def app_manager():
    with AppManager.singleton() as app_manager:
        yield app_manager


@pytest.fixture(scope='session')
def support_app(app_manager: AppManager):
    return app_manager.app()


@pytest.fixture(scope='session')
def support_test_app(app_manager: AppManager):
    return app_manager.test_app()


@pytest.fixture(scope='session')
def support_service_app(app_manager: AppManager):
    return app_manager.service_app()


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
