import logging
import os
# TODO: CAUTION: WE CANNOT USE asyncio.subprocess as we executein in a thread other than made and on unix-like systems, there
# is bug in Python 3.7.
import subprocess
import sys
from contextlib import suppress
from queue import Queue
from typing import Tuple

from apk_bitminer.parsing import AXMLParser

from androidtestorchestrator.application import Application

_BASE_DIR = os.path.join(os.path.dirname(__file__), "..", "..")
_SRC_BASE_DIR = os.path.join(os.path.dirname(__file__), "..", )

TEST_SUPPORT_APP_DIR = os.path.join(_BASE_DIR, "testsupportapps")

RESOURCES_DIR = os.path.join(_SRC_BASE_DIR, "src", "androidtestorchestrator", "resources")
SETUP_PATH = os.path.join(_SRC_BASE_DIR, "setup.py")


log = logging.getLogger(__name__)


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


def gradle_build(*target_and_q: Tuple[str, Queue]):
    assert target_and_q, "empty target specified"
    targets = [t for t, _ in target_and_q]
    try:
        apk_path = None
        gradle_path = os.path.join("gradlew")
        if sys.platform == 'win32':
            cmd = [gradle_path+".bat"] + targets
            shell = True
        else:
            cmd = [os.path.join(".", gradle_path)] + targets
            shell = False
        log.info(f"Launching: {cmd} from {TEST_SUPPORT_APP_DIR}")
        process = subprocess.run(cmd,
                                 cwd=TEST_SUPPORT_APP_DIR,
                                 env=os.environ.copy(),
                                 stdout=sys.stdout,
                                 stderr=sys.stderr,
                                 shell=shell)
        if process.returncode != 0:
            raise Exception(f"Failed to build apk: {cmd}")
        for target, q in target_and_q:
            if target.endswith("assembleAndroidTest"):
                apk_path = os.path.join(TEST_SUPPORT_APP_DIR, "app", "build", "outputs", "apk", "androidTest", "debug", "app-debug-androidTest.apk")
            else:  # assembleDebug
                apk_path = os.path.join(TEST_SUPPORT_APP_DIR, "app", "build", "outputs", "apk", "debug", "app-debug.apk")
            if not os.path.exists(apk_path):
                raise Exception("Failed to find built apk %s" % apk_path)
            q.put(apk_path)
    except Exception:
        for _, q in target_and_q:
            q.put(None)
        # harsh exit to prevent tests from attempting to run that require apk that wasn't built
        raise
    else:
        log.info(f"Built {apk_path}")


def compile_all() -> Tuple[str, str]:
    """
    compile support app and test app in the background and return the queues where they will be placed

    :return: tuple of queues that will hold the apps once built
    """
    support_app_q = Queue()
    support_test_app_q = Queue()
    gradle_build(("assembleAndroidTest", support_test_app_q),
                 ("assembleDebug", support_app_q)
                 )
    return support_app_q, support_test_app_q


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
    sdkmanager_path = os.path.join(android_sdk, "tools", "bin", "sdkmanager")
    avdmanager_path = os.path.join(android_sdk, "tools", "bin", "avdmanager")
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
        image = "system-images;android-28;default;x86_64"
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
