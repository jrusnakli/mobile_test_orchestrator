
Mobile Test Orchestrator
========================

Mobile Test Orchestrator (MTO) provides APIs to orchestrate test execution against Android devices. It allows users to define a test plan, which is a test suites collection.
The test plan can be executed on Android emulators, as well as real devices. The API provides the fundamentals to have seamless user experience to run distributed test on real devices or emulators.

Key features include:

1. Capture of full logcat during execution of the test plan.
2. File position within the logcat file to mark the beginning and end of a test suite, and each test.
3. Monitor specific tags from logcat during execution.


User Guide
==========

Get Started
-----------

A test plan is an iterator over a collection of test suites, which can be created out of `mobiletestorchestrator.TestSuite` class

```python

from mobiletestorchestrator import TestSuite
# arguments to be passed to the am instrument command, run as "am instrument -w -r [arguments] <package>/<runner> "
test_suite = TestSuite(name='test_suite1', arguments=["--package", "com.some.test.package"])
test_plan = iter([test_suite])
```

An orchestrator can execute the test plan. A `mobiletestorchestrator.TestListener` will report the test result as execution proceeds.
A `mobiletestorchestrator.Device` is intended to be a direct bridge to the same functionality as adb, with minimized embellishments. 

```python
from mobiletestorchestrator import AndroidTestOrchestrator, TestSuite, TestRunListener
from mobiletestorchestrator.device import Device
from mobiletestorchestrator.application import TestApplication
device = Device(device_id="emulator-5554")
test_application = TestApplication.from_apk(apk_path="/some/test.apk", device=device)  # installs the given apk

class Listener(TestRunListener):
     def test_ended(self, class_name: str, test_name: str, **kwargs) -> None:
         print("Test %s passed" % test_name)

     def test_failed(self, class_name: str, test_name: str, stack_trace: str) -> None:
         print("Test %s failed" % test_name)

     def test_ignored(self, class_name: str, test_name: str) -> None:
         print("Test %s ignored" % test_name)

     def test_assumption_failure(self, class_name: str, test_name: str, stack_trace: str) -> None:
         print("Test assumption failed, %s skipped" % test_name)

     def test_run_started(self, test_run_name: str, count: int = 0) -> None:
         print("Test execution started: " + test_run_name)

     def test_run_ended(self, duration: float = -1.0, **kwargs) -> None:
         print("Test execution ended")

     def test_run_failed(self, error_message: str) -> None:
         print("Test execution failed with error message: %s" % error_message)

 with AndroidTestOrchestrator(artifact_dir=".") as orchestrator:

     test_suite = TestSuite('test_suite1', ["--package", "com.some.test.package"])
     test_plan = iter([test_suite])
     orchestrator.execute_test_plan(test_application, test_plan, Listener())
     # or
     orchestrator.execute_test_suite(test_suite, Listener())      
```

A sample expected test result output based on the same Listener above will be 
``` 
Test execution started: test_suite1

Test testFoo passed
Test testBar failed
Test testBaz skipped

Test execution endded: test_suite1
```


Developer Guide
===============

Project structure
-----------------

* `docs`:  contains documentation in .rst format
* `orchestrator`: Python code to provide an API to orchestrate test execution
* `testsupportapps`: A sample Android apps with Espresso tests is used solely for testing


Setting up environment
----------------------
Please set ANDROID_SDK_ROOT to point to your Android SDK location

For testing, `$ANDROID_SDK_ROOT/tools/emulator -list-avds` should show at least one emulator definition;  the first will
be used for testing purposes.

If you do not have an emulator listed, you can do the following to create a simple one:

(1) Issue the following command to install an compatible Android system image with the SDK manager, in this example, Android 28

`$ $ANDROID_SDK_ROOT/tools/bin/sdkmanager "system-images;android-28;default;x86_64"`

(2) Issue the following command to create a basic emulator with the system image defined in the previous step:

`$ $ANDROID_SDK_ROOT/tools/bin/avdmanager create avd -n MTO_emulator -k "system-images;android-28;default;x86_64"`

Building the distribution:
--------------------------

From the orchestrator directory, run:

`$ python setup.py install`

This will (in addition to normal Python setup.py "stuff"):

#. package it as a resource with the distro


Running Tests
-------------
Set up a virtual env and ensure pytest and apk-bitminer are installed.

To run tests, ensure environment as above and in the orchestrator/test directory:

`$ pytest -s .`

This will build debug versions of the test butler app, and test support apps used during testing.

Test py files directly  in the `orchestrator/test` are unit-test-like.
   
Those in `orhcestrator/tst/test_system_integration` test a fully integrated system and execution across host,
test butler service on the emulator, and a test app on the device. These are progressively more complex and longer time-running tests and pytest should execute them from faster-running to
the more slower-running integration tests

Debugging Tests
---------------

Recommend setting `PYTHONASYNCIODEBUG` to `1` to use asyncio's debug output

Currently, if any breakpoints are set it causes problems in IntelliJ as under the hoods in places asyncio/subprocess
uses multiprocessing module and fake KeyboardInterrupts get generate/picked up :-(.  pytest staright-up works though.
If you turn off all breakpoints, then you can at least get orderly output in PyChram/IntelliJ

