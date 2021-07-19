import asyncio
import pytest

from mobiletestorchestrator.timing import Timer


class TestTimer(object):

    @pytest.mark.asyncio
    async def test_mark_start_end(self):
        async def run():
            timer.mark_start("task")
            await asyncio.sleep(20)
            timer.mark_end("task")

        try:
            with pytest.raises(asyncio.TimeoutError):
                timer = Timer(duration=1)
                await run()
        except asyncio.CancelledError:
            pass
