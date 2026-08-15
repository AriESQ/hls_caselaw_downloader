"""Logging configuration: plain lines by default, optional JSON for future CI parsing.

Deliberately append-only, no carriage-return/tqdm-style redraw — that's
illegible in captured CI logs, and plain lines are what makes a heartbeat
useful for "is this actually still running" from a log tail.
"""

from __future__ import annotations

import json
import logging
import sys
import time


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(verbose: bool = False, json_logs: bool = False) -> None:
    root = logging.getLogger("hls_caselaw_downloader")
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.handlers.clear()

    handler = logging.StreamHandler(stream=sys.stderr)
    if json_logs:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.propagate = False
