import logging
import os
import subprocess
from typing import Optional, List

from .device import (
    Device,
    RemoteDeviceBased,
)

log = logging.getLogger(__name__)


class DeviceStorage(RemoteDeviceBased):
    """
    Class providing API to push, install and remove files and apps to a remote device

    :param device: which device
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

    def list(self, remote_path: str) -> List[str]:
        """
        list the files at the given path (which can be a filename pattern)

        :param remote_path: path or pattern to match
        :return: List of files found, empty if no files found
        """
        try:
            proc = self.device.execute_remote_cmd("shell", "ls", remote_path, stdout=subprocess.PIPE,
                                                  stderr=subprocess.PIPE)
        except Device.CommandExecutionFailure:
            return []
        return proc.stdout.split()

    def push(self, local_path: str, remote_path: str) -> None:
        """
        Push a local file to the given location on the remote device.
        NOTE: pushing to an app's data directory is not possible and leads to
          a permission-denied response even when using "run-as"

        :param local_path: path to local host file
        :param remote_path: path to place file on the remote device
        :raises FileNotFoundError: if provide local path does not exist and is a file
        :raises Exception: if command to push file failed
        """
        if not os.path.isfile(local_path):
            raise FileNotFoundError("No such file found: %s" % local_path)
        self.device.execute_remote_cmd('push', local_path, remote_path)

    def pull(self, remote_path: str, local_path: str, run_as: Optional[str] = None) -> None:
        """
        Pull a file from device

        :param remote_path: location on phone to pull file from
        :param local_path: path to file to be created from content from device
        :param run_as: user to run command under on remote device, or None
        :raises FileExistsError: if the locat path already exists
        :raises Exception: if command to pull file failed
        """
        if os.path.exists(local_path):
            log.warning("File %s already exists when pulling. Potential to overwrite files.", local_path)
        if run_as:
            with open(local_path, 'w') as out:
                self.device.execute_remote_cmd('shell', 'run-as', run_as, 'cat', remote_path, stdout=out)
        else:
            self.device.execute_remote_cmd('pull', remote_path, local_path)

    def make_dir(self, path: str, run_as: Optional[str] = None) -> None:
        """
        make a directory on remote device

        :param path: path to create
        :param run_as: user to run command under on remote device, or None
        :raises Exception: on failure to create directory
        """
        if run_as:
            self.device.execute_remote_cmd("shell", "run-as", run_as, "mkdir", "-p", path,
                                           timeout=Device.TIMEOUT_ADB_CMD)
        else:
            self.device.execute_remote_cmd("shell", "mkdir", "-p", path,
                                           timeout=Device.TIMEOUT_ADB_CMD)

    def remove(self, path: str, recursive: bool = False, run_as: Optional[str] = None) -> None:
        """
        remove a file or directory from remote device

        :param path: path to remove
        :param recursive: if True and path is directory, recursively remove all contents otherwise, othrewise raise
            Exception if directory is not empty
        :param run_as: user to run command under on remote device, or None
        :raises Exception: on failure to remote specified path
        """
        cmd = ["shell", "run-as", run_as, "rm"] if run_as else ["shell", "rm"]
        if recursive:
            cmd.append("-r")
        cmd.append(path)
        self.device.execute_remote_cmd(*cmd, timeout=Device.TIMEOUT_LONG_ADB_CMD)
