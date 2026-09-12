# Contributing

Contributions are welcome, and they are greatly appreciated! Every little bit helps, and credit will always be given.

## Environment setup

Fork and clone the repository, then:

```bash
cd textual-tetris
uv sync
```

You'll need to install [uv](https://github.com/astral-sh/uv).

You now have the dependencies installed. Run the game with:

```bash
uv run textual-tetris
```

## Development

Create a new branch, edit the code or documentation, then run:

```bash
make qa
make test
make docs
```
