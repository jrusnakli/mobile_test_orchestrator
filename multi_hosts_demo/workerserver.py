import pickle
import sys
import configparser
from pathlib import Path
import os
import shutil
import time
import subprocess
from androidtestorchestrator import TestListener
from androidtestorchestrator import AndroidTestOrchestrator
from androidtestorchestrator import TestApplication, Application, Device
from multiprocessing.managers import BaseManager

PORT_NUM = 55000
AUTH_KEY = b'abcrde'
MASTER_VM = "52.247.223.117"


class EspressoTestRunner(object):
    """
        EspressoTestRunner executes a set of (or single) test suites, with each
        suite being a collection of (adb shell am) instrument commands to run.
        EspressoTestRunner interactions with device during execution of a test or suite of tests.
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

        class Listener(TestListener):

            def __init__(self, result: str):
                self._result = result

            def test_ended(self, test_name: str, test_class: str, test_no: int, duration: float, msg: str = ""):
                """
                Orchestrator execute test plans and calls test_ended,
                triggers remote result_queue to put test result, token qsize += 1
                """
                self._result = "Test %s passed" % test_name
                result_queue.put(self._result)
                token.put("")
                print(self._result)

            def test_failed(self, test_name: str, test_class: str, test_no: int, stack: str, msg: str = ""):
                """
                Orchestrator execute test plans and calls test_failed if test failed,
                triggers remote result_queue to put test result, token qsize += 1
                """
                self._result = "Test %s failed" % test_name
                result_queue.put(self._result)
                token.put("")
                print(self._result)

            def test_ignored(self, test_name: str, test_class: str, test_no: int, msg: str = ""):
                """
                Orchestrator execute test plans and calls test_ignored if test ignored,
                triggers remote result_queue to put "Test skipped", token qsize += 1
                """
                self._result = "Test %s skipped" % test_name
                result_queue.put(self._result)
                token.put("")
                print(self._result)

            def test_assumption_violated(self, test_name: str, test_class: str, test_no: int, reason: str):
                print("Test %s skipped" % test_name)

            def test_suite_started(self, test_suite_name: str):
                print("Test execution started: " + test_suite_name)

            def test_suite_ended(self, test_suite_name: str, test_count: int, execution_time: float):
                print("Test execution ended: " + test_suite_name)

            def test_suite_errored(self, test_suite_name: str, status_code: int):
                print("Test execution of %s errored with status code: %d" % (test_suite_name, status_code))

        work_dir = str(Path.home()) + "/mto_work_dir"
        log_path = work_dir + "/log_test_result"
        if os.path.exists(log_path):
            shutil.rmtree(log_path)
        try:
            access_rights = 0o755
            os.mkdir(log_path, access_rights)
        except OSError:
            print("Creation of the directory %s failed" % log_path)

        def get_next_test_suite(job_queue):
            while job_queue.qsize() != 0:
                yield pickle.loads(job_queue.get())

        with AndroidTestOrchestrator(log_path) as orchestrator:
            test_listener = Listener("")
            orchestrator.execute_test_plan(test_application, get_next_test_suite(job_queue), test_listener)


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

    :raise: Exception if sdk not found through environ vars or in standard user-home location per platform
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
    cmd = [emulator_path, "-port", str(port), "@%s" % avd, "-wipe-data", "-read-only"]
    if is_retry and "-no-snapshot-load" not in cmd:
        cmd.append("-no-snapshot-load")
    if os.environ.get("EMULATOR_OPTS"):
        cmd += os.environ["EMULATOR_OPTS"].split()
    proc = subprocess.Popen(cmd, stderr=sys.stderr, stdout=sys.stdout)
    time.sleep(3)
    getprop_cmd = [adb_path, "-s", device_id, "shell", "getprop", "sys.boot_completed"]
    tries = 100
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
        p = subprocess.Popen(download_emulator_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                             stdin=subprocess.PIPE)
        p.stdin.write(b"Y\n")
        if p.wait() != 0:
            raise Exception("Failed to download image for AVD")
        create_avd_cmd = [avdmanager_path, "create", "avd", "-n", EMULATOR_NAME, "-k",
                          "system-images;android-28;default;x86_64",
                          "-d", "pixel_xl"]
        p = subprocess.Popen(create_avd_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             stdin=subprocess.PIPE)
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


def make_worker_manager(ip, port, authkey):
    """ Create a manager for a worker. This manager connects to a server on the
        given address and exposes the get_job_q and get_result_q methods for
        accessing the shared queues from the server.

        :param ip: ip address of master server
        :param port: used for network traffic between VMs and Master to User.
        port enabled on azure vms (currently using 55000). For more details on how to configure
        inbound/outbound connections visit https://docs.microsoft.com/en-us/azure/virtual-network/security-overview
        :param authkey: a byte string which can be thought of as a password

        :return: a manager object.
    """

    class ServerQueueManager(BaseManager):
        pass

    ServerQueueManager.register('get_job_queue')
    ServerQueueManager.register('get_result_queue')
    ServerQueueManager.register('get_token')

    manager = ServerQueueManager(address=(ip, port), authkey=authkey)
    manager.connect()
    print('worker connected to master %s:%s' % (ip, port))
    return manager


if __name__ == "__main__":
    """
    To run worker_server.py script standalone: 
    python workerserver.py <path of args.txt>
    (without using agent scripts to run automatically -- details in Readme)

    args.txt example:
    [settings]
    app_apk_path = /Users/hewei/app-debug.apk
    test_apk_path = /Users/hewei/app-debug-androidTest.apk  

    If masterserver.py deployed locally (not on Azure vm1 - Master vm) for local test, change 
    MASTER_IP = "52.247.223.117" to MASTER_IP = "localhost"

    The work_dir is "/mto_work_dir" under home_dir, agent scripts deployed to Azure vm 
    creates work_dir if not exists, to run script standalone, mto_work_dir need to exists.
    """
    # port enabled on azure vms (currently using 55000).# auth_key works as a password, send from user to VMs,
    # currently hard corded as a general key for testing purpose.
    port_num = PORT_NUM
    auth_key = AUTH_KEY
    master_vm = MASTER_VM

    # args are read from args.txt file from test artifacts from user
    config = configparser.ConfigParser()
    config.read(sys.argv[1])
    app_apk_path = config.get("settings", "app_apk_path")
    test_apk_path = config.get("settings", "test_apk_path")

    # Start a shared manager server and access its queues
    manager = make_worker_manager(master_vm, port_num, auth_key)
    job_queue = manager.get_job_queue()
    result_queue = manager.get_result_queue()
    token = manager.get_token()

    launch_emulator(5554)
    runner = EspressoTestRunner(app_apk_path, test_apk_path, device="emulator-5554")
    runner.start()
