import argparse
import asyncio
import contextlib
import os
import sys
from dataclasses import dataclass
from typing import ClassVar

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import Footer, Input, Label, Static

from .game import (
    PlayerState,
    TetrisBoardState,
    TetrisPiece,
    coords_to_matrix,
    drop_interval_for_level,
)
from .network import TetrisClient
from .server import TetrisServer

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


class TetrisBoard(Static):
    """The main game board widget"""

    def __init__(self, player_id: int, model: TetrisBoardState | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.player_id = player_id
        self.model = model or TetrisBoardState()
        self.lock_timer: Timer | None = None

    @property
    def board_width(self) -> int:
        return self.model.width

    @property
    def board_height(self) -> int:
        return self.model.height

    @property
    def board(self) -> list[list[int | str]]:
        return self.model.cells

    @board.setter
    def board(self, value: list[list[int | str]]) -> None:
        self.model.cells = value

    @property
    def current_piece(self) -> TetrisPiece | None:
        return self.model.current_piece

    @current_piece.setter
    def current_piece(self, value: TetrisPiece | None) -> None:
        self.model.current_piece = value

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
        moved = self.model.move(dx, dy)
        if not moved:
            if dy > 0:
                self.schedule_lock()
            return False
        self.update_display()
        return True

    def is_grounded(self) -> bool:
        """Return whether the current piece can no longer move down."""
        return self.model.is_grounded()

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
        return self.model.check_collision()

    def lock_piece(self):
        """Fix the current piece to the board and spawn a new one."""
        if self.lock_timer:
            self.lock_timer.stop()
            self.lock_timer = None
        result = self.model.lock()
        if result is None:
            return
        app = self.app
        assert isinstance(app, TetrisApp)
        app.on_piece_locked(self.player_id, result.cleared_lines, result.piece_id)
        app.spawn_next_piece(self.player_id)

    def _clear_full_lines(self) -> int:
        """Remove filled rows and collapse the board."""
        return self.model.clear_full_lines()

    def rotate_piece(self) -> bool:
        """Rotate the current piece"""
        rotated = self.model.rotate()
        if rotated:
            self.update_display()
        return rotated


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
class PlayerView:
    """The mounted widgets used to render one player's game state."""

    board_widget: TetrisBoard
    next_widget: NextPieceWidget
    score_widget: ScoreWidget
    name_widget: Label
    container: Container
    overlay_widget: Static


class PlayerPane(Container):
    """Reusable split-screen pane with board, next piece, and score widgets."""

    def __init__(self, player_id: int, state: PlayerState, **kwargs) -> None:
        super().__init__(**kwargs)
        self.player_id = player_id
        self.name_widget = Label(state.name, classes="player-name")
        self.board_widget = TetrisBoard(player_id, state.board, classes="player-board")
        self.next_widget = NextPieceWidget(classes="player-panel next-widget")
        self.score_widget = ScoreWidget(classes="player-panel score-widget")
        self.overlay_widget = Static("GAME OVER\nPress R to restart", classes="game-over-overlay")

    def compose(self) -> ComposeResult:
        yield self.name_widget
        with Horizontal(classes="player-layout"):
            yield self.board_widget
            with Vertical(classes="player-sidebar"):
                yield self.next_widget
                yield self.score_widget
        yield self.overlay_widget

    def update_name(self, name: str) -> None:
        self.name_widget.update(name)


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

    def __init__(self, two_players: bool, same_controls: bool = False) -> None:
        super().__init__()
        self.two_players = two_players
        self.same_controls = same_controls

    def compose(self) -> ComposeResult:
        with Container(id="help-dialog"):
            yield Label("HELP", id="help-title")
            if self.same_controls:
                yield Label("Arrows: move & rotate", classes="help-line")
                yield Label("Space: hard drop", classes="help-line")
                yield Label("C: chat, N: name, J: join queue", classes="help-line")
            elif self.two_players:
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


class TextEntryScreen(ModalScreen[str | None]):
    """Small one-line editor used for chat and player names."""

    CSS = """
    TextEntryScreen {
        align: center bottom;
        background: transparent;
    }

    #text-entry-dialog {
        width: 64;
        height: auto;
        margin-bottom: 2;
        padding: 1 2;
        background: #16202b;
        border: round #7dd3fc;
    }

    #text-entry-title {
        color: #dbeafe;
        margin-bottom: 1;
    }
    """

    BINDINGS: ClassVar = (("escape", "cancel", "Cancel"),)

    def __init__(self, prompt: str, value: str = "") -> None:
        super().__init__()
        self.prompt = prompt
        self.value = value

    def compose(self) -> ComposeResult:
        with Container(id="text-entry-dialog"):
            yield Label(self.prompt, id="text-entry-title")
            yield Input(value=self.value, id="text-entry-input")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
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
    REMOTE_CLIENT_BINDINGS = (
        ("left", "remote_left", "Left"),
        ("right", "remote_right", "Right"),
        ("down", "remote_down", "Down"),
        ("up", "remote_rotate", "Rotate"),
        ("space", "remote_hard_drop", "Drop"),
    )

    OTHER_BINDINGS = (
        ("h", "help", "Help"),
        ("c", "chat", "Chat"),
        ("n", "name", "Name"),
        ("shift+n", "player_two_name", "P2 Name"),
        ("j", "join_queue", "Queue"),
        ("ctrl+q", "quit", "Quit"),
        ("r", "restart", "Restart"),
        ("ctrl+s", "screenshot", "Screenshot"),
    )

    BINDINGS: ClassVar = OTHER_BINDINGS
    LIVE_ACTIONS: ClassVar = tuple(
        binding[1] for binding in SINGLE_PLAYER_BINDINGS + PLAYER_ONE_BINDINGS + PLAYER_TWO_BINDINGS
    )

    def __init__(
        self,
        two_players: bool = False,
        network_mode: str | None = None,
        connect_url: str | None = None,
        player_name: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        if network_mode not in {None, "client"}:
            raise ValueError(f"Unsupported network mode: {network_mode}")
        if network_mode == "client" and not connect_url:
            raise ValueError("A connect URL is required in client mode")

        self.network_mode = network_mode
        self.connect_url = connect_url
        self.two_players = two_players or network_mode is not None
        self.player_name = player_name
        self.client_id: int | None = None
        self.client_name = player_name or ""
        self.network_role = "spectator"
        self.controlled_player: int | None = None
        self.queue_position: int | None = None
        self.game_timers = {}
        self.game_started = False
        self.state_revision = 0
        self._network_client = TetrisClient(connect_url) if connect_url else None
        self._network_tasks = set()
        self.lines_per_level = 10
        player_ids = (1, 2) if self.two_players else (1,)
        self.players = {
            player_id: PlayerState(player_name if player_id == 1 and player_name else f"Player {player_id}")
            for player_id in player_ids
        }
        if network_mode == "client":
            bindings = self.REMOTE_CLIENT_BINDINGS
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
        layers: base overlay;
    }

    #playfield {
        width: auto;
        height: auto;
        align: center middle;
        layer: base;
    }

    #waiting-overlay {
        layer: overlay;
        width: 38;
        height: auto;
        padding: 2 3;
        content-align: center middle;
        text-align: center;
        text-style: bold;
        color: #f8fafc;
        background: #070c14 94%;
        border: round #7dd3fc;
        display: none;
    }

    #game-container.waiting #waiting-overlay {
        display: block;
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

    .player-name {
        width: 100%;
        height: 1;
        text-align: center;
        text-style: bold;
        color: #f8fafc;
        margin-bottom: 1;
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
        with Container(id="game-container"):
            with Horizontal(id="playfield"):
                if self.two_players:
                    yield PlayerPane(1, self.players[1], id="player-one", classes="player-pane")
                    yield PlayerPane(2, self.players[2], id="player-two", classes="player-pane")
                else:
                    with Container(id="board-container"):
                        yield Label(self.players[1].name, id="single-player-name", classes="player-name")
                        yield TetrisBoard(1, self.players[1].board, id="board")
                        yield Static("GAME OVER\nPress R to restart", id="game-over-overlay")
                    with Vertical(id="sidebar"):
                        with Container(id="next-piece-container"):
                            yield NextPieceWidget(id="next-piece")
                        with Container(id="score-container"):
                            yield ScoreWidget(id="score-widget")
            if self.network_mode == "client":
                yield Static("CONNECTING TO SERVER", id="waiting-overlay")
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
                    first_player.name_widget,
                    first_player,
                    first_player.overlay_widget,
                ),
                2: PlayerView(
                    second_player.board_widget,
                    second_player.next_widget,
                    second_player.score_widget,
                    second_player.name_widget,
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
                    self.query_one("#single-player-name", Label),
                    board_container,
                    self.query_one("#game-over-overlay", Static),
                )
            }

        if self.network_mode == "client":
            self._set_waiting(True)
        else:
            # Give widgets time to mount, then start the local game.
            self.call_after_refresh(self._start_game)

        if self.network_mode == "client":
            self.run_worker(self._run_client())

    def _update_all_displays(self):
        """Update all game displays after widgets are mounted"""
        for player_id in self.players:
            self.spawn_next_piece(player_id)
            self._refresh_score_widget(player_id)

    def _set_waiting(self, waiting: bool) -> None:
        self.query_one("#game-container", Container).set_class(waiting, "waiting")

    def _start_game(self) -> None:
        if self.game_started:
            return
        self.game_started = True
        self._update_all_displays()
        for player_id in self.players:
            self.start_game_timer(player_id)

    def _broadcast_state(self) -> None:
        """Local games do not need to publish rendered state."""

    def _broadcast_event(self, event: dict) -> None:
        """Local games do not publish protocol events."""

    def _schedule_network_task(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._network_tasks.add(task)
        task.add_done_callback(self._network_tasks.discard)

    async def _run_client(self) -> None:
        try:
            assert self._network_client is not None
            await self._network_client.run(self._handle_network_message)
        except Exception as error:
            self.notify(f"Remote game disconnected: {error}", severity="error")
            self._set_waiting(True)
            self.query_one("#waiting-overlay", Static).update("DISCONNECTED")

    async def _handle_network_message(self, payload: dict[str, object]) -> None:
        message_type = payload.get("type")
        if message_type == "welcome":
            client_id = payload.get("client_id")
            self.client_id = client_id if isinstance(client_id, int) else None
            self.client_name = str(payload.get("name", ""))
            self._apply_role(payload)
            self.notify("Connected to remote game")
            if self.player_name and self._network_client is not None:
                await self._network_client.send({"type": "name", "name": self.player_name})
        elif message_type == "role":
            self._apply_role(payload)
        elif message_type == "state":
            self._apply_state(payload)
        elif message_type == "player_renamed" and payload.get("client_id") == self.client_id:
            self.client_name = str(payload["name"])
        elif message_type == "chat":
            sender = payload["sender"]
            message = payload.get("message")
            if isinstance(sender, dict) and isinstance(sender.get("name"), str) and isinstance(message, str):
                self.notify(f"{sender.get('name')}: {message}")

    def _send_network(self, payload: dict[str, object]) -> None:
        if self._network_client:
            self._schedule_network_task(self._network_client.send(payload))

    def _send_input(self, action: str) -> None:
        self._send_network({"type": "input", "action": action})

    def _apply_role(self, payload: dict) -> None:
        self.network_role = payload["role"]
        self.controlled_player = payload.get("player_id")
        self.queue_position = payload.get("queue_position")
        if self.network_role == "player":
            self.notify(f"Playing as Player {self.controlled_player}")
        elif self.queue_position:
            self.notify(f"Spectating · queue position {self.queue_position}")
        else:
            self.notify("Spectating")
        self.refresh_bindings()

    def _apply_state(self, payload: dict) -> None:
        for player_id, player_payload in payload["players"].items():
            player_id = int(player_id)
            state = self.players[player_id]
            board = self.player_panes[player_id].board_widget
            board.board = player_payload["board"]
            board.current_piece = TetrisPiece.from_payload(player_payload["current_piece"])
            next_piece = TetrisPiece.from_payload(player_payload["next_piece"])
            if next_piece is not None:
                state.next_piece = next_piece
            state.name = player_payload["name"]
            state.score = player_payload["score"]
            state.level = player_payload["level"]
            state.lines_cleared = player_payload["lines"]
            state.game_over = player_payload["game_over"]
            view = self.player_panes[player_id]
            view.name_widget.update(state.name)
            view.overlay_widget.display = state.game_over
            view.container.set_class(state.game_over, "game-over")
            board.update_display()
            if next_piece is not None:
                view.next_widget.update_piece(next_piece)
            self._refresh_score_widget(player_id)
        status = payload["status"]
        waiting = status != "running"
        self._set_waiting(waiting)
        if waiting:
            if self.queue_position:
                message = f"SPECTATING\n\nQueue position {self.queue_position}"
            elif self.network_role == "player":
                message = "WAITING FOR OPPONENT"
            else:
                message = "SPECTATING"
            self.query_one("#waiting-overlay", Static).update(message)

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

    def action_remote_left(self) -> None:
        self._send_input("left")

    def action_remote_right(self) -> None:
        self._send_input("right")

    def action_remote_down(self) -> None:
        self._send_input("down")

    def action_remote_rotate(self) -> None:
        self._send_input("rotate")

    def action_remote_hard_drop(self) -> None:
        self._send_input("drop")

    def action_chat(self) -> None:
        if self.network_mode == "client":
            self.push_screen(TextEntryScreen("Chat message"), self._submit_chat)

    def _submit_chat(self, message: str | None) -> None:
        if message:
            self._send_network({"type": "chat", "message": message})

    def action_name(self) -> None:
        current_name = self.client_name if self.network_mode == "client" else self.players[1].name
        self.push_screen(TextEntryScreen("Player name", current_name), self._submit_name)

    def _submit_name(self, name: str | None) -> None:
        if not name or not (name := " ".join(name.split())[:24]):
            return
        if self.network_mode == "client":
            self.client_name = name
            self._send_network({"type": "name", "name": name})
            return
        self.players[1].name = name
        self.player_panes[1].name_widget.update(name)

    def action_player_two_name(self) -> None:
        if self.two_players and self.network_mode is None:
            self.push_screen(TextEntryScreen("Player 2 name", self.players[2].name), self._submit_player_two_name)

    def _submit_player_two_name(self, name: str | None) -> None:
        if name and (name := " ".join(name.split())[:24]):
            self.players[2].name = name
            self.player_panes[2].name_widget.update(name)

    def action_join_queue(self) -> None:
        if self.network_mode == "client" and self.network_role == "spectator":
            self._send_network({"type": "join"})

    def on_piece_locked(self, player_id: int, cleared_lines: int, piece_id: int) -> None:
        """Update score/level/timing after a piece locks."""
        state = self.players[player_id]
        if state.record_lock(cleared_lines, self.lines_per_level):
            self.start_game_timer(player_id)

        self._refresh_score_widget(player_id)
        self._broadcast_event(
            {
                "type": "piece_locked",
                "player": player_id,
                "piece_id": piece_id,
                "cleared_lines": cleared_lines,
            }
        )
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
        return drop_interval_for_level(level)

    def _handle_game_over(self, player_id: int) -> None:
        """Show game over UI and stop input/timers."""
        self.players[player_id].game_over = True
        player_view = self.player_panes[player_id]
        player_view.container.add_class("game-over")
        player_view.overlay_widget.display = True
        if timer := self.game_timers.get(player_id):
            timer.pause()
        self.refresh_bindings()
        self._broadcast_state()

    def _has_active_players(self) -> bool:
        return any(not state.game_over for state in self.players.values())

    def action_restart(self):
        """Restart the whole process when game over."""
        if any(state.game_over for state in self.players.values()):
            # Relaunch the current Python process with same args for a clean state.
            os.execl(sys.executable, sys.executable, *sys.argv)

    def action_help(self):
        """Show the help modal from the footer toolbar."""
        self.push_screen(HelpScreen(self.two_players, same_controls=self.network_mode is not None))

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Disable live controls when the game has ended."""
        if action == "player_two_name" and not (self.two_players and self.network_mode is None):
            return False
        if action == "chat" and self.network_mode != "client":
            return False
        if action == "join_queue" and not (self.network_mode == "client" and self.network_role == "spectator"):
            return False
        # ref: https://textual.textualize.io/guide/actions/#dynamic-actions
        if not (not self._has_active_players() and action in self.LIVE_ACTIONS):
            return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Play Tetris in your terminal.")
    parser.add_argument(
        "-2p", "--2players", action="store_true", dest="two_players", help="Enable local two-player mode."
    )
    network = parser.add_mutually_exclusive_group()
    network.add_argument("--server", action="store_true", help="Run a headless multiplayer server.")
    network.add_argument("--connect", metavar="URL", help="Connect to a remote game server.")
    parser.add_argument("--host", default="0.0.0.0", help="Server bind host (default: 0.0.0.0).")
    parser.add_argument("--port", type=int, default=8765, help="Server port (default: 8765).")
    parser.add_argument("--name", help="Player name shown to other clients.")
    args = parser.parse_args()
    if args.server:
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(TetrisServer(args.host, args.port).serve_forever())
        return
    app = TetrisApp(
        two_players=args.two_players,
        network_mode="client" if args.connect else None,
        connect_url=args.connect,
        player_name=args.name,
    )
    app.run()
