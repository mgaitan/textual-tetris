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
from textual.widgets import Label, Static

# Compact hex-based shape definitions (4x4 grid)
PIECES = {
    "O": {"color": "yellow", "codes": ["56a9", "6a95", "a956", "956a"]},
    "I": {"color": "cyan", "codes": ["4567", "26ae", "ba98", "d951"]},
    "J": {"color": "blue", "codes": ["0456", "2159", "a654", "8951"]},
    "L": {"color": "bright_yellow", "codes": ["2654", "a951", "8456", "0159"]},
    "T": {"color": "magenta", "codes": ["1456", "6159", "9654", "4951"]},
    "Z": {"color": "red", "codes": ["0156", "2659", "a954", "8451"]},
    "S": {"color": "green", "codes": ["1254", "a651", "8956", "0459"]},
}

def coords_to_matrix(coords):
    """Turn a list of (x, y) coords into a minimal 2D matrix (for previews)."""
    width = max(x for x, _ in coords) + 1
    height = max(y for _, y in coords) + 1
    matrix = [[0 for _ in range(width)] for _ in range(height)]
    for x, y in coords:
        matrix[y][x] = 1
    return matrix


class TetrisPiece:
    def __init__(self, piece_type=None):
        if piece_type is None:
            piece_type = random.choice(list(PIECES.keys()))

        self.type = piece_type
        self.color = PIECES[piece_type]["color"]
        self.codes = deque(PIECES[piece_type]["codes"])
        self.code = self.codes[0]
        self.x = 4  # Start at center of board
        self.y = 0

    @property
    def shape(self) -> list(tuple[int, int]):
        """expand the Hex code into a list of (x, y) coords relative to a 4x4 grid """
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

    def rotate(self):
        self.codes.rotate(-1)
        self.code = self.codes[0]

    def undo_rotate(self):
        self.codes.rotate(1)
        self.code = self.codes[0]


class TetrisBoard(Static):
    """The main game board widget"""

    def __init__(self, width=10, height=20, **kwargs):
        super().__init__(**kwargs)
        self.board_width = width
        self.board_height = height
        self.board = [[0 for _ in range(width)] for _ in range(height)]
        self.current_piece = TetrisPiece()

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
        text.append("┌" + "─" * (self.board_width * 2) + "┐\n", style="bold white")

        # Render each row
        for row in display_board:
            text.append("│", style="bold white")
            for cell in row:
                if cell == 0:
                    text.append("  ")
                else:
                    text.append("██", style=f"bold {cell}")
            text.append("│\n", style="bold white")

        # Add bottom border
        text.append("└" + "─" * (self.board_width * 2) + "┘", style="bold white")

        return text

    def update_display(self):
        """Update the board display"""
        board_display = self.query_one("#board-display", Static)
        board_display.update(self.render_board())

    def move_piece(self, dx, dy):
        """Move the current piece"""
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
        dim = max(shape_h, shape_w, 4)

        text = Text()
        # top border
        text.append("┌" + "─" * (dim * 2) + "┐\n", style="dim white")

        # how many qnk rows above/below
        top_pad = (dim - shape_h) // 2
        bottom_pad = dim - shape_h - top_pad

        # helper for an empty row
        for _ in range(top_pad):
            text.append("│", style="dim white")
            text.append("  " * dim)
            text.append("│\n", style="dim white")

        # each shape row, centered horizontally
        for row in shape_matrix:
            # left padding
            left = (dim - len(row)) // 2
            right = dim - len(row) - left
            text.append("│", style="dim white")
            text.append("  " * left)
            for cell in row:
                if cell:
                    text.append("██", style=f"bold {color}")
                else:
                    text.append("  ")
            text.append("  " * right)
            text.append("│\n", style="dim white")

        for _ in range(bottom_pad):
            text.append("│", style="dim white")
            text.append("  " * dim)
            text.append("│\n", style="dim white")

        # bottom border
        text.append("└" + "─" * (dim * 2) + "┘", style="dim white")
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


class TetrisApp(App):
    """Main Tetris application"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.game_timer = None
        self.drop_interval = 1.0  # Seconds between automatic drops
        self.score = 0
        self.level = 1
        self.lines_cleared = 0
        self.lines_per_level = 10
        self.next_piece = TetrisPiece()
        self.game_over = False

    CSS = """
    Screen {
        background: $background;
    }

    #game-container {
        width: 100%;
        height: 100%;
        background: $surface;
        border: heavy $primary;
        padding: 1;
    }

    #board-container {
        width: 30;
        margin: 1;
        padding: 1;
        background: $panel;
        border: solid $accent;
        layers: base overlay;
    }

    #board-display {
        margin: 1;
        padding: 1;
        layer: base;
    }

    #sidebar {
        width: 20;
        margin-left: 2;
        padding: 1;
    }

    #next-piece-container {
        margin-bottom: 2;
        padding: 1;
        background: $panel;
        border: solid $accent;
    }

    #score-container {
        height: 9;
        padding: 1;
        background: $panel;
        border: solid $accent;
    }

    #game-over-overlay {
        layer: overlay;
        content-align: center middle;
        text-style: bold;
        color: $error;
        background: $background 90%;
        display: none;
    }

    .game-over #game-over-overlay {
        display: block;
    }

    .section-title {
        text-align: center;
        text-style: bold;
        color: $accent;
    }

    .score-number {
        text-align: center;
        text-style: bold;
        color: $warning;
        margin-bottom: 1;
        content-align: center middle;
    }

    #controls {
        margin-top: 2;
        padding: 1;
        background: $panel;
        border: solid $accent;
        height: 12;
    }

    #title {
        text-align: center;
        text-style: bold;
        color: $primary;
        margin-bottom: 1;
    }
    """

    BINDINGS = (
        ("left,a", "move_left", "Move Left"),
        ("right,d", "move_right", "Move Right"),
        ("down,s", "move_down", "Move Down"),
        ("up,w", "rotate", "Rotate"),
        ("space", "hard_drop", "Drop"),
        ("ctrl+q", "quit", "Quit"),
        ("r", "restart", "Restart"),
        ("ctrl+s", "screenshot", "Screenshot"),
    )

    def compose(self) -> ComposeResult:
        with Container(id="game-container"):
            yield Label("🎮 TETRIS 🕹", id="title")
            with Horizontal():
                with Container(id="board-container"):
                    yield TetrisBoard(id="board")
                    yield Static("GAME OVER\nPress R to restart", id="game-over-overlay")
                with Vertical(id="sidebar"):
                    with Container(id="next-piece-container"):
                        yield NextPieceWidget(id="next-piece")
                    with Container(id="score-container"):
                        yield ScoreWidget(id="score-widget")
                    with Container(id="controls"):
                        yield Label("CONTROLS", classes="section-title")
                        yield Label("↑/W: Rotate")
                        yield Label("←/A: Move Left")
                        yield Label("→/D: Move Right")
                        yield Label("↓/S: Move Down")
                        yield Label("Space: Drop")
                        yield Label("Ctrl+Q: Quit")

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
        """Move piece left"""
        if not self.game_over:
            self.board.move_piece(-1, 0)

    def action_move_right(self):
        """Move piece right"""
        if not self.game_over:
            self.board.move_piece(1, 0)

    def action_move_down(self):
        """Move piece down"""
        if not self.game_over:
            self.board.move_piece(0, 1)

    def action_rotate(self):
        """Rotate piece"""
        if not self.game_over:
            self.board.rotate_piece()

    def action_hard_drop(self):
        """Instantly drop the piece to the lowest valid position."""
        if not self.game_over:
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
            self.drop_interval = max(0.1, 1.0 - (self.level - 1) * 0.1)
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

    def _handle_game_over(self):
        """Show game over UI and stop input/timers."""
        self.game_over = True
        if self.game_timer:
            self.game_timer.pause()
        self.board_container.add_class("game-over")
        self.game_over_overlay.display = True

    def action_restart(self):
        """Restart the whole process when game over."""
        if self.game_over:
            # Relaunch the current Python process with same args for a clean state.
            os.execl(sys.executable, sys.executable, *sys.argv)

def main():
    app = TetrisApp()
    app.run()
