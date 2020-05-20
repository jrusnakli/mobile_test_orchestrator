import os
import signal
import subprocess

import logging
from subprocess import Popen
from types import TracebackType

try:
    import psutil  # type: ignore
except Exception:
    psutil = None

from contextlib import suppress
from typing import Dict, Tuple, Optional, TextIO, Type

from .timing import StopWatch
from .parsing import LineParser
from .device import Device, RemoteDeviceBased

log = logging.getLogger(__file__)
log.setLevel(logging.WARNING)


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
            self._markers: Dict[str, int] = {}
            self._proc: Optional[Popen] = None
            if os.path.exists(output_path):
                raise Exception(f"Path {output_path} already exists; will not overwrite")
            self._output_file: TextIO = open(output_path, 'w')

        def __enter__(self) -> "DeviceLog.LogCapture":
            """
            start capturing logcat output from device with given id to given output path
            """
            self._markers = {}
            self._proc = self._device.execute_remote_cmd_background("logcat", stdout=self._output_file)
            return self

        def _mark(self, marker: str, start_or_end: str) -> int:
            """
            Capture the current position (after flushing buffers) within the log file as a start/end marker

            :param marker: name to be associated with the starting point
            :param start_or_end: start or end (type of marker)

            :returns: file position captured for the marker (will also be captured internal to this object)
            """
            marker = f"{marker}.{start_or_end}"
            if marker in self._markers:
                log.error(f"Duplicate test marker!: {marker}")
            if self._proc and self._proc.poll() is None:
                if psutil:
                    # For windows compat, we use psutil over os.kill(SIGSTOP/SIGCONT)
                    p = psutil.Process(self._proc.pid)
                    # pause logcat process, flush file, capture current file position and resume logcat
                    p.suspend()
                else:
                    os.kill(self._proc.pid, signal.SIGSTOP)
                self._output_file.flush()
                self._markers[marker] = self._output_file.tell()
                if psutil:
                    p.resume()
                else:
                    os.kill(self._proc.pid, signal.SIGCONT)
            else:
                raise Exception("process is not active")
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

        def __exit__(self, exc_type: Optional[Type[BaseException]], exc_val: Optional[BaseException],
                     exc_tb: Optional[TracebackType]) -> None:
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
            self._output_file = None  # type: ignore

        @property
        def markers(self) -> Dict[str, int]:
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

    DEFAULT_LOGCAT_BUFFER_SIZE = "5M"

    def __init__(self, device: Device) -> None:
        super().__init__(device)
        device.execute_remote_cmd("logcat", "-G", self.DEFAULT_LOGCAT_BUFFER_SIZE)

    def get_logcat_buffer_size(self, channel: str = 'main') -> Optional[str]:
        """
        @:param channel: which channel's size ('main', 'system', or 'crash')

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

        NOTE: Android has intermittent failures not clearing the main log. In particaular, this
        operation seems somehsat asynchronous and can interfere or be interered with if other
        logcat call are made in a short time window around this call.
        """
        self.device.execute_remote_cmd("logcat", "-b", buffer, "-c")

    def logcat(self, *options: str) -> Device.AsyncProcessContext:
        """
        async generator to continually output lines from logcat until client
        exits processing (exist async iterator), at which point process is killed
        :param options: list of string options to provide to logcat command
        :param loop: specific asyncio loop to use or None for default

        :return: AsyncGenerator to iterate over lines of logcat

        :raises: asyncio.TimeoutError if timeout is not None and timeout is reached
        """
        return self.device.monitor_remote_cmd("logcat", *options)

    def capture_to_file(self, output_path: str) -> "LogCapture":
        """
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
