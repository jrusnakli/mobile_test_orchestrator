import os
import time
# TODO: CAUTION: WE CANNOT USE asyncio.subprocess as we executein in a thread other than made and on unix-like systems, there
# is bug in Python 3.7.
import subprocess
import sys
from contextlib import suppress
from queue import Queue
from typing import Tuple

from apk_bitminer.parsing import AXMLParser

from androidtestorchestrator import Application

_BASE_DIR = os.path.join(os.path.dirname(__file__), "..", "..")

TEST_SUPPORT_APP_DIR = os.path.join(_BASE_DIR, "testsupportapps", "TestButlerTestApp")
BUTLER_SERVICE_SRC_DIR = os.path.join(_BASE_DIR, "testbutlerservice")

_SRC_BASE_DIR = os.path.join(os.path.dirname(__file__), "..", )

RESOURCES_DIR = os.path.join(_SRC_BASE_DIR, "src", "androidtestorchestrator", "resources")
SETUP_PATH = os.path.join(_SRC_BASE_DIR, "setup.py")

support_app_q = Queue()
support_test_app_q = Queue()
test_butler_app_q = Queue()


class Config:
    proc_q = Queue()

    @classmethod
    def procs(cls):
        while not cls.proc_q.empty():
            yield cls.proc_q.get()


def add_ext(app):
    """
    if Windows, add ".exe" extension
    :param app: app path to add extension to
    :return: app with .exe extension if Windows, else app
    """
    if sys.platform == 'win32':
        return app + ".exe"
    return app


def find_sdk():
    """
    :return: android sdk location

    :rasise: Exception if sdk not found through environ vars or in standard user-home location per platform
    """
    if os.environ.get("ANDROID_HOME"):
        print("Please use ANDROID_SDK_ROOT over ANDROID_HOME")
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


def wait_for_emulator_boot(port: int, avd: str, adb_path: str, emulator_path: str, is_retry: bool):
    device_id = "emulator-%d" % port

    # read more about cmd option https://developer.android.com/studio/run/emulator-commandline
    cmd = [emulator_path, "-port", str(port), "@%s" % avd, "-wipe-data"]
    if is_retry and "-no-snapshot-load" not in cmd:
        cmd.append("-no-snapshot-load")
    if os.environ.get("EMULATOR_OPTS"):
        cmd += os.environ["EMULATOR_OPTS"].split()
    proc = subprocess.Popen(cmd, stderr=sys.stderr, stdout=sys.stdout)
    time.sleep(3)
    getprop_cmd = [adb_path, "-s", device_id, "shell", "getprop", "sys.boot_completed"]
    tries = 100
    cycles = 2
    while tries > 0:
        if proc.poll() is not None:
            raise Exception("Failed to launch emulator")
        completed = subprocess.run(getprop_cmd, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, encoding='utf-8')
        if completed.returncode != 0:
            print(completed.stderr)
        elif completed.stdout.strip() == '1':  # boot complete
            time.sleep(3)
            break
        time.sleep(3)
        tries -= 1
        if tries == 0:
            proc.kill()
            raise Exception(f"Emulator failed to boot in time \n {completed.stderr}")

    Config.proc_q.put(proc)


def launch(port: int, avd: str, adb_path: str, emulator_path: str):
    for retry in (False, True):
        try:
            wait_for_emulator_boot(port, avd, adb_path, emulator_path, retry)
            break
        except Exception as e:
            if retry:
                raise e


def launch_emulator(port: int):
    """
    Launch a set of emulators, waiting until boot complete on each one.  As each boot is
    achieved, the emulator proc queue is populated (and return through fixture to awaiting tests)

    :param count: number to launch, usually number of processes test is running on

    :return: dictionary of port: multiprocessing.Process of launched emulator processes
    """
    EMULATOR_NAME = "MTO_emulator"
    android_sdk = find_sdk()
    adb_path = os.path.join(android_sdk, "platform-tools", add_ext("adb"))

    completed = subprocess.run([adb_path, "devices"], stdout=subprocess.PIPE, encoding='utf-8')
    if f"emulator-{port}" in completed.stdout:
        print(f"WARNING: using existing emulator at port {port}")
        return

    if sys.platform == 'win32':
        emulator_path = os.path.join(android_sdk, "emulator", "emulator.exe")
    else:
        # latest Android SDK should use $SDK_ROOT/emulator/emulator instead of $SDK_ROOT/tools/emulator
        emulator_path = os.path.join(android_sdk, "emulator", "emulator")
    sdkmanager_path = os.path.join(android_sdk, "tools", "bin", "sdkmanager")
    avdmanager_path = os.path.join(android_sdk, "tools", "bin", "avdmanager")
    if sys.platform.lower() == 'win32':
        sdkmanager_path += ".bat"
        avdmanager_path += ".bat"
        shell = True
    else:
        shell = False
    if not os.path.isfile(emulator_path):
        raise Exception("Unable to find path to 'emulator' command")
    list_emulators_cmd = [emulator_path, "-list-avds"]
    completed = subprocess.run(list_emulators_cmd, timeout=10, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               encoding='utf-8', shell=shell)
    if completed.returncode != 0:
        raise Exception("Command '%s -list-avds' failed with code %d" % (emulator_path, completed.returncode))
    if EMULATOR_NAME not in completed.stdout:
        download_emulator_cmd = [sdkmanager_path, "system-images;android-28;default;x86_64"]
        p = subprocess.Popen(download_emulator_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, stdin=subprocess.PIPE)
        p.stdin.write(b"Y\n")
        if p.wait() != 0:
            raise Exception("Failed to download image for AVD")
        create_avd_cmd = [avdmanager_path, "create", "avd", "-n", EMULATOR_NAME, "-k", "system-images;android-28;default;x86_64",
                          "-d", "pixel_xl"]
        p = subprocess.Popen(create_avd_cmd,  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.PIPE)
        if p.wait() != 0:
            stdout, stderr = p.communicate()
            raise Exception(f"Failed to create avd: {stdout}\n{stderr}")
        completed = subprocess.run(list_emulators_cmd, timeout=10, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   encoding='utf-8')
        if completed.returncode != 0:
            raise Exception("Command '%s -list-avds' failed with code %d" % (emulator_path, completed.returncode))
        if EMULATOR_NAME not in completed.stdout:
            raise Exception("Unable to create AVD for testing")
    avd = EMULATOR_NAME
    launch(port, avd, adb_path, emulator_path)


def gradle_build(*target_and_q: Tuple[str, Queue]):
    assert target_and_q, "empty target specified"
    targets = [t for t, _ in target_and_q]
    try:
        apk_path = None
        gradle_path = os.path.join("testbutlerservice", "gradlew")
        if sys.platform == 'win32':
            cmd = [gradle_path+".bat"] + targets
            shell = True
        else:
            cmd = [os.path.join(".", gradle_path)] + targets
            shell = False
        print(f"Launching: {cmd} from ../../{os.getcwd()}")
        root = os.path.join("..", "..")
        process = subprocess.run(cmd,
                                 cwd=root,
                                 env=os.environ.copy(),
                                 stdout=sys.stdout,
                                 stderr=sys.stderr,
                                 shell=shell)
        if process.returncode != 0:
            raise Exception(f"Failed to build apk: {cmd}")
        for target, q in target_and_q:
            if target.endswith("assembleAndroidTest"):
                suffix = "androidTest"
                suffix2 = f"-{suffix}"
            else:
                suffix = "."
                suffix2 = ""
            path_components = target.split(':')
            apk_path = os.path.join("..", "..", *path_components[:-1], "build", "outputs", "apk", suffix,
                                    "debug", f"app-debug{suffix2}.apk")
            if not os.path.exists(apk_path):
                raise Exception("Failed to find built apk %s" % apk_path)
            q.put(apk_path)
    except Exception as e:
        for _, q in target_and_q:
            q.put(None)
        raise
    else:
        print(f"Built {apk_path}")


def compile_all():
    gradle_build(("testsupportapps:TestButlerTestApp:app:assembleAndroidTest", support_test_app_q),
                 ("testsupportapps:TestButlerTestApp:app:assembleDebug", support_app_q),
                 ("testbutlerservice:app:assembleDebug", test_butler_app_q),
                )


def uninstall_apk(apk, device):
    """
    A little of a misnomer, as we don't actually uninstall an apk, however we can get the name of the
    package from the apk and ensure the app is not installed on the device (ensure "cleanliness" for testing)
    :param apk: apk to get package name from
    :param device: device to uninstall package from
    """
    with suppress(Exception):
        Application(AXMLParser.parse(apk).package_name, device).uninstall()



find_sdk()
