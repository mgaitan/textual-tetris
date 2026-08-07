# textual-tetris

textual-tetris is a minimalist Tetris clone written with [Textual](https://textual.textualize.io/), an amazing TUI framework for Python. It focuses on compact components, colorized blocks, and a responsive keyboard feel in the terminal.

Original Blog post: https://mgaitan.github.io/en/posts/textual-tetris/


## Running
The easiest way is using `uvx` (part of [uv](https://docs.astral.sh/uv/)): 

```bash
uvx textual-tetris
```

For local multiplayer:

```bash
uvx textual-tetris --2players
```

## Remote games

Start a headless server, then connect two players:

```bash
uvx textual-tetris --server --port 8765
uvx textual-tetris --connect ws://HOST:8765 --name Ada
uvx textual-tetris --connect ws://HOST:8765 --name Grace
```

The server does not own a player or render a UI. The first two clients become active players and start the match.
Later connections can watch the game and wait in a FIFO queue. When a player disconnects or loses, the winner
stays and the next queued client is promoted.

Every interactive client uses arrow keys to move and rotate and Space to hard drop. Press `N` to change your
visible name. Press `C` to open a one-line chat input; Enter sends, Escape cancels, and incoming messages appear as
non-blocking notifications. A player who becomes a spectator can press `J` to join the challenger queue again.

## Agentic player

An automated AI (like Codex) player can use the same WebSocket without rendering the terminal UI. 

On connection, the server sends a `welcome` message describing protocol v2, the assigned role and player id,
valid messages and events, and the piece catalog. It then sends revisioned `state` snapshots containing both
players and the complete connection roster.

The welcome also includes the complete piece catalog: each rotation code maps to its four
relative block coordinates, so the client does not need to know how pieces are encoded internally. 

Every message
has a monotonically increasing `revision`; ignore older messages. A `piece_locked` event identifies the locked
`piece_id`, and an input with an `id` receives an `ack` event, so an agent can wait for confirmed state changes.
Send actions as JSON, for example:

```json
{"type": "input", "id": 42, "action": "left"}
```

Agents can also set their name and use chat:

```json
{"type": "name", "name": "Codex"}
{"type": "chat", "message": "good luck"}
```

The same server supports agent-versus-agent games: launch only `--server`, then connect two automated WebSocket
clients. Spectators receive state and chat events but their gameplay inputs are rejected.


## Screenshots

### Single-player

![](screenshot.png)

### Two-player

![](screenshot-2players.png)



## Gameplay
- Blocks follow the classic rules: move left/right, rotate, soft drop, and hard drop.
- Every locked piece awards a small bonus; clearing 1–4 lines follows the traditional scoring table. Levels increase automatically based on the number of cleared lines, and the drop interval accelerates per level.
- The `Next` widget previews the upcoming piece so you can plan ahead, and the score widget keeps score/level/lines visible at all times.

### Controls
In the default one-player mode, use arrows or `W/A/S/D` to move and rotate, and `Space` or `Q` to hard drop.

In remote mode, use arrows and Space. `C` opens chat, `N` changes your player name, and `J` joins the queue.

With `--2players`:
| Key | Action |
| --- | --- |
| `A / D / S / W` (Player 1) | Move left/right, soft drop, rotate |
| `Q` (Player 1) | Hard drop |
| `← / → / ↓ / ↑` (Player 2) | Move left/right, soft drop, rotate |
| `Space` (Player 2) | Hard drop |
| `N / Shift+N` | Change Player 1 / Player 2 name |
| `Ctrl+Q` | Quit |
| `R` | Restart after a game-over |
