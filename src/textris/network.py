from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from websockets.asyncio.client import ClientConnection, connect

MessageHandler = Callable[[dict[str, object]], Awaitable[None]]


class TetrisClient:
    """Small WebSocket transport used by interactive and automated clients."""

    def __init__(self, url: str, *, watch_only: bool = False) -> None:
        self.url = self._watch_only_url(url) if watch_only else url
        self.websocket: ClientConnection | None = None

    async def run(self, handler: MessageHandler) -> None:
        async with connect(self.url) as websocket:
            self.websocket = websocket
            try:
                async for raw_message in websocket:
                    try:
                        payload = json.loads(raw_message)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if isinstance(payload, dict):
                        await handler(payload)
            finally:
                self.websocket = None

    async def send(self, payload: dict[str, object]) -> None:
        if self.websocket is not None:
            await self.websocket.send(json.dumps(payload))

    @staticmethod
    def _watch_only_url(url: str) -> str:
        parts = urlsplit(url)
        query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key != "role"]
        query.append(("role", "spectator"))
        return urlunsplit(parts._replace(query=urlencode(query)))
