"""Allow ``python -m orchestrator.examples.csv_summariser``."""

import sys

from orchestrator.examples.csv_summariser.cli import main

if __name__ == "__main__":
    sys.exit(main())
