---
inclusion: always
---

# Code Quality Standards

Keep the codebase clean, typed, and free of dead code. These rules apply to every change.

## Clean code

- **No dead code.** Do not leave unused functions, classes, imports, variables, or commented-out blocks. If something is no longer used, delete it.
- **No speculative code.** Build only what the current task requires. Avoid unused abstractions, parameters, or config "for later."
- Follow the layered architecture from the design: `models` → `services` → `agent` → `ui`. Dependencies point one direction only: `ui` may use `agent`, `agent` may use `services`, everything may use `models`. Lower layers must never import from higher ones (e.g. `services` never imports `ui`).
- Use constructor injection for dependencies (as designed) so components stay testable.
- Prefer small, single-responsibility functions. Keep modules focused on the responsibility named in the design.

## Style and formatting

- **Formatter + linter:** `ruff` (it covers formatting, import sorting, and lint rules). Run `ruff format` and `ruff check --fix` before committing.
- Follow PEP 8 naming: `snake_case` for functions/variables, `PascalCase` for classes, `UPPER_SNAKE` for constants.
- Keep imports at module top; no inline imports except to break genuine circular dependencies (and prefer refactoring instead).

## Type safety

- All new functions and methods have type hints on parameters and return values.
- `mypy` must pass with no new errors. Use the `Protocol` interfaces defined in the design for service boundaries.
- Avoid `Any` unless interfacing with untyped third-party responses; when unavoidable, parse into a typed dataclass at the boundary.

## Docstrings and comments

- Public classes and functions get a concise docstring describing intent (the "why"), not a restatement of the code.
- Comment only non-obvious decisions. Delete TODOs before merging or convert them into tracked issues.

## Error handling

- Follow the graceful-degradation strategy in the design. External-service failures must never crash the app.
- Never expose stack traces, API keys, endpoints, or internal error codes to users — route through the `ErrorHandler` sanitizer.

## Definition of "clean" before commit

- `ruff check` and `ruff format --check` pass
- `mypy` passes
- No unused imports/variables (ruff F401/F841 clean)
- No leftover debug prints or commented-out code
