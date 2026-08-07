from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass, field
from itertools import count


@dataclass(frozen=True, slots=True)
class PieceDefinition:
    color: str
    codes: tuple[str, ...]


PIECES = {
    "O": PieceDefinition("#FFD23F", ("56a9", "6a95", "a956", "956a")),
    "I": PieceDefinition("#49C6E5", ("4567", "26ae", "ba98", "d951")),
    "J": PieceDefinition("#3A86FF", ("0456", "2159", "a654", "8951")),
    "L": PieceDefinition("#FF9F1C", ("2654", "a951", "8456", "0159")),
    "T": PieceDefinition("#9D4EDD", ("1456", "6159", "9654", "4951")),
    "Z": PieceDefinition("#FF006E", ("0156", "2659", "a954", "8451")),
    "S": PieceDefinition("#A7E916", ("1254", "a651", "8956", "0459")),
}

NETWORK_ACTIONS = ("left", "right", "down", "rotate", "drop")
PIECE_IDS = count(1)


def coords_to_matrix(coords: list[tuple[int, int]]) -> list[list[int]]:
    """Turn coordinates into their smallest containing matrix."""
    min_x = min(x for x, _ in coords)
    min_y = min(y for _, y in coords)
    width = max(x for x, _ in coords) - min_x + 1
    height = max(y for _, y in coords) - min_y + 1
    matrix = [[0 for _ in range(width)] for _ in range(height)]
    for x, y in coords:
        matrix[y - min_y][x - min_x] = 1
    return matrix


def piece_catalog() -> dict[str, dict[str, object]]:
    """Describe every piece rotation for protocol clients."""
    return {
        piece_type: {
            "color": definition.color,
            "rotations": [
                {
                    "code": code,
                    "blocks": [[int(char, 16) % 4, int(char, 16) // 4] for char in code],
                }
                for code in definition.codes
            ],
        }
        for piece_type, definition in PIECES.items()
    }


class TetrisPiece:
    """A tetromino with a stable id, position, and rotation."""

    def __init__(self, piece_type: str | None = None, piece_id: int | None = None) -> None:
        self.type = piece_type or random.choice(list(PIECES))
        self.piece_id = next(PIECE_IDS) if piece_id is None else piece_id
        definition = PIECES[self.type]
        self.color = definition.color
        self.codes = deque(definition.codes)
        self.x = 4
        self.y = 0

    @property
    def shape(self) -> list[tuple[int, int]]:
        """Return relative coordinates within the piece 4x4 grid."""
        return [(value % 4, value // 4) for value in (int(char, 16) for char in self.code)]

    @property
    def blocks(self) -> list[tuple[int, int]]:
        """Return absolute board coordinates occupied by this piece."""
        return [(self.x + px, self.y + py) for px, py in self.shape]

    @property
    def code(self) -> str:
        return self.codes[0]

    def rotate(self) -> None:
        self.codes.rotate(-1)

    def undo_rotate(self) -> None:
        self.codes.rotate(1)

    def payload(self) -> dict[str, object]:
        return {
            "piece_id": self.piece_id,
            "type": self.type,
            "x": self.x,
            "y": self.y,
            "code": self.code,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object] | None) -> TetrisPiece | None:
        if payload is None:
            return None
        piece_id = payload["piece_id"]
        x = payload["x"]
        y = payload["y"]
        if not isinstance(piece_id, int) or not isinstance(x, int) or not isinstance(y, int):
            raise ValueError("Piece id and coordinates must be integers")
        piece = cls(str(payload["type"]), piece_id)
        code = str(payload["code"])
        if code in piece.codes:
            while piece.code != code:
                piece.rotate()
        piece.x = x
        piece.y = y
        return piece


@dataclass(frozen=True, slots=True)
class LockResult:
    piece_id: int
    cleared_lines: int


@dataclass(slots=True)
class TetrisBoardState:
    width: int = 10
    height: int = 20
    cells: list[list[int | str]] = field(init=False)
    current_piece: TetrisPiece | None = None

    def __post_init__(self) -> None:
        self.cells = [[0 for _ in range(self.width)] for _ in range(self.height)]

    def check_collision(self) -> bool:
        if self.current_piece is None:
            return False
        for board_x, board_y in self.current_piece.blocks:
            if board_x < 0 or board_x >= self.width or board_y >= self.height:
                return True
            if board_y >= 0 and self.cells[board_y][board_x] != 0:
                return True
        return False

    def move(self, dx: int, dy: int) -> bool:
        if self.current_piece is None:
            return False
        old_x, old_y = self.current_piece.x, self.current_piece.y
        self.current_piece.x += dx
        self.current_piece.y += dy
        if self.check_collision():
            self.current_piece.x, self.current_piece.y = old_x, old_y
            return False
        return True

    def rotate(self) -> bool:
        if self.current_piece is None:
            return False
        self.current_piece.rotate()
        if self.check_collision():
            self.current_piece.undo_rotate()
            return False
        return True

    def is_grounded(self) -> bool:
        if self.current_piece is None:
            return False
        self.current_piece.y += 1
        grounded = self.check_collision()
        self.current_piece.y -= 1
        return grounded

    def hard_drop(self) -> None:
        while self.move(0, 1):
            pass

    def lock(self) -> LockResult | None:
        piece = self.current_piece
        if piece is None:
            return None
        for board_x, board_y in piece.blocks:
            if 0 <= board_x < self.width and 0 <= board_y < self.height:
                self.cells[board_y][board_x] = piece.color
        cleared = self.clear_full_lines()
        return LockResult(piece.piece_id, cleared)

    def clear_full_lines(self) -> int:
        new_rows = [row for row in self.cells if not all(row)]
        cleared = self.height - len(new_rows)
        self.cells = [[0 for _ in range(self.width)] for _ in range(cleared)] + new_rows
        return cleared


def drop_interval_for_level(level: int) -> float:
    exponent = level - 1
    base = max(0.8 - exponent * 0.007, 0.001)
    return max(0.02, base**exponent)


@dataclass(slots=True)
class PlayerState:
    name: str
    board: TetrisBoardState = field(default_factory=TetrisBoardState)
    next_piece: TetrisPiece = field(default_factory=TetrisPiece)
    score: int = 0
    level: int = 1
    lines_cleared: int = 0
    drop_interval: float = 1.0
    game_over: bool = False

    def record_lock(self, cleared_lines: int, lines_per_level: int = 10) -> bool:
        if cleared_lines:
            line_score = {1: 100, 2: 300, 3: 500, 4: 800}.get(cleared_lines, cleared_lines * 200)
            self.score += line_score * self.level
            self.lines_cleared += cleared_lines
        else:
            self.score += 10
        new_level = max(1, 1 + self.lines_cleared // lines_per_level)
        if new_level == self.level:
            return False
        self.level = new_level
        self.drop_interval = drop_interval_for_level(self.level)
        return True

    def payload(self) -> dict[str, object]:
        return {
            "name": self.name,
            "board": self.board.cells,
            "current_piece": self.board.current_piece.payload() if self.board.current_piece else None,
            "next_piece": self.next_piece.payload(),
            "score": self.score,
            "level": self.level,
            "lines": self.lines_cleared,
            "game_over": self.game_over,
        }


@dataclass(frozen=True, slots=True)
class GameEvent:
    type: str
    payload: dict[str, object]


class TetrisMatch:
    """Pure two-player match state shared by every transport and UI."""

    def __init__(self) -> None:
        self.players: dict[int, PlayerState] = {}
        self.status = "waiting"

    def start(self, names: dict[int, str]) -> list[GameEvent]:
        self.players = {player_id: PlayerState(name) for player_id, name in names.items()}
        self.status = "running"
        events: list[GameEvent] = []
        for player_id in self.players:
            events.extend(self._spawn(player_id))
        return events

    def apply_action(self, player_id: int, action: object) -> tuple[bool, list[GameEvent]]:
        if (
            self.status != "running"
            or action not in NETWORK_ACTIONS
            or player_id not in self.players
            or self.players[player_id].game_over
        ):
            return False, []
        board = self.players[player_id].board
        if action == "left":
            board.move(-1, 0)
        elif action == "right":
            board.move(1, 0)
        elif action == "rotate":
            board.rotate()
        elif action == "down":
            if not board.move(0, 1):
                return True, self._lock(player_id)
        elif action == "drop":
            board.hard_drop()
            return True, self._lock(player_id)
        return True, []

    def tick(self, player_id: int) -> list[GameEvent]:
        if self.status != "running" or self.players[player_id].game_over:
            return []
        if self.players[player_id].board.move(0, 1):
            return []
        return self._lock(player_id)

    def payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "players": {str(player_id): player.payload() for player_id, player in self.players.items()},
        }

    def _lock(self, player_id: int) -> list[GameEvent]:
        state = self.players[player_id]
        result = state.board.lock()
        if result is None:
            return []
        state.record_lock(result.cleared_lines)
        events = [
            GameEvent(
                "piece_locked",
                {
                    "player": player_id,
                    "piece_id": result.piece_id,
                    "cleared_lines": result.cleared_lines,
                },
            )
        ]
        events.extend(self._spawn(player_id))
        return events

    def _spawn(self, player_id: int) -> list[GameEvent]:
        state = self.players[player_id]
        state.board.current_piece = state.next_piece
        if state.board.check_collision():
            state.game_over = True
            self.status = "game_over"
            return [GameEvent("game_over", {"loser": player_id})]
        state.next_piece = TetrisPiece()
        return []
