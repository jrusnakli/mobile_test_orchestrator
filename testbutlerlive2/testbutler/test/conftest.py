import os
import shutil
import subprocess
import sys
import tempfile

import pytest

sys.path += [os.path.dirname(__file__), os.path.join(os.path.dirname(__file__), "..", "src")]

from testbutlerlive.device import Device  # noqa

TEST_SUPPORT_APP_DIR = os.path.join(os.path.dirname(__file__), "..", "..",
                                    "testsupportapps", "TestButlerTestApp")

BUTLER_SERVICE_SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "..",
                                      "testbutlerservice")

RESOURCES_DIR = os.path.join(os.path.dirname(__file__), "..", "src", "testbutlerlive", "resources")

SETUP_PATH = os.path.join(os.path.dirname(__file__), "..", "setup.py")


def add_ext(app):
    """
    if Windows, add ".exe" extension
    :param app: app path to add extension to
    :return: app with .exe extension if Windows, else app
    """
    if sys.platform == 'win32':
        return app + ".exe"
    return app


@pytest.fixture(scope='session')
def test_butler_test_app():
    """
    Compile app used to test the TestButler service
    :return: location to test butler test apk (test apk used to test the test butler service itself)
    """
    if sys.platform == 'win32':
        proc = subprocess.Popen(["gradlew", "assembleAndroidTest"], cwd=TEST_SUPPORT_APP_DIR, shell=True,
                                env=os.environ.copy())
    else:
        proc = subprocess.Popen(["./gradlew", "assembleAndroidTest"], cwd=TEST_SUPPORT_APP_DIR,
                                env=os.environ.copy())
    apk_path = None

    def path():
        nonlocal proc, apk_path
        if apk_path is not None:
            return apk_path
        proc.wait()
        if proc.returncode != 0:
            raise Exception("Failed to build test butler test app %s" % proc.stderr.read())
        apk_path = os.path.join(TEST_SUPPORT_APP_DIR, "app", "build", "outputs", "apk",
                                "app-debug-androidTest.apk")
        if not os.path.exists(apk_path):
            raise Exception("Failed to find built apk")
        return apk_path
    # a little quirky to pass back a function as a fixture,
    # but allows builds to happen in parallel with other fixtures
    return path


@pytest.fixture(scope='session')
def test_butler_app():
    """
    Compile app used to test the TestButler service
    NOTE: the test apk uses this app to interact with and test the TestButler service running on a device
    (i.e., there are two apps for testing: an actual app and a test apk that is run under adb instrument)

    :return: location to app used to invoke test butler service under test (its apk)
    """
    if sys.platform == 'win32':
        proc = subprocess.Popen(['gradlew', "assembleDebug"], cwd=TEST_SUPPORT_APP_DIR, shell=True,
                                env=os.environ.copy(),
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
    else:
        proc = subprocess.Popen(['./gradlew', "assembleDebug"],
                                cwd=TEST_SUPPORT_APP_DIR,
                                env=os.environ.copy(),
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)

    apk_path = None

    def path():
        nonlocal proc, apk_path
        if apk_path is not None:
            return apk_path
        proc.wait()
        if proc.returncode != 0:
            raise Exception("Failed to build test butler test app")
        apk_path = os.path.join(TEST_SUPPORT_APP_DIR, "app", "build", "outputs", "apk",
                                "app-debug.apk")
        if not os.path.exists(apk_path):
            raise Exception("Failed to find built apk")
        return apk_path
    return path


@pytest.fixture(scope='session')
def test_butler_service():
    """
    Compile test butler service apk
    :return: path to built test butler service apk
    """
    if not os.environ.get("ANDROID_SDK_ROOT"):
        if os.environ.get("ANDROID_SDK_ROOT"):
            print("Using %s as sdk root" % os.environ["ANDROID_SDK_ROOT"])
        else:
            if sys.platform == 'win32':
                android_sdk = os.path.join(os.path.expanduser("~"), "AppData", "Local", "Android", "Sdk")
            elif sys.platform == 'darwin':
                android_sdk = os.path.join(os.path.expanduser("~"), "Library", "Android", "Sdk")
            else:
                android_sdk = os.path.join(os.path.expanduser("~"), "Android", "Sdk")
            if not os.path.exists(android_sdk):
                raise Exception("Please set ANDROID_SDK_ROOT")
            os.environ["ANDROID_SDK_ROOT"] = android_sdk

    if sys.platform != 'win32':
        # For now,. windows requires user input for password, so this will hang
        # waiting for that input;  therefore, skip on Windows and build debug app later
        command = [sys.executable, "./setup.py"]
        proc = subprocess.Popen(command, stderr=subprocess.PIPE, stdout=subprocess.PIPE,
                                cwd=os.path.dirname(SETUP_PATH),
                                env=os.environ.copy())
        debug = False
    else:
        proc = subprocess.Popen(["tb_gradlew", "assembleDebug"],
                                cwd=BUTLER_SERVICE_SRC_DIR,
                                shell=True,
                                env=os.environ.copy(),
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        debug = True
    apk_path = None

    def path():
        nonlocal proc, apk_path, debug
        if apk_path is not None:
            return apk_path

        if proc is not None:
            proc.wait()
        if not debug and proc.returncode != 0:
            if sys.platform == 'win32':
                proc = subprocess.Popen(["tb_gradlew", "assembleDebug"],
                                        cwd=BUTLER_SERVICE_SRC_DIR,
                                        shell=True,
                                        stdin=subprocess.DEVNULL,
                                        env=os.environ.copy(),
                                        stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE)
            else:
                proc = subprocess.Popen(["./tb_gradlew", "assembleDebug"],
                                        cwd=BUTLER_SERVICE_SRC_DIR,
                                        stdin=subprocess.DEVNULL,
                                        env=os.environ.copy(),
                                        stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE)
            proc.wait()
            if proc.returncode != 0:
                raise Exception("Failed to build test butler service")
            apk_path = os.path.join(BUTLER_SERVICE_SRC_DIR, "app", "build", "outputs", "apk",
                                    "app-debug.apk")
        elif not debug:  # proc.returncode == 0
            apk_path = os.path.join(os.path.dirname(__file__), "..",
                                    "src", "testbutlerlive", "resources", "apks", "TestButlerLive.apk")
        else:  # debug is True
            apk_path = os.path.join(BUTLER_SERVICE_SRC_DIR, "app", "build", "outputs", "apk",
                                    "app-debug.apk")

        if not os.path.exists(apk_path):
            raise Exception("Failed to find built apk %s" % apk_path)
        return apk_path
    return path


# noinspection PyShadowingNames
@pytest.fixture(scope='session')
def emulator(request):
    import shutil
    import time

    emulator_path = shutil.which("emulator")
    home = os.path.expanduser("~")
    android_sdk = os.environ.get("ANDROID_HOME", os.path.join(home, "Android", "Sdk"))
    
    # Search for any existing devices, and just use that if so
    adb_path = add_ext(os.path.join(android_sdk, "platform-tools", "adb"))

    # noinspection PyShadowingNames
    def get_device():
        completed = subprocess.run([adb_path, "devices"], stdout=subprocess.PIPE, encoding='utf-8')
        for line in completed.stdout.splitlines():
            if not line or "List of" in line:
                continue
            device_id, status = line.split('\t', 1)
            if 'device' in status and 'emulator' in device_id:
                return device_id
        return None

    def stop_emulator():
        p.kill()
        p.wait()

    device_id = get_device()
    if device_id:
        return lambda: device_id

    # no device found, so launch emulator

    # determine path to emulator command:
    if not emulator_path:
        if not os.path.exists(android_sdk):
            raise Exception("Unable to find path to 'emulator' command")
        if sys.platform == 'win32':
            emulator_path = os.path.join(android_sdk, "emulator", "emulator.exe")
        else:
            emulator_path = os.path.join(android_sdk, "tools", "emulator")
        if not os.path.isfile(emulator_path):
            raise Exception("Unable to find path to 'emulator' command")

    # get a list of avds, and just use first one to launch emulator (if found)
    list_emulators_cmd = [emulator_path, "-list-avds"]
    completed = subprocess.run(list_emulators_cmd, timeout=10, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               encoding='utf-8')
    if completed.returncode != 0:
        raise Exception("Command '%s -list-avds' failed with code %d" % (emulator_path, completed.returncode))
    if not completed.stdout:
        raise Exception("No AVDs found to launch emulator as returned by '%s -list-avds" % emulator_path)
    avd = completed.stdout.splitlines()[0]

    # now launch emulator and wait for bootup
    launch_emulator_cmd = [emulator_path, "@%s" % avd, "-no-snapshot-load"]
    p = subprocess.Popen(launch_emulator_cmd, stdout=subprocess.PIPE, stdin=subprocess.PIPE)
    emulator = None

    # noinspection PyShadowingNames
    def launch():
        nonlocal emulator
        if emulator:
            return emulator
        tries = 10
        while tries > 0:
            if not get_device():
                if p.poll() is not None:
                    raise Exception("Command tod launch emulator failed:\n  %s" % launch_emulator_cmd)
                time.sleep(5)
                tries -= 1
            else:
                break
        if tries <= 0:
            stop_emulator()
            raise Exception("Emulator failed to launch or boot in time")

        device_id = get_device()
        getprop_cmd = [adb_path, "-s", device_id, "shell", "getprop", "sys.boot_completed"]
        tries = 60
        while tries > 0:
            completed = subprocess.run(getprop_cmd, stdout=subprocess.PIPE, encoding='utf-8')
            if completed.returncode != 0:
                stop_emulator()
                raise Exception("Cannot determine boot state of emulator")
            if completed.stdout.strip() == '1':  # boot complete
                time.sleep(3)
                break
            time.sleep(2)
            tries -= 1
        if tries <= 0:
            stop_emulator()
            raise Exception("Emulator failed to boot in time")
        emulator = get_device()
        return emulator

    request.addfinalizer(stop_emulator)

    return launch


# noinspection PyShadowingNames
@pytest.fixture(scope='session')
def adb(request, emulator):  # kicks off emulator launch
    tmpdir = tempfile.mkdtemp()

    def device_bridge():
        home = os.path.expanduser("~")
        if sys.platform == 'win32':
            default_sdk = os.path.join(home, "AppData", "Local", "Android", "Sdk")
        elif sys.platform == 'darwin':
            default_sdk = os.path.join(home, "Library", "Android", "sdk")
        else:
            default_sdk = os.path.join(home, "Android", "Sdk")
        return Device(adb_path=os.path.join(os.environ.get("ANDROID_SDK_ROOT") or default_sdk,
                                            "platform-tools", add_ext("adb")),
                      device_id=emulator())

    def fin():
        shutil.rmtree(tmpdir)

    request.addfinalizer(fin)
    return device_bridge
