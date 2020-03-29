import inspect

from multiprocessing.managers import BaseProxy, BaseManager

from ..device import Device
from ..reporting import TestExecutionListener


class ProxyManager(BaseManager):
    pass


class _Proxy(BaseProxy):

    @classmethod
    def register(cls, item: type):
        ProxyManager.register(item.__name__, item)

    @classmethod
    def getproxy(cls, item: type):
        if not hasattr(cls, '_manager'):
            cls._manager = ProxyManager()
        return getattr(cls._manager, item.name)


_Proxy.register(Device)
_Proxy.register(TestExecutionListener)