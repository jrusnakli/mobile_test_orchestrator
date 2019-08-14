


docs:  contains documentation in .rst format
orchestrator: Python code to provide an API to orchestrate test execution
testbulerservice: Android service to interact with orchestrator to effect device changes
testsupportapps: apps use solely for testing 


Setting up environment
----------------------
Please set ANDROID_SDK_ROOT to point to your Android SDK location

For testing, $ANDROID_SDK_ROOT/tools/emulator -list-avds should show at least one emulator definition;  the first will
be used for testing purposes.

If you do not have an emulator listed, you can do the following to create a simple one:

(1) Issue the following command to install an compatible Android system image with the SDK manager, in this example, Android 28

$ $ANDROID_SDK_ROOT/tools/bin/sdkmanager "system-images;android-28;default;x86_64"

(2) Issue the following command to create a basic emulator with the system image defined in the previous step:

$ $ANDROID_SDK_ROOT/tools/bin/avdmanager create avd -n MTO_emulator -k "system-images;android-28;default;x86_64"

Building the distribution:
--------------------------

From the orchestrator directory, run:

$ python setup.py

This will (in addition to normal Python setup.py "stuff"):
(1) build the testbutler service as a release apk
(2) sign the apk
(3) package it as a resource with the distro

NOTE: on Windows there are environment variables to set to point to a key-store (TODO: add these)

Running Tests
-------------
Set up a virtual env and ensure pytest and apk-bitminer are installed.

To run tests, ensure environment as above and in the orchestrator/test directory:

% pytest -s .

This will build debug versions of the test butler app, and test support apps used during testing.

Test py files directly  in the orchestrator/test are unit-test-like.
Those in orchestrator/test/test_component_integration test interactions (standalone with no other test apps) between
   the test butler running on an emulator and the host.
Those in orhcestrator/tst/test_system_integration test a fully integrated system and execution across host,
    test butler service on the emulator, and a test app on the device.

These are progressively mor complex and longer time-running tests and pytest should execute them from faster-running to
   the more slower-running integration tests

Debugging Tests
---------------

Recommend setting PYTHONASYNCIODEBUG to 1 to use asyncio's debug output

Currently, if any breakpoints are set it causes problems in IntelliJ as under the hoods in places asyncio/subprocess
uses multiprocessing module and fake KeyboardInterrupts get generate/picked up :-(.  pytest staright-up works though.
If you turn off all breakpoints, then you can at least get orferly output in PyChram/IntelliJ

