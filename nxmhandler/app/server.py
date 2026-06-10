import atexit
import configparser
import os
import socket
import subprocess
import threading
import traceback

from .packets import decode_packet, LinkPacket, RegisterHandlerPacket
from .utils import INI_PATH, parse_nxm_url, UnknownNxmUrl


class RegisteredHandler:
    def __init__(self, conn: socket.socket):
        self.conn = conn
        self.games: set[str] = set()
        self.types: set[str] = set()

class ClientConnection(threading.Thread):
    def __init__(self, conn: socket.socket, addr, handler: "ServerHandler"):
        super().__init__(daemon=True)
        self.conn = conn
        self.addr = addr
        self.handler = handler



    def run(self):
        try:
            with self.conn:
                file = self.conn.makefile("r")
                line = file.readline()
                print(line)
                packet = decode_packet(line)
                print(packet)
                if isinstance(packet, LinkPacket):
                    self.handler.process_link(packet.url)
                elif isinstance(packet, RegisterHandlerPacket):
                    registered = RegisteredHandler(self.conn)
                    registered.games = packet.games
                    registered.types = packet.types
                    self.handler.register_output_handler(registered)
                    print(f"[+] Registered handler {self.addr} (games={registered.games}, types={registered.types})")

                    while True:
                        if file.readline() == '':
                            break
        except Exception as e:
            print(f"[!] Connection error from {self.addr}: {e}")

class ServerHandler:
    def __init__(self):
        self.lock = threading.Lock()
        self.output_handlers: list[RegisteredHandler] = []

    def process_link(self, link: str):
        self.send_to_registered_handlers(link)
        print("sending to ini handlers: hmm")
        self.send_to_ini_handlers(link)

    def register_output_handler(self, handler: RegisteredHandler):
        with self.lock:
            self.output_handlers.append(handler)


    def send_to_registered_handlers(self, link: str):
        print(link)
        try:
            nxm = parse_nxm_url(link)
            print(nxm)
            if isinstance(nxm, UnknownNxmUrl):
                return
        except Exception:
            traceback.print_exc()
            return

        with self.lock:
            for h in self.output_handlers[:]:
                print(h)
                print(nxm.game)
                print(h.games)

                try:
                    if h.games and nxm.game not in h.games and len(h.games) >= 0:
                        continue
                    if h.types and (nxm.path[0] not in h.types) and len(h.types) >= 0:
                        continue
                    print("sending to: ",h)
                    h.conn.sendall(LinkPacket(link).to_bytes())
                except Exception as e:
                    print(f"[!] Failed to send to handler: {e}")
                    self.output_handlers.remove(h)

    def send_to_ini_handlers(self, link: str):
        with self.lock:
            if not os.path.exists(INI_PATH):
                print("ini doesnt exist")
                return
            try:
                nxm = parse_nxm_url(link)
                if isinstance(nxm, UnknownNxmUrl):
                    print("link is unknown")
                    return
            except Exception:
                return

            config = configparser.ConfigParser()
            config.optionxform = str
            config.read(INI_PATH)

            size = int(config["handlers"].get("size", "0"))
            for i in range(1, size + 1):
                prefix = f"{i}\\"
                types = [t.strip().lower() for t in config["handlers"].get(prefix + "types", "").strip('"').split(',') if t]
                games = [g.strip().lower() for g in config["handlers"].get(prefix + "games", "").strip('"').split(',') if g]

                if types and nxm.path[0] not in types:
                    continue

                if len(games)>0 and nxm.game in games or len(games) == 0: 
                    exe = config["handlers"].get(prefix + "executable", "").strip('"')
                    args = config["handlers"].get(prefix + "arguments", "").strip()
                    full_cmd = f'"{exe}" {args} "{link}"'
                    print(f"[>] Launching: {full_cmd}")
                    subprocess.Popen(full_cmd)
                


class NxmHandlerServer(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.handler = ServerHandler()
        self.config = configparser.ConfigParser()
        self.config.optionxform = str
        atexit.register(self.cleanup)

    def run(self):
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("localhost", 0))
        s.listen(5)
        port = s.getsockname()[1]

        self.config.read(INI_PATH)
        self.config["current_config"] = {"port": str(port)}

        with open(INI_PATH, "w") as ini:
            self.config.write(ini)

        print(f"[+] NXMHandler server listening on port {port}")

        while True:
            conn, addr = s.accept()
            print(conn)
            print(addr)
            ClientConnection(conn, addr, self.handler).start()

    def cleanup(self):
        self.config.remove_section("current_config")
        with open(INI_PATH, "w") as ini:
            self.config.write(ini)