import argparse
import asyncio
import contextlib
import json
import os
import random
import sys
from collections import deque
from dataclasses import dataclass, field
from typing import ClassVar, cast

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import Footer, Label, Static
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

# Compact hex-based shape definitions (4x4 grid)
PIECES = {
    # Keep the classic Guideline hue families, but use explicit tones that stay
    # distinct across terminal themes.
    "O": {"color": "#FFD23F", "codes": ["56a9", "6a95", "a956", "956a"]},
    "I": {"color": "#49C6E5", "codes": ["4567", "26ae", "ba98", "d951"]},
    "J": {"color": "#3A86FF", "codes": ["0456", "2159", "a654", "8951"]},
    "L": {"color": "#FF9F1C", "codes": ["2654", "a951", "8456", "0159"]},
    "T": {"color": "#9D4EDD", "codes": ["1456", "6159", "9654", "4951"]},
    "Z": {"color": "#FF006E", "codes": ["0156", "2659", "a954", "8451"]},
    "S": {"color": "#A7E916", "codes": ["1254", "a651", "8956", "0459"]},
}

CELL_WIDTH = 4
CELL_FILL = "█" * CELL_WIDTH
CELL_EMPTY = " " * CELL_WIDTH
BOARD_RENDER_WIDTH = 10 * CELL_WIDTH + 2
BOARD_CONTAINER_WIDTH = BOARD_RENDER_WIDTH + 2
BOARD_WIDGET_WIDTH = BOARD_RENDER_WIDTH + 4
MAX_PREVIEW_DIM = 4
PREVIEW_RENDER_WIDTH = MAX_PREVIEW_DIM * CELL_WIDTH + 2
NEXT_CONTAINER_WIDTH = PREVIEW_RENDER_WIDTH + 4
PANEL_WIDGET_WIDTH = NEXT_CONTAINER_WIDTH
SIDEBAR_WIDTH = NEXT_CONTAINER_WIDTH
LOCK_DELAY = 0.5


def coords_to_matrix(coords):
    """Turn a list of (x, y) coords into a minimal 2D matrix (for previews)."""
    min_x = min(x for x, _ in coords)
    min_y = min(y for _, y in coords)
    width = max(x for x, _ in coords) - min_x + 1
    height = max(y for _, y in coords) - min_y + 1
    matrix = [[0 for _ in range(width)] for _ in range(height)]
    for x, y in coords:
        matrix[y - min_y][x - min_x] = 1
    return matrix


class TetrisPiece:
    def __init__(self, piece_type=None):
        if piece_type is None:
            piece_type = random.choice(list(PIECES.keys()))

        self.type = piece_type
        self.color = PIECES[piece_type]["color"]
        self.codes = deque(PIECES[piece_type]["codes"])
        self.x = 4  # Start at center of board
        self.y = 0

    @property
    def shape(self) -> list[tuple[int, int]]:
        """expand the Hex code into a list of (x, y) coords relative to a 4x4 grid"""
        coords = []
        for char in self.code:
            value = int(char, 16)
            y, x = divmod(value, 4)
            coords.append((x, y))
        return coords

    @property
    def blocks(self):
        """Absolute board coords occupied by this piece."""
        return [(self.x + px, self.y + py) for px, py in self.shape]

    @property
    def code(self):
        return self.codes[0]

    def rotate(self):
        self.codes.rotate(-1)

    def undo_rotate(self):
        self.codes.rotate(1)


class TetrisBoard(Static):
    """The main game board widget"""

    def __init__(self, player_id: int, width: int = 10, height: int = 20, **kwargs) -> None:
        super().__init__(**kwargs)
        self.player_id = player_id
        self.board_width = width
        self.board_height = height
        self.board = [[0 for _ in range(width)] for _ in range(height)]
        self.current_piece: TetrisPiece | None = None
        self.lock_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        yield Static(self.render_board(), id="board-display")

    def on_mount(self) -> None:
        """Called when the widget is mounted"""
        self.update_display()

    def render_board(self) -> Text:
        """Render the current state of the board"""
        text = Text()

        # Create a copy of the board to render the current piece
        display_board = [row[:] for row in self.board]

        # Add current piece to display board
        if self.current_piece:
            for board_x, board_y in self.current_piece.blocks:
                if 0 <= board_x < self.board_width and 0 <= board_y < self.board_height:
                    display_board[board_y][board_x] = self.current_piece.color

        # Add top border
        text.append("┌" + "─" * (self.board_width * CELL_WIDTH) + "┐\n", style="bold white")

        # Render each row
        for row in display_board:
            rendered_row = Text()
            rendered_row.append("│", style="bold white")
            for cell in row:
                if cell == 0:
                    rendered_row.append(CELL_EMPTY)
                else:
                    rendered_row.append(CELL_FILL, style=f"bold {cell}")
            rendered_row.append("│\n", style="bold white")
            text.append_text(rendered_row)
            text.append_text(rendered_row.copy())

        # Add bottom border
        text.append("└" + "─" * (self.board_width * CELL_WIDTH) + "┘", style="bold white")

        return text

    def update_display(self) -> None:
        """Update the board display"""
        board_display = self.query_one("#board-display", Static)
        board_display.update(self.render_board())

    def move_piece(self, dx: int, dy: int) -> bool:
        """Move the current piece"""
        if not self.current_piece:
            return False
        old_x, old_y = self.current_piece.x, self.current_piece.y
        self.current_piece.x += dx
        self.current_piece.y += dy

        # Check collision with boundaries and existing pieces
        if self.check_collision():
            # Revert move
            self.current_piece.x, self.current_piece.y = old_x, old_y
            # Let players adjust a grounded piece before locking it.
            if dy > 0:
                self.schedule_lock()
            return False

        self.update_display()
        return True

    def is_grounded(self) -> bool:
        """Return whether the current piece can no longer move down."""
        if not self.current_piece:
            return False
        self.current_piece.y += 1
        grounded = self.check_collision()
        self.current_piece.y -= 1
        return grounded

    def schedule_lock(self) -> None:
        """Lock the current piece after a fixed, short adjustment window."""
        if self.lock_timer or not (piece := self.current_piece):
            return
        self.lock_timer = self.set_timer(LOCK_DELAY, lambda: self.lock_if_current(piece))

    def lock_if_current(self, piece: TetrisPiece) -> None:
        """Lock `piece` only if it is still grounded when the deadline expires."""
        self.lock_timer = None
        if self.current_piece is piece and self.is_grounded():
            self.lock_piece()

    def check_collision(self):
        """Check if current piece collides with boundaries or other pieces"""
        if not self.current_piece:
            return False
        for board_x, board_y in self.current_piece.blocks:
            # Check boundaries
            if board_x < 0 or board_x >= self.board_width or board_y >= self.board_height:
                return True

            # Check collision with existing pieces (if board_y >= 0)
            if board_y >= 0 and self.board[board_y][board_x] != 0:
                return True

        return False

    def lock_piece(self):
        """Fix the current piece to the board and spawn a new one."""
        if not self.current_piece:
            return
        if self.lock_timer:
            self.lock_timer.stop()
            self.lock_timer = None
        for board_x, board_y in self.current_piece.blocks:
            if 0 <= board_x < self.board_width and 0 <= board_y < self.board_height:
                self.board[board_y][board_x] = self.current_piece.color

        # Clear any completed lines
        cleared = self._clear_full_lines()

        # Notify app about scoring/level updates.
        # NOTE: If this raises (e.g., during shutdown), allow the error rather than hiding it.
        app = cast("TetrisApp", self.app)
        app.on_piece_locked(self.player_id, cleared)

        # Spawn a new piece
        app.spawn_next_piece(self.player_id)

    def _clear_full_lines(self) -> int:
        """Remove filled rows and collapse the board."""
        new_rows = [row for row in self.board if not all(row)]
        cleared = self.board_height - len(new_rows)
        if cleared:
            # Add empty rows at the top
            self.board = [[0 for _ in range(self.board_width)] for _ in range(cleared)] + new_rows
        else:
            self.board = new_rows
        return cleared

    def rotate_piece(self) -> bool:
        """Rotate the current piece"""
        if not self.current_piece:
            return False
        self.current_piece.rotate()

        # Check if rotation causes collision
        if self.check_collision():
            self.current_piece.undo_rotate()
            return False

        self.update_display()
        return True


class NextPieceWidget(Static):
    """Widget to show the next piece"""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.next_piece = TetrisPiece()

    def compose(self) -> ComposeResult:
        yield Label("NEXT", classes="section-title")
        yield Static(self.render_next_piece(), id="next-piece-display")

    def render_next_piece(self) -> Text:
        shape_matrix = coords_to_matrix(self.next_piece.shape)
        color = self.next_piece.color
        shape_h = len(shape_matrix)
        shape_w = max(len(r) for r in shape_matrix)
        dim = max(MAX_PREVIEW_DIM, shape_h, shape_w)
        content_width = dim * CELL_WIDTH
        shape_render_width = shape_w * CELL_WIDTH
        horizontal_left_pad = (content_width - shape_render_width) // 2
        horizontal_right_pad = content_width - shape_render_width - horizontal_left_pad

        text = Text()
        # top border
        text.append("┌" + "─" * content_width + "┐\n", style="dim white")

        # how many qnk rows above/below
        top_pad = (dim - shape_h) // 2
        bottom_pad = dim - shape_h - top_pad

        # helper for an empty row
        for _ in range(top_pad):
            empty_row = Text()
            empty_row.append("│", style="dim white")
            empty_row.append(" " * content_width)
            empty_row.append("│\n", style="dim white")
            text.append_text(empty_row)
            text.append_text(empty_row.copy())

        # each shape row, centered horizontally
        for row in shape_matrix:
            rendered_row = Text()
            rendered_row.append("│", style="dim white")
            rendered_row.append(" " * horizontal_left_pad)
            for cell in row:
                if cell:
                    rendered_row.append(CELL_FILL, style=f"bold {color}")
                else:
                    rendered_row.append(CELL_EMPTY)
            rendered_row.append(" " * horizontal_right_pad)
            rendered_row.append("│\n", style="dim white")
            text.append_text(rendered_row)
            text.append_text(rendered_row.copy())

        for _ in range(bottom_pad):
            empty_row = Text()
            empty_row.append("│", style="dim white")
            empty_row.append(" " * content_width)
            empty_row.append("│\n", style="dim white")
            text.append_text(empty_row)
            text.append_text(empty_row.copy())

        # bottom border
        text.append("└" + "─" * content_width + "┘", style="dim white")
        return text

    def update_piece(self, piece: TetrisPiece) -> None:
        """Update the next piece"""
        self.next_piece = piece
        next_display = self.query_one("#next-piece-display", Static)
        next_display.update(self.render_next_piece())


class ScoreWidget(Static):
    """Widget to display score and level"""

    score = reactive(0)
    level = reactive(1)
    lines = reactive(0)

    def compose(self) -> ComposeResult:
        yield Label(f"SCORE {self.score}", id="score-value", classes="section-title")
        yield Label(f"LEVEL {self.level}", id="level-value", classes="score-number")
        yield Label(f"LINES {self.lines}", id="lines-value", classes="section-title")

    def watch_score(self, score: int) -> None:
        with contextlib.suppress(NoMatches):
            self.query_one("#score-value", Label).update(f"SCORE {score}")

    def watch_level(self, level: int) -> None:
        with contextlib.suppress(NoMatches):
            self.query_one("#level-value", Label).update(f"LEVEL {level}")

    def watch_lines(self, lines: int) -> None:
        with contextlib.suppress(NoMatches):
            self.query_one("#lines-value", Label).update(f"LINES {lines}")


@dataclass
class PlayerState:
    """The independent game state for one local player."""

    next_piece: TetrisPiece = field(default_factory=TetrisPiece)
    score: int = 0
    level: int = 1
    lines_cleared: int = 0
    drop_interval: float = 1.0
    game_over: bool = False


@dataclass
class PlayerView:
    """The mounted widgets used to render one player's game state."""

    board_widget: TetrisBoard
    next_widget: NextPieceWidget
    score_widget: ScoreWidget
    container: Container
    overlay_widget: Static


class PlayerPane(Container):
    """Reusable split-screen pane with board, next piece, and score widgets."""

    def __init__(self, player_id: int, **kwargs) -> None:
        super().__init__(**kwargs)
        self.player_id = player_id
        self.board_widget = TetrisBoard(player_id, classes="player-board")
        self.next_widget = NextPieceWidget(classes="player-panel next-widget")
        self.score_widget = ScoreWidget(classes="player-panel score-widget")
        self.overlay_widget = Static("GAME OVER\nPress R to restart", classes="game-over-overlay")

    def compose(self) -> ComposeResult:
        with Horizontal(classes="player-layout"):
            yield self.board_widget
            with Vertical(classes="player-sidebar"):
                yield self.next_widget
                yield self.score_widget
        yield self.overlay_widget


class HelpScreen(ModalScreen[None]):
    """Compact controls modal shown from the footer help action."""

    CSS = """
    HelpScreen {
        align: center middle;
        background: #060a12 70%;
    }

    #help-dialog {
        width: 42;
        height: auto;
        padding: 1 2;
        background: #16202b;
        border: round #7dd3fc;
    }

    #help-title {
        text-align: center;
        text-style: bold;
        color: #f8fafc;
        margin-bottom: 1;
    }

    .help-line {
        color: #dbeafe;
        margin-bottom: 1;
    }

    #help-close {
        text-align: center;
        color: #7dd3fc;
        margin-top: 1;
    }
    """

    BINDINGS: ClassVar = (("escape,h,enter,space", "close_help", "Close help"),)

    def __init__(self, two_players: bool) -> None:
        super().__init__()
        self.two_players = two_players

    def compose(self) -> ComposeResult:
        with Container(id="help-dialog"):
            yield Label("HELP", id="help-title")
            if self.two_players:
                yield Label("P1: W/A/S/D move & rotate, Q drops", classes="help-line")
                yield Label("P2: arrows move & rotate, Space drops", classes="help-line")
            else:
                yield Label("Arrows or W/A/S/D: move & rotate", classes="help-line")
                yield Label("Space: hard drop", classes="help-line")
            yield Label("R: restart after game over", classes="help-line")
            yield Label("Ctrl+Q: quit", classes="help-line")
            yield Label("Esc / H to close", id="help-close")

    def action_close_help(self) -> None:
        self.dismiss(None)


class TetrisApp(App):
    """Main Tetris application"""

    SINGLE_PLAYER_BINDINGS = (
        ("left,a", "player_one_left", "Move Left"),
        ("right,d", "player_one_right", "Move Right"),
        ("down,s", "player_one_down", "Move Down"),
        ("up,w", "player_one_rotate", "Rotate"),
        ("space,q", "player_one_hard_drop", "Drop"),
    )
    PLAYER_ONE_BINDINGS = (
        ("a", "player_one_left", "P1 Left"),
        ("d", "player_one_right", "P1 Right"),
        ("s", "player_one_down", "P1 Down"),
        ("w", "player_one_rotate", "P1 Rotate"),
        ("q", "player_one_hard_drop", "P1 Drop"),
    )
    PLAYER_TWO_BINDINGS = (
        ("left", "player_two_left", "P2 Left"),
        ("right", "player_two_right", "P2 Right"),
        ("down", "player_two_down", "P2 Down"),
        ("up", "player_two_rotate", "P2 Rotate"),
        ("space", "player_two_hard_drop", "P2 Drop"),
    )

    OTHER_BINDINGS = (
        ("h", "help", "Help"),
        ("ctrl+q", "quit", "Quit"),
        ("r", "restart", "Restart"),
        ("ctrl+s", "screenshot", "Screenshot"),
    )

    BINDINGS: ClassVar = OTHER_BINDINGS
    LIVE_ACTIONS: ClassVar = tuple(
        binding[1] for binding in SINGLE_PLAYER_BINDINGS + PLAYER_ONE_BINDINGS + PLAYER_TWO_BINDINGS
    )
    NETWORK_PROTOCOL: ClassVar = "textual-tetris/v1"
    NETWORK_ACTIONS: ClassVar = ("left", "right", "down", "rotate", "drop")

    def __init__(
        self,
        two_players: bool = False,
        network_mode: str | None = None,
        connect_url: str | None = None,
        server_host: str = "0.0.0.0",
        server_port: int = 8765,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        if network_mode not in {None, "server", "client"}:
            raise ValueError(f"Unsupported network mode: {network_mode}")
        if network_mode == "client" and not connect_url:
            raise ValueError("A connect URL is required in client mode")

        self.network_mode = network_mode
        self.connect_url = connect_url
        self.server_host = server_host
        self.server_port = server_port
        self.two_players = two_players or network_mode is not None
        self.game_timers = {}
        self._network_client = None
        self._network_clients = set()
        self._network_tasks = set()
        self.lines_per_level = 10
        self.players = {player_id: PlayerState() for player_id in ((1, 2) if self.two_players else (1,))}
        if network_mode == "client":
            bindings = self.PLAYER_TWO_BINDINGS
        elif network_mode == "server":
            bindings = self.PLAYER_ONE_BINDINGS
        else:
            bindings = self.PLAYER_ONE_BINDINGS if two_players else self.SINGLE_PLAYER_BINDINGS
        for keys, action, description in bindings:
            self.bind(keys, action, description=description)
        if two_players and network_mode is None:
            for keys, action, description in self.PLAYER_TWO_BINDINGS:
                self.bind(keys, action, description=description)

    CSS = (
        """
    Screen {
        layout: vertical;
        background: #111927;
    }

    #game-container {
        width: 100%;
        height: 1fr;
        align: center middle;
        background: #111927;
        padding: 1 2;
    }

    #playfield {
        width: auto;
        height: auto;
        align: center middle;
    }

    #board-container {
        width: __BOARD_CONTAINER_WIDTH__;
        height: auto;
        margin: 0 1 0 0;
        padding: 1;
        background: #22303d;
        layers: base overlay;
        content-align: center top;
    }

    #board {
        width: auto;
        height: auto;
    }

    #board-display {
        margin: 0;
        padding: 0;
        layer: base;
    }

    #sidebar {
        width: __SIDEBAR_WIDTH__;
        height: auto;
        padding: 0;
        align-vertical: top;
    }

    #next-piece-container {
        width: __NEXT_CONTAINER_WIDTH__;
        height: auto;
        margin-top: 1;
        margin-bottom: 1;
        padding: 1;
        background: #1a2430;
        border: round #5bc0eb;
    }

    #score-container {
        width: __SIDEBAR_WIDTH__;
        height: auto;
        padding: 1;
        background: #1a2430;
        border: round #5bc0eb;
    }

    .player-pane {
        width: auto;
        height: auto;
        margin: 0 1;
        layers: base overlay;
    }

    .player-layout {
        width: auto;
        height: auto;
    }

    .player-board {
        width: __BOARD_WIDGET_WIDTH__;
        height: auto;
        padding: 1;
        background: #22303d;
        border: round #f4f1de;
        border-bottom: solid #f4f1de;
    }

    .player-panel {
        width: __PANEL_WIDGET_WIDTH__;
        height: auto;
        padding: 1;
        background: #1a2430;
        border: round #5bc0eb;
    }

    .player-sidebar {
        width: __PANEL_WIDGET_WIDTH__;
        height: auto;
        padding: 0;
        align-vertical: top;
        margin-left: 1;
    }

    .next-widget {
        margin-bottom: 1;
    }

    .player-board #board-display {
        margin: 0;
        padding: 0;
        layer: base;
    }

    .game-over-overlay {
        layer: overlay;
        content-align: center middle;
        text-style: bold;
        color: #f8fafc;
        background: #070c14 82%;
        display: none;
    }

    #game-over-overlay {
        layer: overlay;
        content-align: center middle;
        text-style: bold;
        color: #f8fafc;
        background: #070c14 82%;
        display: none;
    }

    .game-over .game-over-overlay {
        display: block;
    }

    .game-over #game-over-overlay {
        display: block;
    }

    .section-title {
        text-align: center;
        text-style: bold;
        color: #7dd3fc;
    }

    .score-number {
        text-align: center;
        text-style: bold;
        color: #f8fafc;
        margin-bottom: 1;
        content-align: center middle;
    }

    Footer {
        dock: bottom;
    }
    """.replace("__BOARD_CONTAINER_WIDTH__", str(BOARD_CONTAINER_WIDTH))
        .replace("__SIDEBAR_WIDTH__", str(SIDEBAR_WIDTH))
        .replace("__NEXT_CONTAINER_WIDTH__", str(NEXT_CONTAINER_WIDTH))
        .replace("__BOARD_WIDGET_WIDTH__", str(BOARD_WIDGET_WIDTH))
        .replace("__PANEL_WIDGET_WIDTH__", str(PANEL_WIDGET_WIDTH))
    )

    def compose(self) -> ComposeResult:
        with Container(id="game-container"), Horizontal(id="playfield"):
            if self.two_players:
                yield PlayerPane(1, id="player-one", classes="player-pane")
                yield PlayerPane(2, id="player-two", classes="player-pane")
            else:
                with Container(id="board-container"):
                    yield TetrisBoard(1, id="board")
                    yield Static("GAME OVER\nPress R to restart", id="game-over-overlay")
                with Vertical(id="sidebar"):
                    with Container(id="next-piece-container"):
                        yield NextPieceWidget(id="next-piece")
                    with Container(id="score-container"):
                        yield ScoreWidget(id="score-widget")
        yield Footer()

    def on_mount(self) -> None:
        """Initialize the game"""
        if self.two_players:
            first_player = self.query_one("#player-one", PlayerPane)
            second_player = self.query_one("#player-two", PlayerPane)
            self.player_panes = {
                1: PlayerView(
                    first_player.board_widget,
                    first_player.next_widget,
                    first_player.score_widget,
                    first_player,
                    first_player.overlay_widget,
                ),
                2: PlayerView(
                    second_player.board_widget,
                    second_player.next_widget,
                    second_player.score_widget,
                    second_player,
                    second_player.overlay_widget,
                ),
            }
        else:
            board_container = self.query_one("#board-container", Container)
            self.player_panes = {
                1: PlayerView(
                    self.query_one("#board", TetrisBoard),
                    self.query_one("#next-piece", NextPieceWidget),
                    self.query_one("#score-widget", ScoreWidget),
                    board_container,
                    self.query_one("#game-over-overlay", Static),
                )
            }

        if self.network_mode != "client":
            # Give widgets time to mount, then update displays.
            self.call_after_refresh(self._update_all_displays)

            # The server starts P2 when the remote client joins.
            for player_id in self.players:
                if self.network_mode == "server" and player_id == 2:
                    continue
                self.start_game_timer(player_id)

        if self.network_mode == "server":
            self.run_worker(self._run_server())
        elif self.network_mode == "client":
            self.run_worker(self._run_client())

    def _update_all_displays(self):
        """Update all game displays after widgets are mounted"""
        for player_id in self.players:
            self.spawn_next_piece(player_id)
            self._refresh_score_widget(player_id)

    @staticmethod
    def _piece_payload(piece: TetrisPiece | None) -> dict | None:
        if piece is None:
            return None
        return {"type": piece.type, "x": piece.x, "y": piece.y, "code": piece.code}

    def _state_payload(self) -> dict:
        return {
            "type": "state",
            "players": {
                str(player_id): {
                    "board": state_view.board,
                    "current_piece": self._piece_payload(state_view.current_piece),
                    "next_piece": self._piece_payload(self.players[player_id].next_piece),
                    "score": self.players[player_id].score,
                    "level": self.players[player_id].level,
                    "lines": self.players[player_id].lines_cleared,
                    "game_over": self.players[player_id].game_over,
                }
                for player_id, state_view in (
                    (player_id, self.player_panes[player_id].board_widget) for player_id in self.players
                )
            },
        }

    def _broadcast_state(self) -> None:
        if self.network_mode == "server" and self._network_clients:
            self._schedule_network_task(self._send_state())

    def _welcome_payload(self) -> dict:
        return {
            "type": "welcome",
            "protocol": self.NETWORK_PROTOCOL,
            "role": "player2",
            "actions": list(self.NETWORK_ACTIONS),
            "input": {"type": "input", "action": "<action>"},
            "state": {
                "board": "20 rows of 10 cells, top to bottom; 0 means empty",
                "current_piece": "type, x, y, and rotation code, or null",
                "next_piece": "type, x, y, and rotation code",
            },
        }

    def _schedule_network_task(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._network_tasks.add(task)
        task.add_done_callback(self._network_tasks.discard)

    async def _send_state(self) -> None:
        message = json.dumps(self._state_payload())
        for websocket in tuple(self._network_clients):
            try:
                await websocket.send(message)
            except Exception:
                self._network_clients.discard(websocket)

    async def _run_server(self) -> None:
        async with serve(self._handle_client, self.server_host, self.server_port) as server:
            port = server.sockets[0].getsockname()[1]
            self.server_port = port
            self.notify(f"Server listening on ws://127.0.0.1:{port}")
            await asyncio.Future()

    async def _handle_client(self, websocket) -> None:
        if self._network_clients:
            await websocket.close(1013, "A remote player is already connected")
            return

        self._network_clients.add(websocket)
        self.start_game_timer(2)
        self.notify("Remote player connected")
        try:
            await websocket.send(json.dumps(self._welcome_payload()))
            await websocket.send(json.dumps(self._state_payload()))
            async for message in websocket:
                try:
                    payload = json.loads(message)
                except json.JSONDecodeError:
                    continue
                action = payload.get("action")
                if payload.get("type") == "input" and action in self.NETWORK_ACTIONS:
                    self._apply_player_two_action(action)
        finally:
            self._network_clients.discard(websocket)
            if not self._network_clients and (timer := self.game_timers.get(2)):
                timer.pause()
            self.notify("Remote player disconnected")

    async def _run_client(self) -> None:
        try:
            assert self.connect_url is not None
            async with connect(self.connect_url) as websocket:
                self._network_client = websocket
                self.notify("Connected to remote game")
                async for message in websocket:
                    payload = json.loads(message)
                    if payload.get("type") == "state":
                        self._apply_state(payload)
        except Exception as error:
            self.notify(f"Remote game disconnected: {error}", severity="error")
        finally:
            self._network_client = None

    def _send_input(self, action: str) -> None:
        if self._network_client:
            self._schedule_network_task(
                self._network_client.send(json.dumps({"type": "input", "action": action}))
            )

    @staticmethod
    def _piece_from_payload(payload: dict | None) -> TetrisPiece | None:
        if payload is None:
            return None
        piece = TetrisPiece(payload["type"])
        if payload["code"] in piece.codes:
            while piece.code != payload["code"]:
                piece.rotate()
        piece.x = payload["x"]
        piece.y = payload["y"]
        return piece

    def _apply_state(self, payload: dict) -> None:
        for player_id, player_payload in payload["players"].items():
            player_id = int(player_id)
            state = self.players[player_id]
            board = self.player_panes[player_id].board_widget
            board.board = player_payload["board"]
            board.current_piece = self._piece_from_payload(player_payload["current_piece"])
            next_piece = self._piece_from_payload(player_payload["next_piece"])
            if next_piece is not None:
                state.next_piece = next_piece
            state.score = player_payload["score"]
            state.level = player_payload["level"]
            state.lines_cleared = player_payload["lines"]
            state.game_over = player_payload["game_over"]
            view = self.player_panes[player_id]
            view.overlay_widget.display = state.game_over
            view.container.set_class(state.game_over, "game-over")
            board.update_display()
            if next_piece is not None:
                view.next_widget.update_piece(next_piece)
            self._refresh_score_widget(player_id)

    def _apply_player_two_action(self, action: str) -> None:
        if action == "left":
            self._move_piece(2, -1, 0)
        elif action == "right":
            self._move_piece(2, 1, 0)
        elif action == "down":
            self._move_piece(2, 0, 1)
        elif action == "rotate":
            self._rotate_piece(2)
        elif action == "drop":
            self._hard_drop(2)

    def start_game_timer(self, player_id: int) -> None:
        """Start or reset one player's automatic piece-dropping timer."""
        if timer := self.game_timers.get(player_id):
            timer.pause()
        state = self.players[player_id]
        self.game_timers[player_id] = self.set_interval(state.drop_interval, lambda: self.auto_drop(player_id))
        if state.game_over:
            self.game_timers[player_id].pause()

    def auto_drop(self, player_id: int) -> None:
        """Automatically drop one active player's piece."""
        state = self.players[player_id]
        if not state.game_over:
            self.player_panes[player_id].board_widget.move_piece(0, 1)
            self._broadcast_state()

    def _move_piece(self, player_id: int, dx: int, dy: int) -> None:
        if (state := self.players.get(player_id)) and not state.game_over:
            self.player_panes[player_id].board_widget.move_piece(dx, dy)
            self._broadcast_state()

    def _rotate_piece(self, player_id: int) -> None:
        if (state := self.players.get(player_id)) and not state.game_over:
            self.player_panes[player_id].board_widget.rotate_piece()
            self._broadcast_state()

    def _hard_drop(self, player_id: int) -> None:
        """Instantly drop the piece to the lowest valid position."""
        if player_id not in self.players:
            return
        board = self.player_panes[player_id].board_widget
        while not self.players[player_id].game_over and board.move_piece(0, 1):
            pass
        self._broadcast_state()

    def action_player_one_left(self) -> None:
        self._move_piece(1, -1, 0)

    def action_player_one_right(self) -> None:
        self._move_piece(1, 1, 0)

    def action_player_one_down(self) -> None:
        self._move_piece(1, 0, 1)

    def action_player_one_rotate(self) -> None:
        self._rotate_piece(1)

    def action_player_one_hard_drop(self) -> None:
        self._hard_drop(1)

    def action_player_two_left(self) -> None:
        if self.network_mode == "client":
            self._send_input("left")
            return
        self._move_piece(2, -1, 0)

    def action_player_two_right(self) -> None:
        if self.network_mode == "client":
            self._send_input("right")
            return
        self._move_piece(2, 1, 0)

    def action_player_two_down(self) -> None:
        if self.network_mode == "client":
            self._send_input("down")
            return
        self._move_piece(2, 0, 1)

    def action_player_two_rotate(self) -> None:
        if self.network_mode == "client":
            self._send_input("rotate")
            return
        self._rotate_piece(2)

    def action_player_two_hard_drop(self) -> None:
        if self.network_mode == "client":
            self._send_input("drop")
            return
        self._hard_drop(2)

    def on_piece_locked(self, player_id: int, cleared_lines: int) -> None:
        """Update score/level/timing after a piece locks."""
        state = self.players[player_id]
        if cleared_lines:
            # Classic scoring scale per number of lines cleared at once
            line_score = {1: 100, 2: 300, 3: 500, 4: 800}.get(cleared_lines, cleared_lines * 200)
            state.score += line_score * state.level
            state.lines_cleared += cleared_lines
        else:
            # Small reward just for locking a piece
            state.score += 10

        # Level up every N cleared lines
        new_level = max(1, 1 + state.lines_cleared // self.lines_per_level)
        if new_level != state.level:
            state.level = new_level
            state.drop_interval = self._drop_interval_for_level(state.level)
            self.start_game_timer(player_id)

        self._refresh_score_widget(player_id)
        self._broadcast_state()

    def spawn_next_piece(self, player_id: int) -> None:
        """Move queued next piece to the board and queue another."""
        state = self.players[player_id]
        if state.game_over:
            return
        board = self.player_panes[player_id].board_widget
        board.current_piece = state.next_piece
        if board.check_collision():
            self._handle_game_over(player_id)
            return
        board.update_display()
        self._queue_new_piece(player_id)
        self._broadcast_state()

    def _queue_new_piece(self, player_id: int) -> None:
        """Create the next piece and update the preview widget."""
        state = self.players[player_id]
        state.next_piece = TetrisPiece()
        self.player_panes[player_id].next_widget.update_piece(state.next_piece)

    def _refresh_score_widget(self, player_id: int) -> None:
        """Push current score state to the widget."""
        state = self.players[player_id]
        score_widget = self.player_panes[player_id].score_widget
        score_widget.score = state.score
        score_widget.level = state.level
        score_widget.lines = state.lines_cleared

    @staticmethod
    def _drop_interval_for_level(level: int) -> float:
        """Return the Tetris guideline drop speed for a level."""
        exponent = level - 1
        base = max(0.8 - exponent * 0.007, 0.001)
        return max(0.02, base**exponent)

    def _handle_game_over(self, player_id: int) -> None:
        """Show game over UI and stop input/timers."""
        self.players[player_id].game_over = True
        player_view = self.player_panes[player_id]
        player_view.container.add_class("game-over")
        player_view.overlay_widget.display = True
        if timer := self.game_timers.get(player_id):
            timer.pause()
        self.refresh_bindings()

    def _has_active_players(self) -> bool:
        return any(not state.game_over for state in self.players.values())

    def action_restart(self):
        """Restart the whole process when game over."""
        if any(state.game_over for state in self.players.values()):
            # Relaunch the current Python process with same args for a clean state.
            os.execl(sys.executable, sys.executable, *sys.argv)

    def action_help(self):
        """Show the help modal from the footer toolbar."""
        self.push_screen(HelpScreen(self.two_players))

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Disable live controls when the game has ended."""
        # ref: https://textual.textualize.io/guide/actions/#dynamic-actions
        if not (not self._has_active_players() and action in self.LIVE_ACTIONS):
            return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Play Tetris in your terminal.")
    parser.add_argument(
        "-2p", "--2players", action="store_true", dest="two_players", help="Enable local two-player mode."
    )
    network = parser.add_mutually_exclusive_group()
    network.add_argument("--server", action="store_true", help="Host a remote two-player game.")
    network.add_argument("--connect", metavar="URL", help="Connect to a remote game server.")
    parser.add_argument("--host", default="0.0.0.0", help="Server bind host (default: 0.0.0.0).")
    parser.add_argument("--port", type=int, default=8765, help="Server port (default: 8765).")
    args = parser.parse_args()
    network_mode = "server" if args.server else "client" if args.connect else None
    app = TetrisApp(
        two_players=args.two_players,
        network_mode=network_mode,
        connect_url=args.connect,
        server_host=args.host,
        server_port=args.port,
    )
    app.run()
