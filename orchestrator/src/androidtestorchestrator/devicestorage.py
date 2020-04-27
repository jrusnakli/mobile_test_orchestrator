"""
The *devicestorage* package provides the API for working with a devices (sdcard) storage
"""
import logging
import os
from typing import Optional

from .device import (
    Device,
    DeviceBased,
)

__all__ = ["DeviceStorage"]


log = logging.getLogger(__name__)


class DeviceStorage(DeviceBased):
    """
    Class providing API to push, push and pull files to a remote device
    """

    ERROR_MSG_INSUFFICIENT_STORAGE = "INSTALL_FAILED_INSUFFICIENT_STORAGE"

    def __init__(self, device: Device):
        super(DeviceStorage, self).__init__(device)
        self._ext_storage = None

    @property
    def external_storage_location(self) -> str:
        """
        :return: location on remote device of external storage
        """
        return self.device.external_storage_location

    def push(self, local_path: str, remote_path: str) -> None:
        """
        Push a local file to the given location on the remote device.

        :param local_path: path to local host file
        :param remote_path: path to place file on the remote device
        :raises FileNotFoundError: if provide local path does not exist and is a file
        :raises `Device.CommandExecutionFailure`: if command to push file failed
        """
        # NOTE: pushing to an app's data directory is not possible and leads to
        # a permission-denied response even when using "run-as"
        if not os.path.isfile(local_path):
            raise FileNotFoundError("No such file found: %s" % local_path)
        self.device.execute_remote_cmd('push', local_path, remote_path, capture_stdout=False)

    async def push_async(self, local_path: str, remote_path: str, timeout: Optional[int] = None) -> None:
        """
        Push a local file asynchronously to the given location on the remote device

        :param local_path: path to local host file
        :param remote_path: path to place file on the remote device
        :param timeout: timeout in seconds before raising TimeoutError, or None for no expiry
        :raises FileNotFoundError: if provide local path does not exist and is a file
        :raises `Device.CommandExecutionFailure`: if command to push file failed
        """
        if not os.path.isfile(local_path):
            raise FileNotFoundError("No such file found: %s" % local_path)
        async with await self.device.monitor_remote_cmd('push', f"{local_path}", f"{remote_path}") as proc:
            await proc.wait(timeout)

    def pull(self, remote_path: str, local_path: str, run_as: Optional[str] = None) -> None:
        """
        Pull a file from device

        :param remote_path: location on phone to pull file from
        :param local_path: path to file to be created from content from device
        :param run_as: user to run command under on remote device, or None
        :raises FileExistsError: if the locat path already exists
        :raises `Device.CommandExecutionFailure`: if command to pull file failed
        """
        if os.path.exists(local_path):
            log.warning("File %s already exists when pulling. Potential to overwrite files.", local_path)
        if run_as:
            with open(local_path, 'w') as out:
                self.device.execute_remote_cmd('shell', 'run-as', run_as, 'cat', remote_path, stdout_redirect=out)
        else:
            self.device.execute_remote_cmd('pull', remote_path, local_path)

    async def pull_async(self, remote_path: str, local_path: str, timeout: Optional[int] = None) -> None:
        """
        Pull a file from device

        :param remote_path: location on phone to pull file from
        :param local_path: path to file to be created from content from device
        :param timeout: timeout in seconds before raising TimeoutError, or None for no expiry
        :raises FileExistsError: if the locat path already exists
        :raises `Device.CommandExecutionFailure`: if command to pull file failed
        """
        if os.path.exists(local_path):
            log.warning("File %s already exists when pulling. Potential to overwrite files." % local_path)
        async with await self.device.monitor_remote_cmd('pull', '%s' % remote_path, '%s' % local_path) as proc:
            await proc.wait(timeout)
        if proc.returncode != 0:
            raise Device.CommandExecutionFailure(f"Failed to pull {remote_path} from device")

    def make_dir(self, path: str, run_as: Optional[str] = None) -> None:
        """
        make a directory on remote device

        :param path: path to create
        :param run_as: user to run command under on remote device, or None
        :raises `Device.CommandExecutionFailure`: on failure to create directory
        """
        if run_as:
            self.device.execute_remote_cmd("shell", "run-as", run_as, "mkdir", "-p", path, capture_stdout=False)
        else:
            self.device.execute_remote_cmd("shell", "mkdir", "-p", path, capture_stdout=False)

    def remove(self, path: str, recursive: bool = False, run_as: Optional[str] = None) -> None:
        """
        remove a file or directory from remote device

        :param path: path to remove
        :param recursive: if True and path is directory, recursively remove all contents otherwise will raise
           `Device.CommandExecutionFailure` exception
        :param run_as: user to run command under on remote device, or None
        :raises `Device.CommandExecutionFailure`: on failure to remove specified path
        """
        if run_as:
            cmd = ["shell", "run-as", run_as, "rm"]
        else:
            cmd = ["shell", "rm"]
        if recursive:
            cmd.append("-r")
        cmd.append(path)
        self.device.execute_remote_cmd(*cmd, capture_stdout=False)
