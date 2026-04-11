import contextlib
import os
import random
import sys
from collections import deque

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Footer, Label, Static

# Compact hex-based shape definitions (4x4 grid)
PIECES = {
    # Keep the familiar Guideline hue families, but use explicit tones that stay
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
MAX_PREVIEW_DIM = 4
PREVIEW_RENDER_WIDTH = MAX_PREVIEW_DIM * CELL_WIDTH + 2
NEXT_CONTAINER_WIDTH = PREVIEW_RENDER_WIDTH + 4
SIDEBAR_WIDTH = NEXT_CONTAINER_WIDTH


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
    def shape(self) -> list(tuple[int, int]):
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

    def __init__(self, width=10, height=20, **kwargs):
        super().__init__(**kwargs)
        self.board_width = width
        self.board_height = height
        self.board = [[0 for _ in range(width)] for _ in range(height)]
        self.current_piece: TetrisPiece | None = None

    def compose(self) -> ComposeResult:
        yield Static(self.render_board(), id="board-display")

    def on_mount(self):
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

    def update_display(self):
        """Update the board display"""
        board_display = self.query_one("#board-display", Static)
        board_display.update(self.render_board())

    def move_piece(self, dx, dy):
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
            # If we were moving down, lock the piece in place
            if dy > 0:
                self.lock_piece()
            return False

        self.update_display()
        return True

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
        for board_x, board_y in self.current_piece.blocks:
            if 0 <= board_x < self.board_width and 0 <= board_y < self.board_height:
                self.board[board_y][board_x] = self.current_piece.color

        # Clear any completed lines
        cleared = self._clear_full_lines()

        # Notify app about scoring/level updates.
        # NOTE: If this raises (e.g., during shutdown), allow the error rather than hiding it.
        self.app.on_piece_locked(cleared)

        # Spawn a new piece
        self.app.spawn_next_piece()

    def _clear_full_lines(self):
        """Remove filled rows and collapse the board."""
        new_rows = [row for row in self.board if not all(row)]
        cleared = self.board_height - len(new_rows)
        if cleared:
            # Add empty rows at the top
            self.board = [[0 for _ in range(self.board_width)] for _ in range(cleared)] + new_rows
        else:
            self.board = new_rows
        return cleared

    def rotate_piece(self):
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

    def __init__(self, **kwargs):
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

    def update_piece(self, piece):
        """Update the next piece"""
        self.next_piece = piece
        next_display = self.query_one("#next-piece-display", Static)
        next_display.update(self.render_next_piece())


class ScoreWidget(Static):
    """Widget to display score and level"""

    score = reactive(0)
    level = reactive(1)
    lines = reactive(0)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        yield Label(f"SCORE {self.score}", id="score-value", classes="section-title")
        yield Label(f"LEVEL {self.level}", id="level-value", classes="score-number")
        yield Label(f"LINES {self.lines}", id="lines-value", classes="section-title")

    def watch_score(self, score: int):
        with contextlib.suppress(NoMatches):
            self.query_one("#score-value", Label).update(f"SCORE {score}")

    def watch_level(self, level: int):
        with contextlib.suppress(NoMatches):
            self.query_one("#level-value", Label).update(f"LEVEL {level}")

    def watch_lines(self, lines: int):
        with contextlib.suppress(NoMatches):
            self.query_one("#lines-value", Label).update(f"LINES {lines}")


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

    BINDINGS = [("escape,h,enter,space", "dismiss", "Close help")]

    def compose(self) -> ComposeResult:
        with Container(id="help-dialog"):
            yield Label("HELP", id="help-title")
            yield Label("↑ / W   Rotate", classes="help-line")
            yield Label("← / A   Move left", classes="help-line")
            yield Label("→ / D   Move right", classes="help-line")
            yield Label("↓ / S   Soft drop", classes="help-line")
            yield Label("Space   Hard drop", classes="help-line")
            yield Label("R       Restart after game over", classes="help-line")
            yield Label("Ctrl+Q  Quit", classes="help-line")
            yield Label("Esc / H to close", id="help-close")

    def action_dismiss(self) -> None:
        self.dismiss(None)


class TetrisApp(App):
    """Main Tetris application"""

    LIVE_BINDINGS = (
        ("left,a", "move_left", "Move Left"),
        ("right,d", "move_right", "Move Right"),
        ("down,s", "move_down", "Move Down"),
        ("up,w", "rotate", "Rotate"),
        ("space", "hard_drop", "Drop"),
    )

    OTHER_BINDINGS = (
        ("h", "help", "Help"),
        ("ctrl+q", "quit", "Quit"),
        ("r", "restart", "Restart"),
        ("ctrl+s", "screenshot", "Screenshot"),
    )

    BINDINGS = LIVE_BINDINGS + OTHER_BINDINGS
    LIVE_ACTIONS = tuple(binding[1] for binding in LIVE_BINDINGS)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.game_timer = None
        self.drop_interval = 1.0
        self.score = 0
        self.level = 1
        self.lines_cleared = 0
        self.lines_per_level = 10
        self.next_piece = TetrisPiece()
        self.game_over = False

    CSS = """
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

    #game-over-overlay {
        layer: overlay;
        content-align: center middle;
        text-style: bold;
        color: #f8fafc;
        background: #070c14 82%;
        display: none;
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
    """.replace("__BOARD_CONTAINER_WIDTH__", str(BOARD_CONTAINER_WIDTH)).replace(
        "__SIDEBAR_WIDTH__", str(SIDEBAR_WIDTH)
    ).replace("__NEXT_CONTAINER_WIDTH__", str(NEXT_CONTAINER_WIDTH))

    def compose(self) -> ComposeResult:
        with Container(id="game-container"):
            with Horizontal(id="playfield"):
                with Container(id="board-container"):
                    yield TetrisBoard(id="board")
                    yield Static("GAME OVER\nPress R to restart", id="game-over-overlay")
                with Vertical(id="sidebar"):
                    with Container(id="next-piece-container"):
                        yield NextPieceWidget(id="next-piece")
                    with Container(id="score-container"):
                        yield ScoreWidget(id="score-widget")
        yield Footer()

    def on_mount(self):
        """Initialize the game"""
        self.board = self.query_one("#board", TetrisBoard)
        self.next_piece_widget = self.query_one("#next-piece", NextPieceWidget)
        self.score_widget = self.query_one("#score-widget", ScoreWidget)
        self.board_container = self.query_one("#board-container")
        self.game_over_overlay = self.query_one("#game-over-overlay", Static)

        # Give widgets time to mount, then update displays
        self.call_after_refresh(self._update_all_displays)

        # Start the game timer for automatic piece dropping
        self.start_game_timer()

    def _update_all_displays(self):
        """Update all game displays after widgets are mounted"""
        # Ensure the board starts with the queued next piece
        self.board.current_piece = self.next_piece
        self.board.update_display()

        # Queue and show the following piece
        self._queue_new_piece()
        self._refresh_score_widget()

    def start_game_timer(self):
        """Start the automatic piece dropping timer"""
        if self.game_timer:
            self.game_timer.pause()
        self.game_timer = self.set_interval(self.drop_interval, self.auto_drop)
        if self.game_over and self.game_timer:
            self.game_timer.pause()

    def auto_drop(self):
        """Automatically drop the current piece"""
        if not self.game_over:
            # move_piece will lock the piece if it can't go lower
            self.board.move_piece(0, 1)

    def action_move_left(self):
        self.board.move_piece(-1, 0)

    def action_move_right(self):
        self.board.move_piece(1, 0)

    def action_move_down(self):
        self.board.move_piece(0, 1)

    def action_rotate(self):
        self.board.rotate_piece()

    def action_hard_drop(self):
        """Instantly drop the piece to the lowest valid position."""
        while self.board.move_piece(0, 1):
            pass

    def on_piece_locked(self, cleared_lines: int):
        """Update score/level/timing after a piece locks."""
        if cleared_lines:
            # Classic scoring scale per number of lines cleared at once
            line_score = {1: 100, 2: 300, 3: 500, 4: 800}.get(cleared_lines, cleared_lines * 200)
            self.score += line_score * self.level
            self.lines_cleared += cleared_lines
        else:
            # Small reward just for locking a piece
            self.score += 10

        # Level up every N cleared lines
        new_level = max(1, 1 + self.lines_cleared // self.lines_per_level)
        if new_level != self.level:
            self.level = new_level
            # Speed up drop interval; clamp to a reasonable minimum
            self.drop_interval = self._drop_interval_for_level(self.level)
            self.start_game_timer()

        self._refresh_score_widget()

    def spawn_next_piece(self):
        """Move queued next piece to the board and queue another."""
        if self.game_over:
            return
        self.board.current_piece = self.next_piece
        if self.board.check_collision():
            self._handle_game_over()
            return
        self.board.update_display()
        self._queue_new_piece()

    def _queue_new_piece(self):
        """Create the next piece and update the preview widget."""
        self.next_piece = TetrisPiece()
        self.next_piece_widget.update_piece(self.next_piece)

    def _refresh_score_widget(self):
        """Push current score state to the widget."""
        score_widget = self.score_widget
        score_widget.score = self.score
        score_widget.level = self.level
        score_widget.lines = self.lines_cleared

    @staticmethod
    def _drop_interval_for_level(level: int) -> float:
        """Return the official Tetris guideline drop speed for the given level."""
        exponent = level - 1
        base = 0.8 - exponent * 0.007
        # Clamp base to avoid negative intervals at high levels
        base = max(base, 0.001)
        return max(0.02, base**exponent)

    def _handle_game_over(self):
        """Show game over UI and stop input/timers."""
        self.game_over = True
        if self.game_timer:
            self.game_timer.pause()
        self.board_container.add_class("game-over")
        self.game_over_overlay.display = True
        self.refresh_bindings()

    def action_restart(self):
        """Restart the whole process when game over."""
        if self.game_over:
            # Relaunch the current Python process with same args for a clean state.
            os.execl(sys.executable, sys.executable, *sys.argv)

    def action_help(self):
        """Show the help modal from the footer toolbar."""
        self.push_screen(HelpScreen())

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Disable live controls when the game has ended."""
        # ref: https://textual.textualize.io/guide/actions/#dynamic-actions
        if not (self.game_over and action in self.LIVE_ACTIONS):
            return True


def main():
    app = TetrisApp()
    app.run()
