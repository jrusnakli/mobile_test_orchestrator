import asyncio
from typing import List

import pytest

from androidtestorchestrator.devicequeues import AsyncDeviceQueue, AsyncEmulatorQueue
from androidtestorchestrator.emulators import Emulator


class TestDeviceQueue:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("q_class", [AsyncDeviceQueue, AsyncEmulatorQueue])
    async def test_device_queue_discovery(self, device_list: List[Emulator], q_class: type):
        device_queue = await q_class.discover()

        async def get_count(count: int = 0):
            # have to recurse to prevent each async with from relinquishing the device back:
            if device_queue.empty():
                return count
            async with device_queue.reserve():
                return await get_count(count+1)

        assert await asyncio.wait_for(get_count(), timeout=3) == len(device_list)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("q_class", [AsyncDeviceQueue, AsyncEmulatorQueue])
    async def test_device_queue_discovery_no_such_devices(self, device_list, q_class: type):
        # device is needed to make sure there are some emulators in existence and the filter filters them out
        with pytest.raises(Exception) as e:
            assert 'discovered' in str(e)
            await q_class.discover(filt=lambda x: False)  # all devices filtered out
