"""Entry point for `python -m hls_caselaw_downloader`."""

import sys

from hls_caselaw_downloader.cli import main

if __name__ == "__main__":
    sys.exit(main())
