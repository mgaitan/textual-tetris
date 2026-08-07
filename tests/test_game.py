import asyncio
import json

from textual.containers import Container, Vertical
from websockets.asyncio.client import connect

from textris import (
    BOARD_WIDGET_WIDTH,
    LOCK_DELAY,
    PANEL_WIDGET_WIDTH,
    PlayerPane,
    TetrisApp,
    TetrisPiece,
    coords_to_matrix,
)


def test_piece_preview_matrix_uses_its_bounding_box() -> None:
    assert coords_to_matrix([(1, 1), (2, 1)]) == [[1, 1]]


def test_piece_rotation_can_be_undone() -> None:
    piece = TetrisPiece("T")
    initial_code = piece.code

    piece.rotate()
    piece.undo_rotate()

    assert piece.code == initial_code


def test_player_sidebar_is_narrower_than_the_board() -> None:
    assert PANEL_WIDGET_WIDTH < BOARD_WIDGET_WIDTH


def test_both_players_receive_independent_pieces_and_controls() -> None:
    async def run() -> None:
        app = TetrisApp(two_players=True)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert len(app.query(PlayerPane)) == 2
            first_piece = app.player_panes[1].board_widget.current_piece
            second_piece = app.player_panes[2].board_widget.current_piece

            assert first_piece is not None
            assert second_piece is not None
            assert first_piece is not second_piece

            first_x = first_piece.x
            second_x = second_piece.x
            await pilot.press("a", "right")

            assert first_piece.x == first_x - 1
            assert second_piece.x == second_x + 1

            player_two_interval = app.players[2].drop_interval
            app.on_piece_locked(1, 10)

            assert app.players[1].level == 2
            assert app.players[1].drop_interval < player_two_interval
            assert app.players[2].drop_interval == player_two_interval

    asyncio.run(run())


def test_single_player_mode_mounts_one_board() -> None:
    async def run() -> None:
        app = TetrisApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert set(app.players) == {1}
            assert set(app.player_panes) == {1}
            assert app.query_one("#board") is app.player_panes[1].board_widget
            assert app.query_one("#board-container", Container)
            assert app.query_one("#sidebar", Vertical)
            assert app.query_one("#next-piece-container", Container)
            assert app.query_one("#score-container", Container)
            assert not app.query(PlayerPane)

    asyncio.run(run())


def test_hard_drop_allows_a_grounded_piece_to_be_adjusted_before_locking() -> None:
    async def run() -> None:
        app = TetrisApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            board = app.player_panes[1].board_widget
            piece = board.current_piece

            assert piece is not None
            await pilot.press("space")
            assert board.current_piece is piece
            assert board.is_grounded()

            await pilot.pause(LOCK_DELAY / 2)
            assert board.current_piece is piece

            x = piece.x
            await pilot.press("left")
            assert piece.x == x - 1

            await pilot.pause(LOCK_DELAY / 2 + 0.1)
            assert board.current_piece is not piece

    asyncio.run(run())


def test_agent_mode_mounts_two_players() -> None:
    async def run() -> None:
        app = TetrisApp(network_mode="agent")
        async with app.run_test() as pilot:
            await pilot.pause()
            assert set(app.players) == {1, 2}
            assert len(app.query(PlayerPane)) == 2

    asyncio.run(run())


def test_server_sends_state_and_accepts_remote_input() -> None:
    async def run() -> None:
        app = TetrisApp(network_mode="server", server_port=0)
        async with app.run_test(size=(181, 59)) as pilot:
            await pilot.pause()
            async with connect(f"ws://127.0.0.1:{app.server_port}") as websocket:
                initial = json.loads(await websocket.recv())
                assert initial["type"] == "state"
                assert set(initial["players"]) == {"1", "2"}

                piece = app.player_panes[2].board_widget.current_piece
                assert piece is not None
                initial_x = piece.x
                await websocket.send(json.dumps({"type": "input", "action": "left"}))
                await pilot.pause()
                await websocket.recv()
                piece = app.player_panes[2].board_widget.current_piece
                assert piece is not None
                assert piece.x == initial_x - 1

    asyncio.run(run())
