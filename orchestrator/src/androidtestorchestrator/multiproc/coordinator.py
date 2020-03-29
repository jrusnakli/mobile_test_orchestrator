import multiprocessing.connection as connection
import socket

from typing import Optional, List

from ..device import Device, DeviceSet


class Coordinator:

    def __init__(self, client_count: int):
        self._hostaddr = socket.gethostbyname(socket.gethostname())
        self._listener: Optional[connection.Listener] = None
        self._client_count = client_count
        self._clients: List[connection.Connection] = []
        self._devices: List[Device] = []

    def start(self):
        self._listener = connection.Listener(self._hostaddr)
        while len(self._clients) < self._client_count:
            new_connection = self._listener.accept()
            self._clients.append(new_connection)
            self._devices += new_connection.recv()

    def device_set(self) -> DeviceSet:
        return DeviceSet(self._devices)

    def stop(self):
        self._listener.close()
