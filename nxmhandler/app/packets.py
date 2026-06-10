import json
from abc import ABC, abstractmethod
from typing import Type, Optional, Callable, List

PACKET_REGISTRY: dict[str, Type["Packet"]] = {}

def decode_packet(line: str) -> Optional["Packet"]:
    line = line.strip()
    if not line:
        return None

    if " " in line:
        packet_type, payload = line.split(" ", 1)
    else:
        packet_type, payload = line, ""

    cls = PACKET_REGISTRY.get(packet_type)
    if not cls:
        return None

    try:
        return cls.decode(payload)
    except Exception:
        return None

class Packet(ABC):
    PACKET_TYPE: str = "BASE"

    @abstractmethod
    def encode(self) -> str:
        pass

    @classmethod
    @abstractmethod
    def decode(cls, payload: str) -> "Packet":
        pass

    def to_bytes(self) -> bytes:
        return f"{self.PACKET_TYPE} {self.encode()}".strip().encode() + b"\n"


def register_packet(name: str) -> Callable[[Type[Packet]], Type[Packet]]:
    def decorator(cls: Type[Packet]) -> Type[Packet]:
        cls.PACKET_TYPE = name
        PACKET_REGISTRY[name] = cls
        return cls
    return decorator

@register_packet("REGISTER_HANDLER")
class RegisterHandlerPacket(Packet):
    def __init__(self, games: List[str] | None, types: List[str] | None):
        super().__init__()
        self.games = games
        self.types = types

    def encode(self) -> str:
        return json.dumps({
            'games': self.games,
            'types': self.types
        },indent=None)

    @classmethod
    def decode(cls, payload: str) -> 'RegisterHandlerPacket':
        data = json.loads(payload)
        return cls(data.get("games", []), data.get("types", []))

@register_packet("LINK")
class LinkPacket(Packet):
    def __init__(self, url: str):
        self.url = url

    def encode(self) -> str:
        return self.url

    @classmethod
    def decode(cls, payload: str) -> "LinkPacket":
        return cls(payload.strip())

@register_packet("PING")
class PingPacket(Packet):
    def encode(self) -> str:
        return ""

    @classmethod
    def decode(cls, payload: str) -> "PingPacket":
        return cls()