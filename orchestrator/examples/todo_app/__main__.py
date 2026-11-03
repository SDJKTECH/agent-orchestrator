"""Entry point for ``python -m orchestrator.examples.todo_app``."""

import sys

from orchestrator.examples.todo_app.cli import main

if __name__ == "__main__":
    sys.exit(main())
