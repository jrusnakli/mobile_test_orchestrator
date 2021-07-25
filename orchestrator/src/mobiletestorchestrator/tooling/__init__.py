from multiprocessing.managers import BaseProxy, BaseManager
from typing import Any

from ..device import Device
from ..reporting import TestExecutionListener


class ProxyManager(BaseManager):
    pass


class _Proxy(BaseProxy):

    @classmethod
    def register(cls, item: type) -> None:
        ProxyManager.register(item.__name__, item)

    @classmethod
    def getproxy(cls, item: type) -> Any:
        if not hasattr(cls, '_manager'):
            cls._manager = ProxyManager()  # type: ignore
        return getattr(cls._manager, item.name)  # type: ignore


_Proxy.register(Device)
_Proxy.register(TestExecutionListener)
