from textris import TetrisPiece, coords_to_matrix


def test_piece_preview_matrix_uses_its_bounding_box() -> None:
    assert coords_to_matrix([(1, 1), (2, 1)]) == [[1, 1]]


def test_piece_rotation_can_be_undone() -> None:
    piece = TetrisPiece("T")
    initial_code = piece.code

    piece.rotate()
    piece.undo_rotate()

    assert piece.code == initial_code
