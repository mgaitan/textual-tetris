# Textual Tetris

[![ci](https://github.com/mgaitan/textual-tetris/workflows/ci/badge.svg)](https://github.com/mgaitan/textual-tetris/actions?query=workflow%3Aci)
[![docs](https://img.shields.io/badge/docs-blue.svg?style=flat)](https://mgaitan.github.io/textual-tetris/)
[![pypi version](https://img.shields.io/pypi/v/copier-update-placeholder-textual-tetris-20260912.svg)](https://pypi.org/project/copier-update-placeholder-textual-tetris-20260912/)
[![Changelog](https://img.shields.io/github/v/release/mgaitan/textual-tetris?include_prereleases&label=changelog)](https://github.com/mgaitan/textual-tetris/releases)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/mgaitan/textual-tetris/actions/workflows/ci.yml)
[![ty](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ty/main/assets/badge/v0.json)](https://github.com/astral-sh/ty)
[![License](https://img.shields.io/badge/license-BSD--3--Clause-blue.svg)](https://github.com/mgaitan/textual-tetris/blob/main/LICENSE)


A Tetris game for your terminal, built with the Textual framework.

## Quick Start

Run directly without installing via `uvx`:

```bash
uvx --with=copier-update-placeholder-textual-tetris-20260912 textual-tetris --help
```

When running from source, we use {term}`PYTHONPATH` in docs examples so the local package is importable without an install step.

```{richterm} env PYTHONPATH=../src uv run -m textris --help
:hide-command: true
```

To install the tool permanently, use:

```bash
uv tool install copier-update-placeholder-textual-tetris-20260912
```

## Documentation Map (Diataxis)

This project follows the [Diataxis](https://diataxis.fr/) framework:

- Tutorials: learning-oriented, step-by-step.
- How-to guides: goal-oriented operational procedures.
- Reference: factual, lookup-first technical details.
- Explanation: context, rationale, and design choices.


```{toctree}
:maxdepth: 2
:caption: Tutorials

getting_started.md
```

```{toctree}
:maxdepth: 2
:caption: How-to Guides

development_workflow.md
```

```{toctree}
:maxdepth: 2
:caption: Reference

configuration.md
```

```{toctree}
:maxdepth: 2
:caption: Explanation

about_the_docs.md
```

```{toctree}
:maxdepth: 2
:caption: Project Policies

../CONTRIBUTING.md
../CODE_OF_CONDUCT.md
```
