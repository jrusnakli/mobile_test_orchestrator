"""
The *device_log* package provides the API for streaming, capturing and manipulating the device log
"""
import os
import subprocess

import logging
from subprocess import Popen
from types import TracebackType

from contextlib import suppress
from typing import Dict, Tuple, Optional, TextIO, Type

from .parsing import LineParser
from .device import Device, RemoteDeviceBased

log = logging.getLogger(__file__)

__all__ = ["DeviceLog"]


class DeviceLog(RemoteDeviceBased):
    """
    Class to read, capture and clear a device's log (Android logcat)
    """

    class LogCapture(RemoteDeviceBased):
        """
        context manager to capture logcat output from an Android device to a file, providing interface
        to mark key positions within the file (e.g. start and end of a test)
        """

        def __init__(self, device: Device, output_path: str):
            """
            :param device: device whose log we want to monitor
            :param output_path: file path where logcat output is to be captured
            """
            super(DeviceLog.LogCapture, self).__init__(device)
            self._proc: Optional[Popen[str]] = None
            if os.path.exists(output_path):
                raise Exception(f"Path {output_path} already exists; will not overwrite")
            self._output_file: TextIO = open(output_path, 'w')

        def __enter__(self) -> "DeviceLog.LogCapture":
            """
            start capturing logcat output from device with given id to given output path
            """
            self._proc = self._device.execute_remote_cmd_background("logcat", stdout=self._output_file)
            return self

        def __exit__(self, exc_type: Optional[Type[BaseException]], exc_val: Optional[BaseException],
                     exc_tb: Optional[TracebackType]) -> None:
            """
            stop logcat capture and close file
            """
            if not self._proc:
                return
            with suppress(Exception):
                self._proc.kill()
            self._proc = None
            self._output_file.close()
            self._output_file = None  # type: ignore

    DEFAULT_LOGCAT_BUFFER_SIZE = "5M"

    def __init__(self, device: Device) -> None:
        super().__init__(device)
        device.execute_remote_cmd("logcat", "-G", self.DEFAULT_LOGCAT_BUFFER_SIZE)

    def get_logcat_buffer_size(self, channel: str = 'main') -> Optional[str]:
        """
        :param channel: which channel's size ('main', 'system', or 'crash')
        :return: the logcat buffer size for given channel, or None if not defined
        """
        completed = self.device.execute_remote_cmd("logcat", "-g", stdout=subprocess.PIPE)
        stdout: str = completed.stdout
        for line in stdout.splitlines():
            if line.startswith(channel):
                "format is <channel>: ring buffer is <size>"
                return line.split()[4]
        return None

    logcat_buffer_size = property(get_logcat_buffer_size)

    def set_logcat_buffer_size(self, size_spec: str) -> None:
        """
        :param size_spec: string spec (per adb logcat --help) for size of buffer (e.g. 10M = 10 megabytes)
        """
        self.device.execute_remote_cmd("logcat", "-G", size_spec)

    def clear(self, buffer: str = "all") -> None:
        """
        clear device log on the device and start fresh

        NOTE: Android has intermittent failures not clearing the main log. In particular, this
        operation seems somewhat asynchronous and can interfere or be interfered with if other
        logcat call are made in a short time window around this call.
        """
        self.device.execute_remote_cmd("logcat", "-b", buffer, "-c")

    async def clear_async(self, buffer: str = "all", timeout: Optional[float] = None) -> None:
        """
        clear device log on the device and start fresh

        NOTE: Android has intermittent failures not clearing the main log. In particular, this
        operation seems somewhat asynchronous and can interfere or be interfered with if other
        logcat call are made in a short time window around this call.

        :param buffer: type of buffer to clear
        :param timeout: optional timeout

        :raises: asyncio.TimeoutError if timeout is specified and reached before command completes
        """
        await self.device.execute_remote_cmd_async("logcat", "-b", buffer, "-c", timeout=timeout)

    def logcat(self, *options: str) -> Device.AsyncProcessContext:
        """
        async generator to continually output lines from logcat until client
        exits processing (exist async iterator), at which point process is killed

        :param options: list of string options to provide to logcat command
        :return: AsyncGenerator to iterate over lines of logcat
        :raises: asyncio.TimeoutError if timeout is not None and timeout is reached
        """
        return self.device.monitor_remote_cmd("logcat", *options)

    def capture_to_file(self, output_path: str) -> "LogCapture":
        """
        :param output_path: path to capture log output to

        :return: context manager for capturing output to specified file
        """
        return self.LogCapture(self.device, output_path=output_path)


# noinspection SpellCheckingInspection
class LogcatTagDemuxer(LineParser):
    """
    Concrete LineParser that processes lines of output from logcat filtered on a set of tags and demuxes those lines
    based on a specific handler for each tag
    """

    def __init__(self, handlers: Dict[str, Tuple[str, LineParser]]):
        """
        :param handlers: dictionary of tuples of (logcat priority, handler)
        """
        # remove any spec on priority from tags:
        super().__init__()
        self._handlers = {tag: handlers[tag][1] for tag in handlers}

    def parse_line(self, line: str) -> None:
        """
        farm each incoming line to associated handler based on adb tag
        :param line: line to be parsed
        """
        if not self._handlers:
            return
        if line.startswith("-----"):
            # ignore, these are startup output not actual logcat output from device
            return
        try:
            # extract basic tag from line of logcat:
            tag = line.split('(', 2)[0]
            if not len(tag) > 2 and tag[1] == '/':
                log.debug("Invalid tag in logcat output: %s" % line)
                return
            tag = tag[2:]
            if tag not in self._handlers:
                log.error("Unrecognized tag!? %s" % tag)
                return
            # demux and handle through the proper handler
            self._handlers[tag].parse_line(line)
        except ValueError:
            log.error("Unexpected logcat line format: %s" % line)
