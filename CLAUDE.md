# Agent Orchestrator — Claude Code Instructions

## Project purpose

This repository implements a Python-based orchestrator for running Claude Code agents against software development tasks in isolated Git worktrees.

## Technology stack

- Python 3.12+
- FastAPI
- SQLAlchemy
- Pydantic Settings
- Pytest
- Ruff
- Git worktrees
- Claude Code CLI

## Development rules

1. Inspect relevant source files and tests before changing code.
2. Follow existing project architecture and coding conventions.
3. Keep changes focused on the assigned task.
4. Add or update tests for behavioral changes.
5. Run relevant tests and Ruff checks when feasible.
6. Do not claim checks passed unless they were actually run and passed.
7. Do not commit, push, or rewrite Git history unless explicitly instructed.
8. Never expose secrets, API keys, credentials, or environment variable values.
9. Do not modify files outside the assigned worktree.
10. Avoid destructive Git commands and unrelated refactoring.

## Agent execution rules

- Treat the task title and description as the implementation requirements.
- Inspect acceptance criteria if provided.
- Preserve existing behavior unless the task explicitly requires a change.
- Report changed files, important implementation decisions, tests executed, and any remaining risks.
- If blocked, explain the blocker instead of inventing successful results.

## Quality expectations

Code should be maintainable, typed where appropriate, and covered by meaningful tests. Changes must be compatible with the repository's configured Ruff rules and pytest suite.