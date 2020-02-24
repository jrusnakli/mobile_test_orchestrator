import logging
import os
from typing import Optional

from .device import (
    Device,
    RemoteDeviceBased,
)

log = logging.getLogger(__name__)


class DeviceStorage(RemoteDeviceBased):
    """
    Class providing API to push, install and remove files and apps to a remote device
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
        Push a local file to the given location on the remote device

        :param local_path: path to local host file
        :param remote_path: path to place file on the remote device

        :raises FileNotFoundError: if provide local path does not exist and is a file
        :raises Exception: if command to push file failed
        """
        if not os.path.isfile(local_path):
            raise FileNotFoundError("No such file found: %s" % local_path)
        self.device.execute_remote_cmd('push', '%s' % local_path, '%s' % remote_path, capture_stdout=False)

    async def push_async(self, local_path: str, remote_path: str, timeout: Optional[int] = None) -> None:
        """
        Push a local file asynchronously to the given location on the remote device

        :param local_path: path to local host file
        :param remote_path: path to place file on the remote device
        :param timeout: timeout in seconds before raising TimeoutError, or None for no expiry

        :raises FileNotFoundError: if provide local path does not exist and is a file
        :raises Exception: if command to push file failed
        """
        if not os.path.isfile(local_path):
            raise FileNotFoundError("No such file found: %s" % local_path)
        async with await self.device.execute_remote_cmd_async('push', f"{local_path}", f"{remote_path}") as proc:
            await proc.wait(timeout)

    def pull(self, remote_path: str, local_path: str) -> None:
        """
        Pull a file from device

        :param remote_path: location on phone to pull file from
        :param local_path: path to file to be created from content from device

        :raises FileExistsError: if the locat path already exists
        :raises Exception: if command to pull file failed
        """
        if os.path.exists(local_path):
            log.warning("File %s already exists when pulling. Potential to overwrite files." % local_path)
        self.device.execute_remote_cmd('pull', '%s' % remote_path, '%s' % local_path)

    async def pull_async(self, remote_path: str, local_path: str, timeout: Optional[int] = None) -> None:
        """
        Pull a file from device

        :param remote_path: location on phone to pull file from
        :param local_path: path to file to be created from content from device
        :param timeout: timeout in seconds before raising TimeoutError, or None for no expiry

        :raises FileExistsError: if the locat path already exists
        :raises Exception: if command to pull file failed
        """
        if os.path.exists(local_path):
            log.warning("File %s already exists when pulling. Potential to overwrite files." % local_path)
        async with await self.device.execute_remote_cmd_async('pull', '%s' % remote_path, '%s' % local_path) as proc:
            await proc.wait(timeout)

    def make_dir(self, path: str, run_as: Optional[str] = None) -> None:
        """
        make a directory on remote device

        :param path: path to create
        :param run_as: user to run command under on remote device, or None

        :raises Exception: on failure to create directory
        """
        if run_as:
            self.device.execute_remote_cmd("shell", "run-as", run_as, "mkdir", "-p", path, capture_stdout=False)
        else:
            self.device.execute_remote_cmd("shell", "mkdir", "-p", path, capture_stdout=False)

    def remove(self, path: str, recursive: bool = False) -> None:
        """
        remove a file or directory from remote device

        :param path: path to remove
        :param recursive: if True and path is directory, recursively remove all contents otherwise, othrewise raise
            Exception if directory is not empty

        :raises Exception: on failure to remote specified path
        """
        cmd = ["shell", "rm"]
        if recursive:
            cmd.append("-r")
        cmd.append(path)
        self.device.execute_remote_cmd(*cmd, capture_stdout=False)
