from textris.game import TetrisMatch, TetrisPiece


def test_match_rules_are_independent_from_textual() -> None:
    match = TetrisMatch()

    events = match.start({1: "Ada", 2: "Grace"})

    assert events == []
    assert match.status == "running"
    assert match.players[1].name == "Ada"
    piece = match.players[1].board.current_piece
    assert piece is not None
    initial_x = piece.x

    accepted, events = match.apply_action(1, "left")

    assert accepted
    assert events == []
    assert piece.x == initial_x - 1

    accepted, events = match.apply_action(1, "drop")

    assert accepted
    assert [event.type for event in events] == ["piece_locked"]
    assert match.players[1].score == 10


def test_match_reports_game_over_from_pure_board_state() -> None:
    match = TetrisMatch()
    match.start({1: "Ada", 2: "Grace"})
    state = match.players[1]
    state.next_piece = TetrisPiece("O")
    for x, y in state.next_piece.blocks:
        state.board.cells[y][x] = "occupied"

    accepted, events = match.apply_action(1, "drop")

    assert accepted
    assert events[-1].type == "game_over"
    assert events[-1].payload["loser"] == 1
    assert match.status == "game_over"
