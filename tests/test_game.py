import asyncio

from textual.containers import Container, Vertical

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
            app.on_piece_locked(1, 10, first_piece.piece_id)

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


def test_remote_client_uses_arrow_and_space_keys() -> None:
    client_keys = {binding[0] for binding in TetrisApp.REMOTE_CLIENT_BINDINGS}

    assert client_keys == {"left", "right", "down", "up", "space"}


def test_local_player_names_are_visible_and_changeable() -> None:
    async def run() -> None:
        app = TetrisApp(two_players=True)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._submit_name("Ada")
            app._submit_player_two_name("Grace")

            assert str(app.player_panes[1].name_widget.render()) == "Ada"
            assert str(app.player_panes[2].name_widget.render()) == "Grace"

    asyncio.run(run())
