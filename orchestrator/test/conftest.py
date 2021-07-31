import asyncio

import getpass
import os
import queue
import threading
import shutil
import tempfile
from pathlib import Path

import pytest

from typing import Optional

from mobiletestorchestrator.application import Application, TestApplication, ServiceApplication, AsyncApplication
from mobiletestorchestrator.device import Device
from mobiletestorchestrator.device_pool import AsyncQueueAdapter, AsyncEmulatorPool
from mobiletestorchestrator.emulators import EmulatorBundleConfiguration, Emulator
from mobiletestorchestrator.tooling.sdkmanager import SdkManager
from . import support
from .support import uninstall_apk, find_sdk

TAG_MTO_DEVICE_ID = "MTO_DEVICE_ID"
IS_CIRCLECI = getpass.getuser() == 'circleci' or "CIRCLECI" in os.environ
Device.TIMEOUT_LONG_ADB_CMD = 10*60  # circleci may need more time

if IS_CIRCLECI:
    Device.TIMEOUT_ADB_CMD *= 10  # slow machine
    Device.TIMEOUT_LONG_ADB_CMD = 10*60  # circleci may need more time
    print(">>>> Running in Circleci environment.  Not using parallelized testing")
else:
    print(">>>> Parallelized testing is enabled for this run.")

# Run a bunch of stuff in the background, such as compiling depenent apks for test and launching emulators
# This allows tests to potentially run in parallel (if not dependent on output of these tasks), parallelizes
# these dependent tasks. The tasks populate results out to Queue's that test fixtures then use as needed
# (hence once a test needs that fixture, it would block until the dependent task(s) are complete, but only then)


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
            count = 2  # min(multiprocessing.cpu_count(), int(os.environ.get("MTO_MAX_EMULATORS", "4")))
        return count


@pytest.fixture(scope='session')
def device_pool_q():
    try:
        sdk_manager = SdkManager(DeviceManager.CONFIG.sdk, bootstrap=bool(IS_CIRCLECI))
        if IS_CIRCLECI:
            print(">>> Bootstrapping Android SDK platform tools...")
            sdk_manager.bootstrap_platform_tools()
            sdk_manager.bootstrap_build_tools("28.0.3")
            assert os.path.exists("/opt/android/sdk/build-tools/28.0.3/aidl")
        os.environ["ANDROID_SDK_ROOT"] = str(DeviceManager.CONFIG.sdk)
        os.environ["ANDROID_HOME"] = str(DeviceManager.CONFIG.sdk)
        os.environ["ANDROID_AVD_HOME"] = str(DeviceManager.CONFIG.avd_dir)
        if IS_CIRCLECI:
            AppManager.singleton()  # force build to happen fist, in serial
        print(">>> Creating Android emulator AVD...")
        if IS_CIRCLECI:
            print(">>> Bootstrapping Android SDK emulator...")
            sdk_manager.bootstrap_emulator()
        image = "android-28;default;x86_64"
        sdk_manager.create_avd(DeviceManager.CONFIG.avd_dir, DeviceManager.AVD, image,
                               "pixel_xl", "--force")
        assert os.path.exists(DeviceManager.CONFIG.avd_dir.joinpath(DeviceManager.AVD).with_suffix(".ini"))
        assert os.path.exists(DeviceManager.CONFIG.avd_dir.joinpath(DeviceManager.AVD).with_suffix(".avd"))
        q = queue.Queue(DeviceManager.count())
        return q
    finally:
        shutil.rmtree(DeviceManager.TMP_DIR)


pool_of_pools_q = queue.Queue()
pool_sem = threading.Semaphore(0)


class Thread(threading.Thread):

    def __init__(self, q):
        super().__init__()
        self._q = q

    def run(self):
        asyncio.new_event_loop().run_until_complete(pool_helper(self._q))


@pytest.fixture(scope='session')
def device_pool(device_pool_q):
    try:
        Thread(device_pool_q).start()
        yield pool_of_pools_q.get()
    finally:
        pool_sem.release()


async def pool_helper(device_pool_q):
    config = DeviceManager.CONFIG
    config_args = DeviceManager.ARGS
    queue = AsyncQueueAdapter(device_pool_q)
    if IS_CIRCLECI:
        config_args = list(config_args) + ["-no-accel"]
    os.environ["ANDROID_SDK_ROOT"] = str(config.sdk)
    os.environ["ANDROID_HOME"] = str(config.sdk)
    os.environ["ANDROID_AVD_HOME"] = str(config.avd_dir)
    async with AsyncEmulatorPool.create(DeviceManager.count(),
                                        DeviceManager.AVD,
                                        config,
                                        *config_args,
                                        external_queue=queue,
                                        wait_for_startup=False) as pool:
        pool_of_pools_q.put(pool)
        await pool.wait_for_startup()
        while not pool_sem.acquire(blocking=False):
            await asyncio.sleep(1)


@pytest.fixture(scope='session')
def emulator_config() -> EmulatorBundleConfiguration:
    return DeviceManager.CONFIG


@pytest.fixture()
async def devices(device_pool: AsyncEmulatorPool, app_manager: "AppManager"):
    # convert queue to an async queue.  We specifially want to test with AsyncEmulatorPool,
    # so will not ust the AsynQueueAdapter class.
    count = min(DeviceManager.count(), 2)
    async with device_pool.reserve_many(count, timeout=100) as devs:
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
    async with device_pool.reserve(timeout=100) as device:
        uninstall_apk(app_manager.app(), device)
        uninstall_apk(app_manager.test_app(), device)
        uninstall_apk(app_manager.service_app(), device)
        yield device



#################
# App-related fixtures;  TODO: logic could be cleaned up overall here
#################


class AppManager:
    """
    For managing compilation of apps used as test resources and providing them through fixtures
    """


    def __init__(self):
        self._support_apk, self._support_test_apk, self._support_service_apk = support.compile_all()

    def test_app(self) -> str:
        """
        :return: the string path to the test apk that was compiled
        """
        return self._support_test_apk

    def service_app(self):
        """
        :return: the string path to the service apk that was compiled
        """
        return self._support_service_apk

    def app(self):
        """
        :return: the string path to the target apk that was compiled
        """
        return self._support_apk

    _singleton: Optional["AppManager"] = None

    @staticmethod
    def singleton():
        if AppManager._singleton is None:
            AppManager._singleton = AppManager()
        return AppManager._singleton


# noinspection PyShadowingNames
@pytest.fixture()
async def android_app(device: Device, support_app: str):
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
                           ):
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
                            ):
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


@pytest.fixture(scope='session')
def app_manager():
    yield AppManager.singleton()


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


@pytest.fixture()
async def install_app_async(device: Device):
    apps = []

    async def do_install(app_cls: AsyncApplication, package_name: str):
        uninstall_apk(package_name, device)
        app = await app_cls.from_apk(package_name, device)
        apps.append(app)
        return app

    yield do_install

    for app in apps:
        await app.uninstall()
