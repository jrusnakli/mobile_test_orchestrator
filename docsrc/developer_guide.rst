.. _developer_guide:

Welcome to MobileTestOrchestrator's Developer Guide!
=====================================================
.. contents:: Table of Contents


Introduction
============

This is the developer guide for MobileTestOrchestrator.  Mobile Test Orchestrator (MTO) is a scalable framework for executing
distributed, parallelized tests for Android applications.  As medium to large size apps grow in features and in the
number of tests, so can the time it takes to execute those tests.  Having a platform that can distribute tests across
many different emulator instances or real devices can keep those times down and development cycles short.

In this document, the term "device" will be used to refer to either an emulator or a real device without distinction.
Android development tools nicely abstract out the difference between emulators and real devices, and any distinction
is mostly transpaarent to a test framework.

Test Execution on Android
=========================

Interactions with Android devices for developer purposes mostly (if not entierly) relies on the Android Device Bridge.
This is a tool that connect the device to a host over USB for the purpose of preparing the device for testing and then
executing tests.  The below diagram shows the basic setup and some primary functions for testing.

.. image:: resources/basic_setup.png

This depicts testing via a real device, but the concept is similar for emulators, albeit without a physical USB
connection.  All transactions with a device occur through the Android Device Bridge (a command line tool "adb") that
makes it transparent as to whether you are communicating over a USB connection or directly with an emulator running
on the host.

As apps grow in feature set and testing becomes more robust, the number of tests grow.  Test execution time goes up
with such a single-device setup.  In the age with such an intense focus on developer productivity, keeping test
execution times down is a necessity.  The solution is to distribute tests across multiple device instances.

Distributing and Parallelizing Testing
======================================

One can imagine the next logical step is to run multiple device instances from a single host to run testing.  This is
easily achievable with Android which allows multiple emulator instances or multiple USB connections to be active at
one time.  At enterprise scale, however, execution in a cloud of devices (multiple hosts running multiple device
instances) is desirable.  Mobile Test Orchestrator provides the tools for test execution within such environments.

Python and Asyncio
------------------

A number of the functions to be performed when executing tests on devices are related to setup: installing the apps,
uploading files as test vectors, changing initial settings or properties of the device.  These are one-time setup costs.
Moreover they entail overhead that at best can be minimized as part of the developer's test strategy, but the testing
framework itself has little to do with optimizing that overhead.

Functions such as the actual execution of tests, taking a screenshot or downloading results files are functions that
are primarily conducted on the device.  The host, for the most part, is simply a conduit to the device for conducting
the necessary steps. Ideally, the work that has to be done would all be on the device, with little work to do on the
host.  In reality, the primary transactions the host conducts are almost all I/O type transaction through the adb
tool.  In other words, the solution for parallelized testing on Android (from a single host perspective)
lends itself best to an asynchronous (event-base) task system over a true threaded multi-core system.

Overall, Python, with its multiprocessing support for distributed processing among multiple hosts, and its asyncio
framework for handling event-triggered tasking for handling I/O-bound systems seems a match made in heaven for
the framework that is needed.

Getting to the Details
----------------------

To be more specific, the I/O ladened tasks performed by the host during test setup and execution are:

Setup:
#. Installation of the target app and test app
#. Optionally. installation of possibly other (foreign) apps needed for testing purposes
#. Optionally, pushing of files to the device to act as test vectors for testing (e.g., a video file if testing video transcoding logic)
#. Optionally, initial settings and properties on the device

Execution and Post-Debugging Support:
#. Triggering execution of the tests on the device (via adb execution of the "instrument" command on the device)
#. Capturing of the device log (known in Android and refered to from here on out as "logcat") during the run
#. Monitoring specific logcat commands (as a means for tests to communicate additional data to the host, e.g.)
#. Capturing test status from output of the instrument command, piped through adb
#. Invoking adb commands to capture a screenshot to a file on the host (e.g., upon a failure detected by the host)

Teardown:
#. Pull local files, such as screenshots taken by the test, onto the host machine
#. Cleanup -- uninstall apps, remove any file artifacts from setup or execution, ...

And of course there is the overall orchsetratin of execution across potientially multiple machines hosting multiple
devices.

The Architecture
================

Now that we have the basic concepts in place, let's look at the architecture of  MTO and what is needed to support
them.  The system can be thought of at three levels of complexity:

#. foundational -- these are elements responsible for performing individual single tasks against the device.  For
example, installing and apk bundle or pushing a file to the device, or running an application once installed.  The
level of this API for the most part entails single isolated transactions to perform the most basic functions
#. test execution -- the software elements needed to execute a series of tests in the context of a single host, hosting
multiple devices, including setup and teardown functions, and collection of logs and results
#. test orchestration -- the software elements needed to distribute testing across multiple devices, communicating a
a consistent test configuration to each of the hosts, and collecting test results and artifacts during and at the end
of testing.

The Foundation
--------------

The basic elements of the architecture are in a handful packages: *device*, *application*, *devicelog*, *devicestorage*.

Core Device Classes
###################

.. automodule:: androidtestorchestrator.device
   :members: Device, DeviceBased

Handling Logs
#############

.. automodule:: androidtestorchestrator.devicelog
   :members: DeviceLog

Accessing Device Storage
########################

.. automodule:: androidtestorchestrator.devicestorage
   :members: DeviceStorage

Working with Applications
#########################

.. automodule:: androidtestorchestrator.application
   :members:




