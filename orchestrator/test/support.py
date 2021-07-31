import logging
import multiprocessing
import os
# TODO: CAUTION: WE CANNOT USE asyncio.subprocess as we executein in a thread other than made and on unix-like systems, there
# is bug in Python 3.7.
import shutil
import subprocess
import sys
from contextlib import suppress
from queue import Queue
from typing import Tuple, Dict, List

from apk_bitminer.parsing import AXMLParser

from mobiletestorchestrator.application import Application

_BASE_DIR = os.path.join(os.path.dirname(__file__), "..", "..")
_SRC_BASE_DIR = os.path.join(os.path.dirname(__file__), "..", )

TEST_SUPPORT_APP_DIR = os.path.join(_BASE_DIR, "testsupportapps")
TEST_SERVICE_APP_DIR = os.path.join(_BASE_DIR, "testservice")

RESOURCES_DIR = os.path.join(_SRC_BASE_DIR, "src", "mobiletestorchestrator", "resources")
SETUP_PATH = os.path.join(_SRC_BASE_DIR, "setup.py")


log = logging.getLogger(__name__)


def add_ext(path: str):
    if sys.platform == 'win32':
        path += ".exe"
    return path


def find_sdk():
    """
    :return: android sdk location

    :rasise: Exception if sdk not found through environ vars or in standard user-home location per platform
    """
    if os.environ.get("ANDROID_HOME"):
        log.info("Please use ANDROID_SDK_ROOT over ANDROID_HOME")
        os.environ["ANDROID_SDK_ROOT"] = os.environ["ANDROID_HOME"]
        del os.environ["ANDROID_HOME"]
    if os.environ.get("ANDROID_SDK_ROOT"):
        os.environ["ANDROID_HOME"] = os.environ["ANDROID_SDK_ROOT"]  # some android tools still expecte this
        return os.environ["ANDROID_SDK_ROOT"]

    if sys.platform == 'win32':
        android_sdk = os.path.join(os.path.expanduser("~"), "AppData", "Local", "Android", "Sdk")
    elif sys.platform == 'darwin':
        android_sdk = os.path.join(os.path.expanduser("~"), "Library", "Android", "Sdk")
    else:
        android_sdk = os.path.join(os.path.expanduser("~"), "Android", "Sdk")
    if not os.path.exists(android_sdk):
        raise Exception("Please set ANDROID_SDK_ROOT")
    os.environ["ANDROID_SDK_ROOT"] = android_sdk
    os.environ["ANDROID_HOME"] = android_sdk  # some android tools still expecte this
    return android_sdk


def gradle_build(targets_and_q: Dict[str, List[Tuple[str, str]]]):
    find_sdk()  # sets env vars
    processes = []
    for root_build_dir, target_and_q in targets_and_q.items():
        targets = [t for t, _ in target_and_q]
        gradle_path = os.path.join("gradlew")
        if sys.platform == 'win32':
            cmd = [gradle_path+".bat"] + targets
            shell = True
        else:
            cmd = [os.path.join(".", gradle_path)] + targets
            shell = False
        log.info(f"Launching: {cmd} from {root_build_dir}")
        processes.append(subprocess.Popen(cmd,
                         cwd=root_build_dir,
                         env=os.environ.copy(),
                         stdout=sys.stdout,
                         stderr=sys.stderr,
                         shell=shell))
    for process in processes:
        process.wait()
        if process.returncode != 0:
            raise Exception("Failed to build at least one app")
    log.info(f"Built apks")


def compile_all() -> Tuple[str, str, str]:
    """
    compile support app and test app in the background and return the queues where they will be placed

    :return: tuple of queues that will hold the apps once built
    """
    gradle_build(
            {TEST_SUPPORT_APP_DIR: [
                ("assemble", None),
                ("assembleAndroidTest",  "support_app"),
                ("assembleDebug", "support_test_app"),
            ],
             TEST_SERVICE_APP_DIR: [
                ("assembleDebug",  "support_service_app")
            ],
            },
        )
    return os.path.join(TEST_SUPPORT_APP_DIR, "app", "build", "outputs", "apk", "debug", "app-debug.apk"), \
        os.path.join(TEST_SUPPORT_APP_DIR, "app", "build", "outputs", "apk", "androidTest", "debug",
                     "app-debug-androidTest.apk"), \
        os.path.join(TEST_SERVICE_APP_DIR, "app", "build", "outputs", "apk", "debug", "app-debug.apk")


def uninstall_apk(apk, device):
    """
    A little of a misnomer, as we don't actually uninstall an apk, however we can get the name of the
    package from the apk and ensure the app is not installed on the device (ensure "cleanliness" for testing)
    :param apk: apk to get package name from
    :param device: device to uninstall package from
    """
    with suppress(Exception):
        Application(AXMLParser.parse(apk).package_name, device).uninstall()


def ensure_avd(android_sdk: str, avd: str):
    adb_path = os.path.join(android_sdk, "platform-tools", "adb")
    if sys.platform.lower() == 'win32':
        adb_path += ".exe"

    is_no_window = False

    if sys.platform == 'win32':
        emulator_path = os.path.join(android_sdk, "emulator", "emulator-headless.exe")
    else:
        # latest Android SDK should use $SDK_ROOT/emulator/emulator instead of $SDK_ROOT/tools/emulator
        emulator_path = os.path.join(android_sdk, "emulator", "emulator-headless")
    sdkmanager_path = shutil.which("sdkmanager") or os.path.join(android_sdk, "tools", "bin", "sdkmanager")
    avdmanager_path = shutil.which("avdmanager") or os.path.join(android_sdk, "tools", "bin", "avdmanager")
    if sys.platform.lower() == 'win32':
        sdkmanager_path += ".bat"
        avdmanager_path += ".bat"
        shell = True
    else:
        shell = False
    if not os.path.isfile(emulator_path):
        # As of v29.2.11, emulator-headless is no longer present, but has been merged to emulator -no-window,
        # so check for newer command
        if sys.platform == 'win32':
            emulator_path = os.path.join(android_sdk, "emulator", "emulator.exe")
        else:
            emulator_path = os.path.join(android_sdk, "emulator", "emulator")
        if not os.path.isfile(emulator_path):
            raise Exception("Unable to find path to 'emulator' command")
        is_no_window = True
    list_emulators_cmd = [emulator_path, "-list-avds"]
    if is_no_window:
        list_emulators_cmd.append("-no-window")
    completed = subprocess.run(list_emulators_cmd, timeout=10, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               encoding='utf-8', shell=shell)
    if completed.returncode != 0:
        raise Exception("Command '%s -list-avds' failed with code %d" % (emulator_path, completed.returncode))
    if avd not in completed.stdout:
        image = "system-images;android-29;default;x86_64"
        download_emulator_cmd = [sdkmanager_path, image]
        p = subprocess.Popen(download_emulator_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        print(">>>> Downloading system image to create avd...(May take some time)")
        while p.returncode is None:
            bytes = p.stdout.read(100)
            if not bytes:
                break
            sys.stdout.write(bytes.decode('latin-1'))
        if p.wait() != 0:
            stdout, _ = p.communicate()
            raise Exception(f"Failed to download image for AVD {stdout}")
        print(">>>> Download complete.")
        create_avd_cmd = [avdmanager_path, "create", "avd", "-n", avd, "-k", image, "-d", "pixel_xl"]
        p = subprocess.Popen(create_avd_cmd,  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.PIPE)
        if p.wait() != 0:
            stdout, stderr = p.communicate()
            raise Exception(f"Failed to create avd: {stdout}\n{stderr}")
        completed = subprocess.run(list_emulators_cmd, timeout=10, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   encoding='utf-8')
        if completed.returncode != 0:
            raise Exception("Command '%s -list-avds' failed with code %d" % (emulator_path, completed.returncode))
        if avd not in completed.stdout:
            raise Exception("Unable to create AVD for testing")


find_sdk()
