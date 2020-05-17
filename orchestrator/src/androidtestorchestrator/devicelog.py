"""
The *devielog* package provides the API for streaming, capturing and manipulating the device log
"""
import os
import subprocess
from asyncio import AbstractEventLoop

import logging
from subprocess import Popen
from types import TracebackType

from contextlib import suppress
from typing import AsyncContextManager, Optional, TextIO, Type, Any

from .device import Device, DeviceBased

log = logging.getLogger(__file__)
log.setLevel(logging.WARNING)

__all__ = ["DeviceLog"]


class DeviceLog(DeviceBased):
    """
    Class to read, capture and clear a device's log (Android logcat)
    """

    class LogCapture(DeviceBased):
        """
        Context manager to capture continuous logcat output from an Android device to a file.
        On exit, will terminat the logcat process, closing the file

        :param device: device whose log we want to monitor
        :param output_path: file path where logcat output is to be captured
        """

        def __init__(self, device: Device, output_path: str):
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
        """
        :param device: Device from which to capture the log
        """
        super().__init__(device)
        device.execute_remote_cmd("logcat", "-G", self.DEFAULT_LOGCAT_BUFFER_SIZE)

    def get_logcat_buffer_size(self, channel: str = 'main') -> Optional[str]:
        """
        :param channel: which channel's size ('main', 'system', or 'crash')
        :return: the logcat buffer size for given channel, or None if not defined
        """
        completed = self.device.execute_remote_cmd("logcat", "-g", stdout=subprocess.PIPE)
        output: str = completed.stdout
        for line in output.splitlines():
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

    def clear(self) -> None:
        """
        clear device log on the device and start fresh
        """
        self.device.execute_remote_cmd("logcat", "-b", "all", "-c")

    async def logcat(self, *options: str, loop: Optional[AbstractEventLoop] = None
                     ) -> AsyncContextManager[Any]:
        """
        async generator to continually output lines from logcat until client
        exits processing (exist async iterator), at which point process is killed

        :param options: list of string options to provide to logcat command
        :param loop: specific asyncio loop to use or None for default
        :return: AsyncGenerator to iterate over lines of logcat
        :raises: asyncio.TimeoutError if timeout is not None and timeout is reached
        """
        return await self.device.monitor_remote_cmd("logcat", *options, loop=loop)

    def capture_to_file(self, output_path: str) -> "LogCapture":
        """
        Capture log to a file. This is a convenience method over instantiating
        an instance of LogCaptur directly

        :param output_path: path to capture log output to
        :return: context manager for capturing output to specified file

        >>> device = Device("some_serial_id", "/path/to/adb")
        ... log = DeviceLog(device)
        ... with log.capture_to_file("./log.txt") as log_capture:
        ...     log_capture.mark_start("some_task")
        ...     # do_something()
        ...     log_capture.mark_end("some_task")
        ... # file closed, logcat process terminated

        """
        return self.LogCapture(self.device, output_path=output_path)
