# textual-tetris

textual-tetris is a minimalist Tetris clone written with [Textual](https://textual.textualize.io/), an amazing TUI framework for Python. It focuses on compact components, colorized blocks, and a responsive keyboard feel in the terminal.

## Running
The easiest way is using `uvx` (part of [uv](https://docs.astral.sh/uv/)): 

```bash
uvx textual-tetris
```

## The mandatory screenshot 

![](screenshot.svg)



## Gameplay
- Blocks follow the classic rules: move left/right, rotate, soft drop, and hard drop.
- Every locked piece awards a small bonus; clearing 1–4 lines follows the traditional scoring table. Levels increase automatically based on the number of cleared lines, and the drop interval accelerates per level.
- The `Next` widget previews the upcoming piece so you can plan ahead, and the score widget keeps score/level/lines visible at all times.

### Controls
| Key | Action |
| --- | --- |
| `← / A` | Move left |
| `→ / D` | Move right |
| `↓ / S` | Soft drop |
| `↑ / W` | Rotate |
| `Space` | Hard drop |
| `Ctrl+Q` | Quit |
| `R` | Restart after a game-over |
