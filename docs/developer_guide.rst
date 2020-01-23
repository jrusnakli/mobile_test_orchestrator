.. _developer_guide:

Welcome to AndroidTestOrchestrator's Developer Guide!
=====================================================
.. contents:: Table of Contents


Introduction
============

This is the developer guide for AndroidTestOrchestrator.  There are three main tasks to cover:

#. Capturing logcat to a file as well as capturing key starting/end point within the file
#. Parsing specific tags from logcat for processing

Test Execution
==============
The class diagram below shows the classes involved in test exection and their relationships.  The execution is
conducted through asyncio via async tasks.

.. image:: resources/TestExecutionClassDiagram.png

Test Status Collection
----------------------

Test status collection is performed via the following classes:

.. image:: resources/TestStatusCollectionCD.png

Capturing logcat
================

Capturing logcat is done through a single process in launched in the background, with output redirected to the file.
No Python code is involved in processing output and writing to a file in this way.  There is an interface provided,
however, to capture points of interest (e.g. start or end of a test suite or test) during logcat capture.  This is s
shown below

.. image:: resources/LogcatCaptureSequence.png



