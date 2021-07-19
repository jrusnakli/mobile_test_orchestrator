import asyncio
from queue import Queue

import pytest

from mobiletestorchestrator.device_pool import AsyncQueueAdapter


class TestAsyncQueueAdapter:

    @pytest.mark.asyncio
    async def test_get_put(self):
        non_async_q = Queue(10)
        for index in range(10):
            non_async_q.put(index)
        async_q = AsyncQueueAdapter(non_async_q)
        for index in range(10):
            assert await asyncio.wait_for(async_q.get(), timeout=1) == index
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(async_q.get(), timeout=1)
        assert non_async_q.empty()
        await asyncio.wait_for(async_q.put(99), timeout=1)
        assert not non_async_q.empty()
        assert non_async_q.get() == 99
        assert non_async_q.empty()
