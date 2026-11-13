"""The log kept when the game starts the app, which has no console (pythonw)."""

import io
import os
import platform
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import TextIO

from . import __version__

MAX_BYTES = 256 * 1024  # small enough to attach to a bug report


def _home_pattern(home: str) -> re.Pattern[str] | None:
    """Matches the home folder however a path is written: in another case, with forward slashes, or with the
    doubled backslashes of a repr (as in an OSError's message). None if there's no home folder to hide."""
    parts = re.split(r"[\\/]+", home.rstrip("\\/"))
    if len(parts) < 2:
        return None
    return re.compile(r"[\\/]+".join(map(re.escape, parts)) + r"""(?=[\\/'"\s,:;)\]]|$)""", re.IGNORECASE)


class _Log(io.TextIOWrapper):
    """A text file that writes the home folder as `~`. Players paste the log into public issues, and the home
    folder's path holds their Windows username: tracebacks show it, as a release's Python lives under it."""

    def __init__(self, file: io.BufferedIOBase, home: re.Pattern[str] | None, **options):
        super().__init__(file, **options)
        self._home = home

    def write(self, text: str) -> int:
        return super().write(self._home.sub("~", text) if self._home else text)


def open_log(path: Path) -> TextIO:
    """The log for this session, added to what's there so restarting the game doesn't lose the traceback a
    player needs to report. Its size is checked only here: Windows can't rename a file that's open, and errors
    already logged aren't logged again (`run_watch`), so a single session adds little.

    One over `MAX_BYTES` becomes `calculatro.log.1`, replacing any before it. Only a full log moves, so clean
    restarts can't push a traceback out. If it can't move (something else has it open), it keeps growing.
    """
    try:
        if path.stat().st_size > MAX_BYTES:
            os.replace(path, path.with_name(path.name + ".1"))
    except OSError:
        pass
    # Unlike a console's stderr, a file's errors are strict by default: writing a lone surrogate would raise.
    return _Log(open(path, "ab"), _home_pattern(os.path.expanduser("~")),
                encoding="utf-8", errors="backslashreplace", line_buffering=True)


def header(state_path: Path, game_pid: int) -> str:
    """The line starting a session's entries, with what a bug report needs to know about this install."""
    started = datetime.now().isoformat(sep=" ", timespec="seconds")
    return (f"=== Calculatro {__version__} started {started}: Python {platform.python_version()} "
            f"({sys.executable}), pid {os.getpid()}, game pid {game_pid}, watching {state_path}")
