from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent
LOGS_DIR = BASE_DIR / "logs"


def configure_logging(enable_log_file: bool) -> Optional[Path]:
    """Configure the root logger once, before any task runs. Returns the
    log file path, or None if file logging is disabled.

    `enable_log_file` is the master on/off switch for writing a
    timestamped log file under LOGS_DIR -- owned and passed in by
    main.py, so it's the one place in the project you flip while
    debugging. This function itself has no opinion on whether logging
    should be on, just how to wire it up when it is.

    This replaces per-task logging setup. Individual task modules should
    just do `logger = logging.getLogger(__name__)` and log normally --
    they don't need to know about this flag, LOGS_DIR, or handler wiring
    at all.

    Uses delay=True on the FileHandler, so the file is only actually
    created on disk the moment something is first logged. This is what
    stops empty log files from piling up: a task that runs cleanly and
    never logs anything (common for tasks that simply don't have any
    warning/info logging in their path) won't leave a 0-byte file behind
    just because logging was enabled.

    Safe to call more than once in the same process (e.g. tests) -- any
    handler from a previous call is removed first so runs don't
    accumulate duplicate handlers/duplicate log lines.
    """
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    for old_handler in root.handlers[:]:
        if getattr(old_handler, "_project_logging_handler", False):
            root.removeHandler(old_handler)
            old_handler.close()

    if not enable_log_file:
        # Without any handler, Python's logging module falls back to its
        # "handler of last resort" and prints WARNING+ straight to
        # stderr. A NullHandler suppresses that so disabling file logging
        # actually means "no logging output", not "logging output, just
        # unformatted and on stderr".
        null_handler = logging.NullHandler()
        null_handler._project_logging_handler = True
        root.addHandler(null_handler)
        return None

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"{datetime.now():%Y-%m-%d_%H-%M-%S}.log"

    handler = logging.FileHandler(log_path, encoding="utf-8", delay=True)
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    handler._project_logging_handler = True
    root.addHandler(handler)

    return log_path
