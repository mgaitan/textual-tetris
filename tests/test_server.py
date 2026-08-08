import asyncio
import json
from collections.abc import Callable

from websockets.asyncio.client import ClientConnection, connect

from textris import TetrisApp
from textris.server import PROTOCOL, TetrisServer


async def receive_until(
    websocket: ClientConnection,
    message_type: str,
    predicate: Callable[[dict], bool] | None = None,
) -> dict:
    deadline = asyncio.get_running_loop().time() + 2
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        message = json.loads(await asyncio.wait_for(websocket.recv(), remaining))
        if message.get("type") == message_type and (predicate is None or predicate(message)):
            return message


def test_headless_server_supports_agents_names_chat_and_queue() -> None:
    async def run() -> None:
        server = TetrisServer("127.0.0.1", 0)
        async with server.running():
            url = f"ws://127.0.0.1:{server.port}"
            async with connect(url) as first:
                welcome_one = json.loads(await first.recv())
                assert welcome_one["protocol"] == PROTOCOL
                assert welcome_one["role"] == "player"
                assert welcome_one["player_id"] == 1
                assert welcome_one["messages"] == ["input", "name", "chat", "join"]
                assert "roster" in welcome_one["schemas"]["state"]
                assert set(welcome_one["pieces"]) == {"O", "I", "J", "L", "T", "Z", "S"}
                waiting = await receive_until(first, "state")
                assert waiting["status"] == "waiting"

                await first.send(json.dumps({"type": "name", "name": "Ada"}))
                renamed = await receive_until(first, "player_renamed")
                assert renamed["name"] == "Ada"

                async with connect(url) as second:
                    welcome_two = json.loads(await second.recv())
                    assert welcome_two["role"] == "player"
                    assert welcome_two["player_id"] == 2
                    running = await receive_until(first, "state", lambda message: message["status"] == "running")
                    assert running["players"]["1"]["name"] == "Ada"
                    current = running["players"]["1"]["current_piece"]
                    assert current is not None

                    async with connect(url) as spectator:
                        welcome_three = json.loads(await spectator.recv())
                        assert welcome_three["role"] == "spectator"
                        assert welcome_three["queue_position"] == 1

                        await spectator.send(json.dumps({"type": "chat", "message": "good luck"}))
                        chat = await receive_until(first, "chat")
                        assert chat["message"] == "good luck"
                        assert chat["sender"]["name"] == "Player 3"

                        await spectator.send(json.dumps({"type": "input", "id": 7, "action": "left"}))
                        rejected = await receive_until(spectator, "ack", lambda message: message["id"] == 7)
                        assert rejected["accepted"] is False

                        await first.send(json.dumps({"type": "input", "id": 8, "action": "left"}))
                        accepted = await receive_until(first, "ack", lambda message: message["id"] == 8)
                        assert accepted["accepted"] is True
                        moved = await receive_until(
                            first,
                            "state",
                            lambda message: message["players"]["1"]["current_piece"]["x"] == current["x"] - 1,
                        )
                        assert moved["status"] == "running"

                    await asyncio.sleep(0)

                await asyncio.sleep(0)

    asyncio.run(run())


def test_next_queued_client_is_promoted_after_disconnect() -> None:
    async def run() -> None:
        server = TetrisServer("127.0.0.1", 0)
        async with server.running():
            url = f"ws://127.0.0.1:{server.port}"
            async with connect(url) as first, connect(url) as second, connect(url) as queued:
                await receive_until(first, "state", lambda message: message["status"] == "running")
                await receive_until(queued, "state")

                await second.close()

                role = await receive_until(
                    queued,
                    "role",
                    lambda message: message["role"] == "player" and message["player_id"] == 2,
                )
                assert role["queue_position"] is None
                restarted = await receive_until(queued, "state", lambda message: message["status"] == "running")
                assert restarted["roster"][0]["player_id"] == 1
                assert any(member["player_id"] == 2 for member in restarted["roster"])

    asyncio.run(run())


def test_watch_only_client_never_occupies_or_queues_for_a_player_slot() -> None:
    async def run() -> None:
        server = TetrisServer("127.0.0.1", 0)
        async with server.running():
            url = f"ws://127.0.0.1:{server.port}"
            async with connect(f"{url}?role=spectator") as watcher:
                welcome = json.loads(await watcher.recv())
                assert welcome["role"] == "spectator"
                assert welcome["player_id"] is None
                assert welcome["queue_position"] is None
                assert welcome["watch_only"] is True
                assert welcome["spectator_query"] == "role=spectator"
                assert welcome["name"] == "Spectator 1"

                await watcher.send(json.dumps({"type": "join"}))
                await watcher.send(json.dumps({"type": "chat", "message": "watching"}))
                await receive_until(watcher, "chat")

                async with connect(url) as first, connect(url) as second:
                    welcome_one = json.loads(await first.recv())
                    welcome_two = json.loads(await second.recv())
                    assert welcome_one["player_id"] == 1
                    assert welcome_one["name"] == "Player 1"
                    assert welcome_two["player_id"] == 2
                    assert welcome_two["name"] == "Player 2"

                    running = await receive_until(watcher, "state", lambda message: message["status"] == "running")
                    observer = next(member for member in running["roster"] if member["watch_only"])
                    assert observer["player_id"] is None
                    assert observer["queue_position"] is None

                    await second.close()
                    deadline = asyncio.get_running_loop().time() + 2
                    while len(server.active_players) != 1:
                        assert asyncio.get_running_loop().time() < deadline
                        await asyncio.sleep(0.01)
                    assert all(not session.watch_only for session in server.active_players.values())

    asyncio.run(run())


def test_next_queued_client_is_promoted_after_game_over() -> None:
    async def run() -> None:
        server = TetrisServer("127.0.0.1", 0)
        async with server.running():
            url = f"ws://127.0.0.1:{server.port}"
            async with connect(url) as first, connect(url) as second, connect(url) as queued:
                await receive_until(first, "state", lambda message: message["status"] == "running")
                await receive_until(queued, "state")
                losing_state = server.match.players[2]
                for x, y in losing_state.next_piece.blocks:
                    losing_state.board.cells[y][x] = "occupied"

                await second.send(json.dumps({"type": "input", "action": "drop"}))

                role = await receive_until(
                    queued,
                    "role",
                    lambda message: message["role"] == "player" and message["player_id"] == 2,
                )
                assert role["queue_position"] is None
                loser_role = await receive_until(
                    second,
                    "role",
                    lambda message: message["role"] == "spectator",
                )
                assert loser_role["player_id"] is None
                restarted = await receive_until(queued, "state", lambda message: message["status"] == "running")
                assert any(member["player_id"] == 1 for member in restarted["roster"])

    asyncio.run(run())


def test_textual_client_receives_role_and_visible_name() -> None:
    async def run() -> None:
        server = TetrisServer("127.0.0.1", 0)
        async with server.running():
            url = f"ws://127.0.0.1:{server.port}"
            async with connect(url):
                app = TetrisApp(network_mode="client", connect_url=url, player_name="Ada")
                async with app.run_test(size=(181, 59)) as pilot:
                    deadline = asyncio.get_running_loop().time() + 2
                    while app.controlled_player != 2 or app.players[2].name != "Ada":
                        assert asyncio.get_running_loop().time() < deadline
                        await pilot.pause(0.05)
                    assert app.network_role == "player"
                    assert not app.query_one("#game-container").has_class("waiting")
                    assert app.player_panes[2].score_widget.player_name == "Ada"

    asyncio.run(run())


def test_embedded_server_waits_for_a_remote_player() -> None:
    async def run() -> None:
        server = TetrisServer("127.0.0.1", 0)
        app = TetrisApp(network_mode="client", player_name="tin", embedded_server=server)
        async with app.run_test(size=(181, 59)) as pilot:
            deadline = asyncio.get_running_loop().time() + 2
            while app.controlled_player != 1 or app.players[1].name != "tin":
                assert asyncio.get_running_loop().time() < deadline
                await pilot.pause(0.05)

            assert server.match.status == "waiting"
            assert app.query_one("#game-container").has_class("waiting")

            async with connect(f"ws://127.0.0.1:{server.port}"):
                deadline = asyncio.get_running_loop().time() + 2
                while server.match.status != "running" or app.query_one("#game-container").has_class("waiting"):
                    assert asyncio.get_running_loop().time() < deadline
                    await pilot.pause(0.05)

    asyncio.run(run())


def test_embedded_watch_only_server_leaves_both_player_slots_available() -> None:
    async def run() -> None:
        server = TetrisServer("127.0.0.1", 0)
        app = TetrisApp(network_mode="client", embedded_server=server, just_watch=True)
        async with app.run_test(size=(181, 59)) as pilot:
            deadline = asyncio.get_running_loop().time() + 2
            while app.client_id is None:
                assert asyncio.get_running_loop().time() < deadline
                await pilot.pause(0.05)

            assert app.network_role == "spectator"
            assert app.controlled_player is None
            assert server.active_players == {}

            url = f"ws://127.0.0.1:{server.port}"
            async with connect(url) as first, connect(url) as second:
                assert json.loads(await first.recv())["player_id"] == 1
                assert json.loads(await second.recv())["player_id"] == 2
                deadline = asyncio.get_running_loop().time() + 2
                while server.match.status != "running" or app.query_one("#game-container").has_class("waiting"):
                    assert asyncio.get_running_loop().time() < deadline
                    await pilot.pause(0.05)

    asyncio.run(run())


def test_loser_can_join_again_when_no_challenger_is_waiting() -> None:
    async def run() -> None:
        server = TetrisServer("127.0.0.1", 0)
        async with server.running():
            url = f"ws://127.0.0.1:{server.port}"
            async with connect(url) as winner, connect(url) as loser:
                await receive_until(winner, "state", lambda message: message["status"] == "running")
                losing_state = server.match.players[2]
                for x, y in losing_state.next_piece.blocks:
                    losing_state.board.cells[y][x] = "occupied"
                await loser.send(json.dumps({"type": "input", "action": "drop"}))
                await receive_until(loser, "role", lambda message: message["role"] == "spectator")

                await loser.send(json.dumps({"type": "join"}))

                role = await receive_until(
                    loser,
                    "role",
                    lambda message: message["role"] == "player" and message["player_id"] == 2,
                )
                assert role["queue_position"] is None
                restarted = await receive_until(loser, "state", lambda message: message["status"] == "running")
                assert restarted["match_id"] > 1

    asyncio.run(run())
