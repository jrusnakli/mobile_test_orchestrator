import asyncio

import pytest

from androidtestorchestrator import _preloading


@pytest.mark.asyncio
async def test_preloading_empty():
    async def empty_gen():
        return
        yield

    g = _preloading(empty_gen())
    async for i in g:
        assert False
    with pytest.raises(StopAsyncIteration):
        await g.__anext__()


@pytest.mark.asyncio
async def test_preloading_single():
    async def single_gen():
        yield 1

    g = _preloading(single_gen())
    assert await g.__anext__() == 1
    with pytest.raises(StopAsyncIteration):
        await g.__anext__()


@pytest.mark.asyncio
async def test_preloading_many():
    last_called = None

    async def gen():
        nonlocal last_called
        last_called = 1
        yield 1
        last_called = 2
        yield 2
        last_called = 3
        yield 3
        last_called = 4
        yield 4

    g = _preloading(gen())
    assert await g.__anext__() == 1
    # need a sleep call so that the background task gets a chance to run
    await asyncio.sleep(0)
    # assert that next item in underlying generator has been called
    assert last_called == 2
    assert await g.__anext__() == 2
    await asyncio.sleep(0)
    assert last_called == 3
    assert await g.__anext__() == 3
    await asyncio.sleep(0)
    assert last_called == 4
    assert await g.__anext__() == 4
    with pytest.raises(StopAsyncIteration):
        await g.__anext__()


@pytest.mark.asyncio
async def test_preloading_exception():
    async def gen():
        yield 1
        raise Exception()

    g = _preloading(gen())
    assert await g.__anext__() == 1
    await asyncio.sleep(0)
    with pytest.raises(Exception):
        await g.__anext__()
