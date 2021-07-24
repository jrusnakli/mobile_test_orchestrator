import multiprocessing

import asyncio
import getpass
import os
from pathlib import Path
from queue import Queue

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

        queue = Queue(count)

        # launch emulators in parallel and wait for all to boot:
        async def launch(index: int):
            if index:
                await asyncio.sleep(index*2)  # stabilizes the launches spacing them out (otherwise, intermittent fail to boot)
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


# noinspection PyShadowingNames
@pytest.fixture()
def android_test_app(device,
                     support_app: str,
                     support_test_app: str):
    uninstall_apk(support_app, device)
    uninstall_apk(support_test_app, device)
    app_for_test = TestApplication.from_apk(support_test_app, device)
    support_app = Application.from_apk(support_app, device)
    try:
        yield app_for_test
    finally:
        app_for_test.uninstall()
        support_app.uninstall()


@pytest.fixture()
def android_service_app(device,
                        support_app: str):
    # the support app is created to act as a service app as well
    uninstall_apk(support_app, device)
    service_app = ServiceApplication.from_apk(support_app, device)
    try:
        yield service_app
    finally:
        service_app.uninstall()


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
    return TestAppManager.service_app()

@pytest.fixture
def fake_sdk(mp_tmp_dir_factory: TmpDirFactory):
    tmpdir = mp_tmp_dir_factory.create_tmp_dir("sdk")
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
