import os
import sys
import shutil
from androidtestorchestrator import support
from androidtestorchestrator import TestApplication, Application, Device
from androidtestorchestrator import TestListener
from androidtestorchestrator import AndroidTestOrchestrator, TestSuite


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
                              adb_path=os.path.join(support.find_sdk(), "platform-tools", support.add_ext("adb")))

    def start(self):
        # install test app
        test_application = TestApplication.from_apk(self._test_app, self._device, as_upgrade=True)
        # install app
        app = Application.from_apk(self._app, self._device, as_upgrade=True)

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


def main(argv):
    # pass args -- arg1: path of app,apk, arg2: path of test_app.apk, arg3: device (eg: "emulator-5554")
    support.launch_emulator(5554)
    app = argv[1]
    test_app = argv[2]
    device = argv[3]
    runner = EspressoTestRunner(app, test_app, device)
    runner.start()


if __name__ == "__main__":
    main(sys.argv)
