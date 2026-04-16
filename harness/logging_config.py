"""Shared logging setup for harness CLI entry points.

Keeps log configuration consistent across scripts so that users can:
  - Silence progress chatter during automated runs (--log-level WARNING)
  - See full protocol traces when debugging (--log-level DEBUG)
  - Mirror logs to a file alongside any JSONL traffic captures

All harness modules use ``logging.getLogger(__name__)``; CLI entry points
are responsible for calling ``configure_logging`` once at startup.
"""

import logging
import sys

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s  %(message)s"
_DATEFMT = "%H:%M:%S"


def configure_logging(level: str = "INFO", log_file: str | None = None) -> None:
    """Configure the root logger for a CLI entry point.

    Args:
        level: Standard level name (DEBUG/INFO/WARNING/ERROR/CRITICAL).
        log_file: Optional path; if set, logs are also appended here.
    """
    resolved = getattr(logging, level.upper(), None)
    if not isinstance(resolved, int):
        raise ValueError(f"Unknown log level: {level!r}")

    root = logging.getLogger()
    # Replace any prior handlers so repeated calls stay idempotent.
    for handler in list(root.handlers):
        root.removeHandler(handler)

    root.setLevel(resolved)
    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    root.addHandler(stream)

    if log_file:
        fh = logging.FileHandler(log_file)
        fh.setFormatter(formatter)
        root.addHandler(fh)
