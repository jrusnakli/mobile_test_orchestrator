import os
import shutil
import time
import subprocess
import sys
from androidtestorchestrator import TestListener
from androidtestorchestrator import AndroidTestOrchestrator, TestSuite
from androidtestorchestrator import TestApplication, Application, Device


class EspressoTestRunner(object):
    """
    Entry point for test application
    MTO project(androidtestorchestrator) pre-installed into lib
    """
    def __init__(self, app: str, test_app: str, device: str):
        self._app = app
        self._test_app = test_app
        self._device = device
        self._device = Device(device_id=device,
                              adb_path=os.path.join(find_sdk(), "platform-tools", add_ext("adb")))

    def start(self):
        # install test app
        test_application = TestApplication.from_apk(self._test_app, self._device, as_upgrade=True)
        # install app
        app = Application.from_apk(self._app, self._device, as_upgrade=True)

        # TODO: generate comprehensive report
        class Listener(TestListener):
            def test_ended(self, test_name: str, test_class: str, test_no: int, duration: float, msg: str = ""):
                print("Test %s passed" % test_name)

            def test_failed(self, test_name: str, test_class: str, test_no: int, stack: str, msg: str = ""):
                print("Test %s failed" % test_name)

            def test_ignored(self, test_name: str, test_class: str, test_no: int, msg: str = ""):
                print("Test %s skipped" % test_name)

            def test_assumption_violated(self, test_name: str, test_class: str, test_no: int, reason: str):
                print("Test %s skipped" % test_name)

            def test_suite_started(self, test_suite_name:str):
                print("Test execution started: " + test_suite_name)

            def test_suite_ended(self, test_suite_name: str, test_count: int, execution_time: float):
                print("Test execution ended: " + test_suite_name)

            def test_suite_errored(self, test_suite_name: str, status_code: int):
                print("Test execution of %s errored with status code: %d" % (test_suite_name, status_code))

        # log_path temporarily hardcoded for testing purpose.
        log_path = "log_test_result"
        if os.path.exists(log_path):
            shutil.rmtree(log_path)
            # define the access rights
        access_rights = 0o755
        try:
            os.mkdir(log_path, access_rights)
        except OSError:
            print("Creation of the directory %s failed" % log_path)

        with AndroidTestOrchestrator(log_path) as orchestrator:
            test_suite = TestSuite('test_suite', [])
            test_plan = iter([test_suite])
            orchestrator.execute_test_plan(test_application, test_plan, Listener())
            # orchestrator.execute_test_suite(test_suite, Listener())


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
    # cycles = 2
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

    # Config.proc_q.put(proc)


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


def main(argv):
    # pass args -- arg1: path of app,apk, arg2: path of test_app.apk, arg3: device (eg: "emulator-5554")
    launch_emulator(5554)
    app = argv[1]
    test_app = argv[2]
    device = argv[3]
    runner = EspressoTestRunner(app, test_app, device)
    runner.start()


if __name__ == "__main__":
    main(sys.argv)
