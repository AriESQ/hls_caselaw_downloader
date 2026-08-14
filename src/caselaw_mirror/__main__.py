"""Entry point for `python -m caselaw_mirror`."""

import sys

from caselaw_mirror.cli import main

if __name__ == "__main__":
    sys.exit(main())
