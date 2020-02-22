from contextlib import suppress

import asyncio
import logging

from abc import ABC, abstractmethod
from asyncio import Task, Future
from typing import Optional

log = logging.getLogger(__name__)


class StopWatch(ABC):
    """
    Interface for marking start and end positions (for example, of a test)
    """

    @abstractmethod
    def mark_start(self, name: str) -> None:
        """
        mark start of some activity
        :param name:  name of activity
        """

    @abstractmethod
    def mark_end(self, name: str) -> None:
        """
        mark end of activity
        :param name: name of activity
        """


class Timer(StopWatch):
    """
    A one-time timer used to raise exception if a task is taking too long, viable only in the context
    of a running asyncio loop (in other words, mark_start and mark_end are expected to be called
    within a running EventLoop)

    This is helpful when the start and stop indicators are from output of a command running on a remote device,
    where typical direct asyncio timer logic inline to the code isn't applicable
    """

    def __init__(self, duration: float) -> None:
        """
        :param duration: duration at end of which timer will expire and raise an `asyncior.TimeoutError`
        """
        self._future: Optional[Future[bool]] = asyncio.get_running_loop().create_future()
        self._timeout = duration
        self._task: Optional[Task[None]] = None

    def mark_end(self, name: str) -> None:
        """
        Mark the end of an activity and cancel timer
        :param name: name associated with the activity
        """
        # if we are in a timer, set the future to True to mark as done
        # thiw will release the timer task and prevent a TimeoutError
        if self._future:
            self._future.set_result(True)
        # if there is a current task, ensure it is not active:
        if self._task:
            with suppress(Exception):
                self._task.cancel()

    def mark_start(self, name: str) -> None:
        """
        Mark the start of an activity by creating a timer (within the context of a running event loop)

        :param name: name associated with the activity
        :raises: asyncio.TimeoutError if timer expires before end is marked
        """
        async def timer() -> None:
            assert self._future is not None, "Internal error: timer was run multiple times"
            try:
                await asyncio.wait_for(self._future, timeout=self._timeout)
            except asyncio.TimeoutError:
                log.error("Task %s timed out" % name)
                for task in asyncio.all_tasks():
                    if task != self._task:
                        task.cancel()
                raise
            self._future = None  # timer is used up

        # cancel any existing task if needed(restart timer essentially)
        if self._task:
            with suppress(Exception):
                self._task.cancel()
        # create a new future and start the timer:
        self._future = asyncio.get_running_loop().create_future()
        # in principle, a loop is already running, so just add another task to process:
        self._task = asyncio.create_task(timer())
