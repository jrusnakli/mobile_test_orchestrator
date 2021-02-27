import asyncio

import pytest

from mobiletestorchestrator.timing import Timer


class TestTimer(object):

    def test_mark_start_end(self):
        async def task():
            timer = Timer(duration=1)
            timer.mark_start("task")
            await asyncio.sleep(20)
            timer.mark_end("task")
        with pytest.raises(RuntimeError):
            asyncio.get_event_loop().run_until_complete(task())
