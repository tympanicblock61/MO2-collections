import logging
import os.path
import sys
from typing import List

import mobase

from .packets import RegisterHandlerPacket, decode_packet, LinkPacket
from .utils import parse_nxm_url, NxmUrl, UnknownNxmUrl, get_server_port, PYTHON_ENV

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger()

if "PyQt6" in sys.modules:
    # noinspection PyUnresolvedReferences
    from PyQt6.QtCore import QThread, QObject, QMetaObject, Qt, Q_ARG, pyqtSlot

class QtUrlRouter(QObject):
    def __init__(self, handler: "NXMHandler"):
        super().__init__()
        self.handler = handler

    # noinspection PyProtectedMember
    @pyqtSlot(str)
    def route_url(self, url: str):
        self.handler._url_processor(url)

import time
import subprocess
import socket
import os

debug = False

class ConnectionThread(QThread):
    def __init__(self, callback_obj: QObject, games: List[str], types: List[str]):
        super().__init__()
        self.callback_obj = callback_obj
        self.games = games or []
        self.types = types or []

    def wait_for_server(self, port, timeout=5.0):
        start = time.time()
        while time.time() - start < timeout:
            try:
                with socket.create_connection(("localhost", port), timeout=1):
                    return True
            except OSError:
                time.sleep(0.1)
        return False

    def try_connect(self):
        port = get_server_port()
        try:
            with socket.create_connection(("localhost", port)) as s:
                logger.info(RegisterHandlerPacket(self.games, self.types).to_bytes())
                s.sendall(RegisterHandlerPacket(self.games, self.types).to_bytes())
                logger.info(f"[+] Registered as remote handler on port {port}. Waiting for links...")

                while True:
                    data = s.recv(1024)
                    if not data:
                        logger.info("[!] Disconnected from server.")
                        break
                    packet = decode_packet(data.decode())
                    if isinstance(packet, LinkPacket):
                        QMetaObject.invokeMethod(
                            self.callback_obj,
                            "route_url",
                            Qt.ConnectionType.QueuedConnection,
                            Q_ARG(str, packet.url)
                        )
                return True
        except Exception as e:
            logger.info(f"[!] Connection failed: {e}")
            return False

    def run(self):
        initial_port = get_server_port()

        if not self.try_connect():
            logger.info("[*] Attempting to start handler server...")

            if debug:
                py = PYTHON_ENV.get('python')
            else:
                py = PYTHON_ENV.get('pythonw')

            subprocess.Popen([
                py,
                os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "__main__.py"))
            ])

            timeout = 5.0
            start_time = time.time()
            while time.time() - start_time < timeout:
                current_port = get_server_port()
                if current_port != initial_port and self.wait_for_server(current_port, timeout=0.5):
                    logger.info(f"[*] Server started on new port {current_port}, reconnecting...")
                    self.try_connect()
                    return

                time.sleep(0.1)

            logger.error("[!] Failed to start handler server.")


class NXMHandler(mobase.IPlugin):
    def __init__(self, games: List[str] = None, types: List[str] = None):
        super(NXMHandler, self).__init__()
        self.qt_helper = QtUrlRouter(self)
        self._connection_thread = None
        self._nxm_games = games or []
        self._nxm_types = types or []

    def author(self):
        pass

    def description(self):
        pass

    def init(self, organizer):
        thread = ConnectionThread(self.qt_helper, self._nxm_games, self._nxm_types)
        thread.start()
        self._connection_thread = thread

    def name(self):
        pass

    def settings(self):
        pass

    def version(self):
        pass

    def _url_processor(self, url: str):
        nxm = parse_nxm_url(url)
        if isinstance(nxm, UnknownNxmUrl):
            logger.info(f"[!] Ignored unknown URL: {url}")
            return
        self.nxm_receive_url(nxm)

    # noinspection PyMethodMayBeStatic
    def nxm_receive_url(self, url: NxmUrl):
        logger.info(f"[+] Received NXM link: {url.raw_url}")
