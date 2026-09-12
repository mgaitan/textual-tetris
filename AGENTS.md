Project: Textual-Tetris

A minimalist Tetris clone written with Textual TUI framework for Python. 

Repository: https://github.com/mgaitan/textual-tetris


## Stack

- **Python:** Python 3.15. Check main dependencies in `pyproject.toml`.
- **Tests:** pytest, factory-boy, pytest-mock. `make test` to run tests. or `uv run pytest`
- **Productivity:** Dependencies managed with `uv` via `pyproject.toml`;
- **lint/format:** Ruff.

## Python preferences

- Modern and idiomatic practices that emphasize clarity and predictable behavior. Examples of modern features:
   - Pathlib for file operations
   - Data model methods (like __len__, __add__, etc.)
   - Stdlib or pydantic dataclasses
   - Advanced itertools
   - Pattern matching
   - Walrus operator
   - Enums subclasses
- Dependency changes use `uv add` or `uv remove`
- Use `uv run - <<'PY' ...` instead `python - <<'PY'` for inline scripts
- Docstrings in Markdown ("myst") format, expressing intentions rather than implementation details.
  Make references to other code if appropriate. Eg: "See also `{py:func}`other_module.helper_function`.".
- Explicit and robust type annotations using built-in generics (`list`, `dict`, etc.), union types with `|`, etc.
- Prefer flat code: use early returns, guard clauses, fixtures over context managers on tests, etc.

## Git/GitHub preferences

- Ensure you are in a proper branch/worktree for each new feature or bugfix.
- Never commit or push automatically unless instructed otherwise.
- Never merge your own PRs without explicit maintainer approval in chat, even if CI is green.
- Prefer `gh` CLI for all interactions with GitHub if possible. Eg. Use it to open PRs / manage issues.
- For `gh pr` interactions, prefer `--body-file` with a temporary file created in the system temp directory (for example via `mktemp` or `$TMPDIR`), and delete the file after the command completes.
- For issue categorization, use GitHub labels instead of title prefixes like `Bug:`, `Feat:`, or `Docs:`.
- Before assigning labels, inspect labels already used in the target repository and follow that taxonomy first.
- If labels are missing or incomplete, use this baseline set: `bug`, `enhancement`, `documentation`, `maintenance`, `refactor`, `test`, `ci`, `dependencies`, `security`, `performance`, `ux`, `question`.
- Consider mentioning yourself in the commits as co-author if you helped enough. Use the standard Git co-author trailer:
  "Co-authored-by: Name <email>".
  For common AI collaborators, use:
  - "Co-authored-by: Codex <codex@openai.com>".
  - "Co-authored-by: Claude <noreply@anthropic.com>".
- For releases, prefer the repository `Makefile` targets instead of ad-hoc commands. Use `make release` to run checks and publish a release. When preparing a version-bump PR, use `make bump` (which currently bumps the minor version only).

## Language preferences

- The public language is English: all committable text and GitHub interactions must be in simple English (including documentation, comments, docstrings, commit messages, PR descriptions, etc.).
- However, when interacting with the developer in chat, respond in the language they use.
- Avoid sexist or exclusionary language. Always prefer gender-neutral phrasing.
