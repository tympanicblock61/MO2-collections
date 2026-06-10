import time
import traceback
if __name__ == "__main__":

    try:
        import socket
        import sys

        from app.registry import register_self_as_handler
        from app.server import NxmHandlerServer
        from app.utils import get_server_port
        from app.packets import LinkPacket

        if len(sys.argv) > 1:
            try:
                with socket.create_connection(("localhost", get_server_port())) as s:
                    s.sendall(LinkPacket(sys.argv[1]).to_bytes())
            except ConnectionRefusedError:
                register_self_as_handler()
                server = NxmHandlerServer()
                server.run()
        else:
            register_self_as_handler()
            server = NxmHandlerServer()
            server.run()
    except Exception:
        traceback.print_exc()
        time.sleep(10000)