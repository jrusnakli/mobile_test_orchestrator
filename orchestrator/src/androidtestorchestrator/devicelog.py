import asyncio.subprocess
import logging
import os

from collections import OrderedDict
from contextlib import suppress
from typing import Union, ContextManager, AsyncGenerator, Dict, Tuple

import psutil

from .device import Device, RemoteDeviceBased
from .parsing import LineParser
from .timing import StopWatch


log = logging.getLogger(__name__)


class DeviceLog(RemoteDeviceBased):
    """
    Class to read, capture and clear a device's log (Android logcat)
    """

    class LogCapture(RemoteDeviceBased, StopWatch):
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
            self._markers = OrderedDict()
            self._proc = None
            if os.path.exists(output_path):
                raise Exception("Path %s already exists; will not overwrite" % output_path)
            self._output_file = open(output_path, 'w')

        def __enter__(self) -> StopWatch:
            """
            start capturing logcat output from device with given id to given output path
            """
            self._markers = OrderedDict()
            self._proc = self._device.execute_remote_cmd_background("logcat", stdout=self._output_file)
            return self

        def _mark(self, marker: str, start_or_end: str) -> None:
            """
            Capture the current position (after flushing buffers) within the log file as a start/end marker

            :param marker: name to be associated with the starting point
            :param start_or_end: start or end (type of marker)

            :returns: file position captured for the marker (will also be captured internal to this object)
            """
            if not self._proc or self._proc.poll() is not None:
                raise Exception("No running process. Cannot mark output")
            marker = marker + ".%s" % start_or_end
            if marker in self._markers:
                log.error("Duplicate test marker!: %s" % marker)
            # For windows compat, we use psutil over os.kill(SIGSTOP/SIGCONT)
            p = psutil.Process(self._proc.pid)
            # pause logcat process, flush file, capture current file position and resume logcat
            p.suspend()
            self._output_file.flush()
            self._markers[marker] = self._output_file.tell()
            p.resume()
            return self._markers[marker]

        def mark_start(self, name: str) -> None:
            """
            Capture current position within output file as a marker of the start of an activity
            :param name: name of activity
            """
            self._mark(name, "start")

        def mark_end(self, name: str) -> None:
            """
            Capture current position within output file as a marker of end of an activity
            :param name: name of activity
            """
            self._mark(name, "end")

        def __exit__(self, exc_type, exc_val, exc_tb) -> None:
            """
            stop logcat capture.  markers will be "line_justifyd" so that starting markers will be placed at the
            beginning of the line at which the marker was captured and ending markers will be placed at the end of
            the line at which the marker was captured
            """
            if not self._proc:
                return
            with suppress(Exception):
                self._proc.kill()
            self._proc = None
            self._output_file.close()
            self._output_file = None

        @property
        def markers(self) -> OrderedDict:
            """
            Only call this function once log capture has stopped

            :return: a dictionary of all captured markers, with the key (appended with ".start" or ".end")
                being the name of the marker (e.g. a test name) and the value being the associated line-justified file
                position associated with that marker.
            """
            self._line_justify_markers()
            return self._markers

        def _line_justify_markers(self) -> None:
            """
            align start markers to just after previous return or beginning of file,
            end marker to next return character or end of file
            """
            # TODO: may need to tune this to look for specific tags when searching for "start" markers
            chunk = 100
            with open(self._output_file.name, 'r', encoding='utf-8', errors='ignore') as f:
                # TODO: simpler algorithm here?
                for marker, pos in self._markers.items():
                    if marker.endswith('start'):
                        new_pos = max(pos - 1, 0)
                        while True:
                            if new_pos == 0:
                                break
                            size = min(chunk, new_pos + 1)
                            new_pos = max(new_pos - chunk, 0)
                            f.seek(new_pos)
                            data = f.read(size)
                            if '\n' in data:
                                new_pos += data.rfind('\n') + 1
                                break
                            elif '\r' in data:
                                new_pos += data.rfind('\r') + 1
                                break
                    elif marker.endswith('end'):
                        new_pos = pos
                        while True:
                            f.seek(new_pos)
                            data = f.read(chunk)
                            if '\n' in data:
                                new_pos += data.find('\n')
                                break
                            elif '\r' in data:
                                new_pos += data.find('\r')
                                break
                            elif len(data) < chunk:
                                new_pos += len(data)
                                break
                            new_pos += len(data)
                    else:
                        raise Exception("Internal error: marker is neither a start or end marker: " + marker)
                    self._markers[marker] = new_pos

    def __init__(self, device: Device):
        super().__init__(device)
        self._device = device

    def clear(self) -> None:
        """
        clear device log on the device and start fresh
        """
        self.device.execute_remote_cmd("logcat", "-c", capture_stdout=False)

    async def logcat(self, *options: str) -> AsyncGenerator[str, str]:
        """
        async generator to continually parser logcat (more precisely, until parser.parse_lines method exits)

        :param options: list of string options to provide to logcat command
        :return: AsyncGenerator to iterate over lines of logcat (as context manager)
        :raises: asyncio.TimeoutError if timeout is not None and timeout is reached
        """
        with await self.device.execute_remote_cmd_async("logcat", *options, wait_timeout=0) as line_generator:
            async for line in line_generator:
                yield line

    def capture_to_file(self, output_path: str) -> ContextManager["DeviceLog.LogCapture"]:
        """
        :param output_path: path to capture log output to

        :return: context manager for capturing output to specified file

        >>> device = Device("some_serial_id", "/path/to/adb")
        ... log = DeviceLog(device)
        ... with log.capture_to_file("./log.txt") as log_capture:
        ...     log_capture.mark_start("some_stask")
        ...     # do_something()
        ...     log_capture.mark_end("some_task")
        ... # file closed, logcat process terminated
        """
        return self.LogCapture(self.device, output_path=output_path)


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

    def parse_line(self, line: str):
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