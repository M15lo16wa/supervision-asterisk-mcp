# src/domain/ports.py
from abc import ABC, abstractmethod
from src.domain.entities import Channel

class AriGateway(ABC):
    @abstractmethod
    async def list_channels(self) -> list[Channel]:
        ...

    @abstractmethod
    async def hangup(self, channel_id: str) -> None:
        ...
