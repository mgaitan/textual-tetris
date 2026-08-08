from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from itertools import count
from urllib.parse import parse_qs, urlsplit

from websockets.asyncio.server import ServerConnection, serve

from .game import NETWORK_ACTIONS, GameEvent, PlayerState, TetrisMatch, piece_catalog

PROTOCOL = "textual-tetris/v2"
MAX_NAME_LENGTH = 24
MAX_CHAT_LENGTH = 200


@dataclass(eq=False, slots=True)
class ClientSession:
    websocket: ServerConnection
    client_id: int
    name: str
    role: str = "spectator"
    player_id: int | None = None
    queued: bool = False
    watch_only: bool = False
    custom_name: bool = False


class TetrisServer:
    """Authoritative headless server for one rotating two-player match."""

    def __init__(self, host: str = "0.0.0.0", port: int = 8765) -> None:
        self.host = host
        self.port = port
        self.match = TetrisMatch()
        self.clients: dict[ServerConnection, ClientSession] = {}
        self.active_players: dict[int, ClientSession] = {}
        self.waiting_players: deque[ClientSession] = deque()
        self.revision = 0
        self.match_id = 0
        self._client_ids = count(1)
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()

    @asynccontextmanager
    async def running(self) -> AsyncIterator[TetrisServer]:
        """Run until the surrounding context exits."""
        async with serve(self._handle_client, self.host, self.port) as websocket_server:
            self.port = websocket_server.sockets[0].getsockname()[1]
            yield self

    async def serve_forever(self) -> None:
        async with self.running():
            print(f"Server listening on ws://127.0.0.1:{self.port}")
            print("Waiting for two players. Press Ctrl+C to stop.")
            await asyncio.Future()

    async def _handle_client(self, websocket: ServerConnection) -> None:
        client_id = next(self._client_ids)
        watch_only = self._watch_only(websocket)
        session = ClientSession(
            websocket,
            client_id,
            f"Spectator {client_id}" if watch_only else f"Player {client_id}",
            watch_only=watch_only,
        )
        async with self._lock:
            self.clients[websocket] = session
            self._place_new_client(session)
            await self._send(session, self._welcome_payload(session))
            await self._send_role(session)
            if len(self.active_players) == 2 and self.match.status != "running":
                await self._start_match()
            else:
                if self.match.status != "running":
                    self._prepare_waiting_state()
                await self._broadcast_state()
        try:
            async for raw_message in websocket:
                await self._handle_message(session, raw_message)
        finally:
            await self._disconnect(session)

    async def _handle_message(self, session: ClientSession, raw_message: str | bytes) -> None:
        try:
            payload = json.loads(raw_message)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        if not isinstance(payload, dict):
            return
        message_type = payload.get("type")
        async with self._lock:
            if message_type == "input":
                await self._handle_input(session, payload)
            elif message_type == "name":
                await self._handle_name(session, payload.get("name"))
            elif message_type == "chat":
                await self._handle_chat(session, payload.get("message"))
            elif message_type == "join":
                await self._join_queue(session)

    async def _handle_input(self, session: ClientSession, payload: dict[str, object]) -> None:
        accepted = False
        events = []
        if session.player_id is not None:
            accepted, events = self.match.apply_action(session.player_id, payload.get("action"))
        if accepted:
            for event in events:
                await self._broadcast_event(event.type, event.payload)
            await self._broadcast_state()
        if "id" in payload:
            await self._send(
                session,
                {
                    "type": "ack",
                    "id": payload["id"],
                    "accepted": accepted,
                    "revision": self.revision,
                },
            )
        loser = self._loser(events)
        if loser is not None:
            await self._finish_match(loser)

    async def _handle_name(self, session: ClientSession, value: object) -> None:
        if not isinstance(value, str) or not (name := self._clean_name(value)):
            return
        session.name = name
        session.custom_name = True
        if session.player_id in self.match.players:
            self.match.players[session.player_id].name = name
        await self._broadcast_event(
            "player_renamed",
            {"client_id": session.client_id, "player_id": session.player_id, "name": name},
        )
        await self._broadcast_state()

    async def _handle_chat(self, session: ClientSession, value: object) -> None:
        if not isinstance(value, str) or not (message := " ".join(value.split())):
            return
        message = message[:MAX_CHAT_LENGTH]
        await self._broadcast_event(
            "chat",
            {
                "sender": {
                    "client_id": session.client_id,
                    "name": session.name,
                    "role": session.role,
                    "player_id": session.player_id,
                },
                "message": message,
            },
        )

    async def _join_queue(self, session: ClientSession) -> None:
        if session.watch_only or session.player_id is not None or session.queued:
            return
        if len(self.active_players) < 2:
            vacant_id = next(player_id for player_id in (1, 2) if player_id not in self.active_players)
            self._assign_player(session, vacant_id)
            await self._send_role(session)
            if len(self.active_players) == 2:
                await self._start_match()
            else:
                await self._broadcast_state()
            return
        session.queued = True
        self.waiting_players.append(session)
        await self._refresh_roles()
        await self._broadcast_state()

    async def _disconnect(self, session: ClientSession) -> None:
        async with self._lock:
            if session.websocket not in self.clients:
                return
            del self.clients[session.websocket]
            was_active = session.player_id is not None
            if session.queued:
                self.waiting_players.remove(session)
            if session.player_id is not None:
                del self.active_players[session.player_id]
            session.player_id = None
            session.queued = False
            if was_active:
                self.match.status = "waiting"
                self.match_id += 1
                self._promote_waiting_player()
                if len(self.active_players) == 2:
                    await self._start_match()
                else:
                    await self._refresh_roles()
                    await self._broadcast_state()
            else:
                await self._refresh_roles()
                await self._broadcast_state()

    def _place_new_client(self, session: ClientSession) -> None:
        if session.watch_only:
            return
        for player_id in (1, 2):
            if player_id not in self.active_players:
                self._assign_player(session, player_id)
                return
        session.queued = True
        self.waiting_players.append(session)

    def _assign_player(self, session: ClientSession, player_id: int) -> None:
        if not session.custom_name:
            session.name = f"Player {player_id}"
        session.role = "player"
        session.player_id = player_id
        session.queued = False
        self.active_players[player_id] = session

    def _promote_waiting_player(self) -> ClientSession | None:
        if not self.waiting_players:
            return None
        session = self.waiting_players.popleft()
        vacant_id = next(player_id for player_id in (1, 2) if player_id not in self.active_players)
        self._assign_player(session, vacant_id)
        return session

    async def _finish_match(self, loser_id: int) -> None:
        loser = self.active_players.pop(loser_id, None)
        self.match.status = "waiting"
        self.match_id += 1
        if loser is not None:
            loser.role = "spectator"
            loser.player_id = None
            loser.queued = False
            await self._send_role(loser)
        promoted = self._promote_waiting_player()
        if promoted is not None:
            await self._send_role(promoted)
        if len(self.active_players) == 2:
            await self._start_match()
            return
        await self._refresh_roles()
        await self._broadcast_state()

    async def _start_match(self) -> None:
        self.match_id += 1
        names = {player_id: session.name for player_id, session in self.active_players.items()}
        events = self.match.start(names)
        await self._refresh_roles()
        await self._broadcast_event("match_started", {"match_id": self.match_id})
        for event in events:
            await self._broadcast_event(event.type, event.payload)
        await self._broadcast_state()
        task = asyncio.create_task(self._game_loop(self.match_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _game_loop(self, match_id: int) -> None:
        loop = asyncio.get_running_loop()
        next_drop = {player_id: loop.time() + state.drop_interval for player_id, state in self.match.players.items()}
        while True:
            await asyncio.sleep(0.02)
            async with self._lock:
                if match_id != self.match_id or self.match.status != "running":
                    return
                now = loop.time()
                for player_id, state in self.match.players.items():
                    if now < next_drop[player_id]:
                        continue
                    events = self.match.tick(player_id)
                    next_drop[player_id] = now + state.drop_interval
                    for event in events:
                        await self._broadcast_event(event.type, event.payload)
                    await self._broadcast_state()
                    loser = self._loser(events)
                    if loser is not None:
                        await self._finish_match(loser)
                        return

    def _prepare_waiting_state(self) -> None:
        if self.match.players:
            self.match.status = "waiting"
            return
        self.match.players = {
            player_id: PlayerState(
                self.active_players[player_id].name if player_id in self.active_players else "Waiting"
            )
            for player_id in (1, 2)
        }
        self.match.status = "waiting"

    async def _refresh_roles(self) -> None:
        for session in self.clients.values():
            await self._send_role(session)

    async def _send_role(self, session: ClientSession) -> None:
        await self._send(
            session,
            {
                "type": "role",
                "role": session.role,
                "player_id": session.player_id,
                "queue_position": self._queue_position(session),
                "watch_only": session.watch_only,
                "revision": self.revision,
            },
        )

    def _welcome_payload(self, session: ClientSession) -> dict[str, object]:
        return {
            "type": "welcome",
            "revision": self.revision,
            "protocol": PROTOCOL,
            "client_id": session.client_id,
            "name": session.name,
            "role": session.role,
            "player_id": session.player_id,
            "queue_position": self._queue_position(session),
            "watch_only": session.watch_only,
            "spectator_query": "role=spectator",
            "actions": list(NETWORK_ACTIONS),
            "messages": ["input", "name", "chat", "join"],
            "events": [
                "state",
                "ack",
                "role",
                "piece_locked",
                "game_over",
                "match_started",
                "player_renamed",
                "chat",
            ],
            "pieces": piece_catalog(),
            "schemas": {
                "input": {"type": "input", "id": "<optional client-defined id>", "action": "<action>"},
                "name": {"type": "name", "name": f"<1-{MAX_NAME_LENGTH} character display name>"},
                "chat": {"type": "chat", "message": f"<1-{MAX_CHAT_LENGTH} character message>"},
                "join": {"type": "join", "description": "join the challenger queue as a spectator"},
                "state": {
                    "status": "waiting, running, or game_over",
                    "players": "player snapshots keyed by stable match slot 1 or 2",
                    "board": "20 rows of 10 cells, top to bottom; 0 means empty",
                    "current_piece": "piece_id, type, x, y, and rotation code, or null",
                    "next_piece": "piece_id, type, x, y, and rotation code",
                    "roster": "all connections with name, role, player_id, and queue_position",
                },
            },
        }

    def _state_payload(self) -> dict[str, object]:
        payload = self.match.payload()
        payload.update(
            {
                "type": "state",
                "revision": self.revision,
                "match_id": self.match_id,
                "roster": [
                    {
                        "client_id": session.client_id,
                        "name": session.name,
                        "role": session.role,
                        "player_id": session.player_id,
                        "queue_position": self._queue_position(session),
                        "watch_only": session.watch_only,
                    }
                    for session in self.clients.values()
                ],
            }
        )
        return payload

    async def _broadcast_state(self) -> None:
        self.revision += 1
        await self._broadcast(self._state_payload())

    async def _broadcast_event(self, event_type: str, payload: dict[str, object]) -> None:
        self.revision += 1
        await self._broadcast({"type": event_type, **payload, "revision": self.revision})

    async def _broadcast(self, payload: dict[str, object]) -> None:
        await asyncio.gather(*(self._send(session, payload) for session in tuple(self.clients.values())))

    @staticmethod
    async def _send(session: ClientSession, payload: dict[str, object]) -> None:
        with suppress(Exception):
            await session.websocket.send(json.dumps(payload))

    @staticmethod
    def _loser(events: list[GameEvent]) -> int | None:
        value = next((event.payload["loser"] for event in events if event.type == "game_over"), None)
        return value if isinstance(value, int) else None

    def _queue_position(self, session: ClientSession) -> int | None:
        if not session.queued:
            return None
        return list(self.waiting_players).index(session) + 1

    @staticmethod
    def _watch_only(websocket: ServerConnection) -> bool:
        request = websocket.request
        if request is None:
            return False
        query = parse_qs(urlsplit(request.path).query)
        return query.get("role") == ["spectator"]

    @staticmethod
    def _clean_name(value: str) -> str:
        return " ".join(value.split())[:MAX_NAME_LENGTH]
