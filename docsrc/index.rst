.. AndroidTestOrchestrator documentation master file, created by
   sphinx-quickstart on Thu Nov 22 09:11:43 2018.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

Welcome to AndroidTestOrchestrator's documentation!
===================================================
.. contents:: Table of Contents


If you are looking for the Developer's Guide, follow these links:

.. toctree::
   :maxdepth: 2
   :caption: Developer's Guide:

   developer_guide


.. _introduction:

Introduction
============

AndroidTestOrchestrator provides an API for executing a test plan (a collection of test suites) against a set of
Android devices.  Although these devices can be emulators, the main use for AndroidTestOrchestrator is for real
devices.

Other features include:
#. Capture of full logcat during execution of the test plan
#. Ability to monitor specific tags fom logcat during execution
#. Support for emulator bring up
#. Support for running in parallel on multiple devices/emulators

Developers:  see the :ref:`developer_guide`;

.. _main_use_case:

Executing a Test Plan
=====================

A test plan is nothing more than an Iterator over a collection of test suites using the AndroidTestOrchestrator class
in the `androidtestorchestrator` module

.. autoclass:: androidtestorchestrator.TestSuite

A plan is executed via the top-level class:

.. automodule:: androidtestorchestrator
   :members: AndroidTestOrchestrator

.. _device_interactions:

Interacting with Devices Directly
=================================

An API is also provided to interact directly with devices if needed.  In basic scenarios, this is generally not needed.
However, for more advanced use cases, this may be useful.

Raw device instractions
-----------------------
From the `androidorchestrator.application` device:

.. automodule:: androidtestorchestrator.device
   :members: Device

Installing and running applications
-----------------------------------

From the `androidorchestrator.application` module:

.. automodule:: androidtestorchestrator.application
   :members: Application, ServiceApplication, TestApplication

Pushing and Pulling Files to/from Device Storage
------------------------------------------------

.. automodule:: androidtestorchestrator.devicestorage
   :members: DeviceStorage

Accessing logcat
----------------

.. automodule:: androidtestorchestrator.devicelog
   :members: DeviceLog


Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
