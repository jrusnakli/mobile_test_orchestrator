import asyncio
from typing import List

import pytest

from mobiletestorchestrator.device_pool import AsyncDevicePool, AsyncEmulatorPool
from mobiletestorchestrator.emulators import Emulator


class TestDevicePool:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("q_class", [AsyncDevicePool, AsyncEmulatorPool])
    async def test_device_queue_discovery(self, devices: List[Emulator], q_class: type):
        device_queue = await q_class.discover()

        async def get_count(count: int = 0):
            # have to recurse to prevent each async with from relinquishing the device back:
            if device_queue._q.empty():
                return count
            async with device_queue.reserve():
                return await get_count(count+1)

        assert await asyncio.wait_for(get_count(), timeout=3) == len(devices)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("q_class", [AsyncDevicePool, AsyncEmulatorPool])
    async def test_device_queue_discovery_no_such_devices(self, devices, q_class: type):
        # device is needed to make sure there are some emulators in existence and the filter filters them out
        with pytest.raises(Exception) as e:
            assert 'discovered' in str(e)
            await q_class.discover(filt=lambda x: False)  # all devices filtered out
