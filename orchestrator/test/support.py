import multiprocessing
import os
import subprocess
import sys
import time
from multiprocessing import Queue
from typing import List

_BASE_DIR = os.path.join(os.path.dirname(__file__), "..", "..")

TEST_SUPPORT_APP_DIR = os.path.join(_BASE_DIR, "testsupportapps", "TestButlerTestApp")
BUTLER_SERVICE_SRC_DIR = os.path.join(_BASE_DIR, "testbutlerservice")

_SRC_BASE_DIR = os.path.join(os.path.dirname(__file__), "..", )

RESOURCES_DIR = os.path.join(_SRC_BASE_DIR, "src", "androidtestorchestrator", "resources")
SETUP_PATH = os.path.join(_SRC_BASE_DIR, "setup.py")

support_app_q = multiprocessing.Queue()
support_test_app_q = multiprocessing.Queue()
test_butler_app_q = multiprocessing.Queue()

emulator_port_pool_q = multiprocessing.Queue()


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

    cmd = [emulator_path, "-port", str(port), "@%s" % avd]
    if is_retry:
        cmd.append("-no-snapshot-load")
    proc = subprocess.Popen(cmd, stderr=sys.stderr, stdout=sys.stdout)
    time.sleep(3)
    getprop_cmd = [adb_path, "-s", device_id, "shell", "getprop", "sys.boot_completed"]
    tries = 60
    cycles = 2
    while tries > 0:
        completed = subprocess.run(getprop_cmd, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, encoding='utf-8', timeout=5)
        if completed.returncode != 0:
            if 60 - tries > 5*cycles:
                # kill and try again
                proc.kill()
                proc = subprocess.Popen(cmd, stderr=sys.stderr, stdout=sys.stdout)
            elif proc.poll() is not None:
                raise Exception("Failed to start emulator")
            time.sleep(3)
            cycles += 1
            continue
        if completed.stdout.strip() == '1':  # boot complete
            time.sleep(3)
            break
        time.sleep(3)
        tries -= 1
    if tries <= 0:
        proc.kill()
        raise Exception("Emulator failed to boot in time")

    Config.proc_q.put(proc)


def launch(ports: List[int], avds: List[str], count: int, adb_path: str, emulator_path: str):
    if len(avds) < count:
        raise Exception("Not enough avds defined to support %d processor cores" % count)
    for index, port in enumerate(ports[:count]):
        for retry in (False, True):
            try:
                wait_for_emulator_boot(port, avds[index], adb_path, emulator_path, retry)
                emulator_port_pool_q.put(port)
                break
            except Exception:
                if retry:
                    emulator_port_pool_q.put(None)


def launch_emulators(count):
    """
    Launch a set of emulators, waiting until boot complete on each one.  As each boot is
    achieved, the emaultor proc queue is populated (and return through fixture to awaiting tests)

    :param count: number to launch, usually number of processes test is running on

    :return: dictionary of port: multiprocessing.Process of launched emulator processes
    """
    ports = list(range(5554, 5682, 2))
    if count > len(ports):
        for _ in range(count):
            emulator_port_pool_q.put(None)
        raise ValueError("Max number of cores allowed is %d" % len(ports))
    android_sdk = find_sdk()
    adb_path = os.path.join(android_sdk, "platform-tools", add_ext("adb"))

    if sys.platform == 'win32':
        emulator_path = os.path.join(android_sdk, "emulator", "emulator.exe")
    else:
        emulator_path = os.path.join(android_sdk, "tools", "emulator")
    if not os.path.isfile(emulator_path):
        for _ in range(count):
            emulator_port_pool_q.put(None)
        raise Exception("Unable to find path to 'emulator' command")
    list_emulators_cmd = [emulator_path, "-list-avds"]
    completed = subprocess.run(list_emulators_cmd, timeout=10, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               encoding='utf-8')
    if completed.returncode != 0:
        raise Exception("Command '%s -list-avds' failed with code %d" % (emulator_path, completed.returncode))
    if not completed.stdout:
        raise Exception("No AVDs found to launch emulator as returned by '%s -list-avds" % emulator_path)
    avds = completed.stdout.splitlines()
    completed = subprocess.run([adb_path, "devices"], stdout=subprocess.PIPE, encoding='utf-8')
    for line in completed.stdout.splitlines():
        if not line or "List of" in line:
            continue
        device_id, status = line.split('\t', 1)
        if 'device' in status and "emulator" in device_id:
            port = int(device_id.split('-')[-1])
            for _ in range(count):
                emulator_port_pool_q.put(port)
            ports.remove(port)
            count -= 1

    proc = multiprocessing.Process(target=launch, args=(ports, avds, count, adb_path, emulator_path))
    proc.start()


def apk(dir: str, count: int, q: multiprocessing.Queue, target: str = "assembleDebug"):
    try:
        if sys.platform == 'win32':
            cmd = ["gradlew", target]
            shell = True
        else:
            cmd = ["./gradlew", target]
            shell = False
        completed = subprocess.run(cmd,
                                   cwd=dir,
                                   env=os.environ.copy(),
                                   stdout=sys.stdout,
                                   stderr=sys.stderr,
                                   shell=shell)
        if completed.returncode != 0:
            raise Exception("Failed to build apk:\n%s" %  cmd)
        if target == "assembleAndroidTest":
            suffix = "androidTest"
            suffix2 = f"-{suffix}"
        else:
            suffix = "."
            suffix2 = ""
        apk_path = os.path.join(dir, "app", "build", "outputs", "apk", suffix,
                                "debug", f"app-debug{suffix2}.apk")
        if not os.path.exists(apk_path):
            raise Exception("Failed to find built apk %s" % apk_path)
        for _ in range(count):
            q.put(apk_path)
    except Exception as e:
        for _ in range(count):
            q.put(None)
        raise


def compile_support_test_app(count):
    """
    Compile a test app and make the resulting apk available to all awaiting test Processes
    :param count: number of test processes needing a support test app
    """
    proc = multiprocessing.Process(target=apk, args=(TEST_SUPPORT_APP_DIR, count, support_test_app_q, "assembleAndroidTest"))
    proc.start()


def compile_support_app(count):
    proc = multiprocessing.Process(target=apk, args=(TEST_SUPPORT_APP_DIR, count, support_app_q))
    proc.start()


def compile_test_butler_app(count):
    proc = multiprocessing.Process(target=apk, args=(BUTLER_SERVICE_SRC_DIR, count, test_butler_app_q))
    proc.start()


find_sdk()

