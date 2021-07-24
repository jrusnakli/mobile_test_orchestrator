import asyncio
from typing import Iterator, AsyncIterator, Any


async def _async_iter_adapter(iter: Iterator[Any]) -> AsyncIterator[Any]:
    for item in iter:
        yield item
        await asyncio.sleep(0)
