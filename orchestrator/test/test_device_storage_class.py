import os
import subprocess
from contextlib import suppress

import pytest

from androidtestorchestrator.device import Device
from androidtestorchestrator.devicestorage import DeviceStorage


# noinspection PyShadowingNames
class TestDeviceStorage:
    def test_external_storage_location(self, device: Device):
        assert DeviceStorage(device).external_storage_location == "/sdcard"

    def test_push_remove(self, device: Device):
        storage = DeviceStorage(device)
        remote_location = "/".join([storage.external_storage_location, "some_file"])

        with suppress(Exception):
            storage.remove(remote_location)

        completed = device.execute_remote_cmd("shell", "ls", device.external_storage_location, stdout=subprocess.PIPE)
        output: str = completed.stdout
        if os.path.basename(remote_location) in output:
            raise Exception("Error: did not expect file %s on remote device" % remote_location)
        storage.push(local_path=(os.path.abspath(__file__)), remote_path=remote_location)
        completed = device.execute_remote_cmd("shell", "ls", device.external_storage_location + "/",
                                              stdout=subprocess.PIPE)
        output: str = completed.stdout
        assert os.path.basename(remote_location) in output

        storage.remove(remote_location)
        completed = device.execute_remote_cmd("shell", "ls", device.external_storage_location, stdout=subprocess.PIPE)
        output: str = completed.stdout
        assert not os.path.basename(remote_location) in output

    @pytest.mark.asyncio
    async def test_push_remove_async(self, device: Device):
        storage = DeviceStorage(device)
        remote_location = "/".join([storage.external_storage_location, "some_file"])

        with suppress(Exception):
            storage.remove(remote_location)

        completed = device.execute_remote_cmd("shell", "ls", device.external_storage_location, stdout=subprocess.PIPE)
        output: str = completed.stdout
        if os.path.basename(remote_location) in output:
            raise Exception("Error: did not expect file %s on remote device" % remote_location)
        await storage.push_async(local_path=(os.path.abspath(__file__)), remote_path=remote_location)
        completed = device.execute_remote_cmd("shell", "ls", device.external_storage_location + "/", stdout=subprocess.PIPE)
        output: str = completed.stdout
        assert os.path.basename(remote_location) in output

        storage.remove(remote_location)
        completed = device.execute_remote_cmd("shell", "ls", device.external_storage_location, stdout=subprocess.PIPE)
        output: str = completed.stdout
        assert not os.path.basename(remote_location) in output

    def test_push_invalid_remote_path(self, device: Device):
        storage = DeviceStorage(device)
        remote_location = "/a/bogus/remote/location"
        with pytest.raises(Exception):
            storage.push(local_path=(os.path.abspath(__file__)),
                         remote_path=remote_location)

    def test_pull(self, device: Device, temp_dir):
        storage = DeviceStorage(device)
        local_path = os.path.join(temp_dir, "somefile")
        remote_path = "/".join([storage.external_storage_location, "touchedfile"])
        device.execute_remote_cmd("shell", "touch", remote_path)
        storage.pull(remote_path=remote_path, local_path=local_path)
        assert os.path.exists(local_path)

    @pytest.mark.asyncio
    async def test_pull_async(self, device: Device, temp_dir):
        storage = DeviceStorage(device)
        local_path = os.path.join(temp_dir, "somefile")
        remote_path = "/".join([storage.external_storage_location, "touchedfile"])
        device.execute_remote_cmd("shell", "touch", remote_path)
        await storage.pull_async(remote_path=remote_path, local_path=local_path)
        assert os.path.exists(local_path)

    def test_pull_invalid_remote_path(self, device: Device, temp_dir):
        storage = DeviceStorage(device)
        local = os.path.join(str(temp_dir), "nosuchfile")
        with pytest.raises(Exception):
            storage.pull(remote_path="/no/such/file", local_path=local)
        assert not os.path.exists(local)

    @pytest.mark.asyncio
    async def test_pull_invalid_remote_path_async(self, device: Device, temp_dir):
        storage = DeviceStorage(device)
        local = os.path.join(str(temp_dir), "nosuchfile")
        with pytest.raises(Exception):
            await storage.pull_async(remote_path="/no/such/file", local_path=local)
        assert not os.path.exists(local)

    def test_make_dir(self, device: Device):
        storage = DeviceStorage(device)
        new_remote_dir = "/".join([storage.external_storage_location, "a", "b", "c", "d"])
        # assure dir does not already exist:
        with suppress(Exception):
            storage.remove(new_remote_dir, recursive=True)

        try:
            completed = device.execute_remote_cmd("shell", "ls", "-d", new_remote_dir, stdout=subprocess.PIPE)
            output: str = completed.stdout
            # expect "no such directory" error leading to exception, but just in case:
            assert new_remote_dir not in output or "No such file" in output
        except Device.CommandExecutionFailure as e:
            assert "no such" in str(e).lower()

        storage.make_dir(new_remote_dir)
        completed = device.execute_remote_cmd("shell", "ls", "-d", new_remote_dir, stdout=subprocess.PIPE)
        output: str = completed.stdout
        assert new_remote_dir in output
