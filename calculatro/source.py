"""Sources of live game state: the JSON file the mod writes."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Protocol

from .state import SCHEMA, GameState, MalformedState, SchemaMismatch


def default_state_path() -> Path:
    return Path(os.environ["APPDATA"]) / "Balatro" / "calculatro_state.json"


class StateSource(Protocol):
    def poll(self) -> GameState | None: ...


class FileStateSource:
    """The mod's state file, polled for a new GameState whenever it changes."""

    def __init__(self, path: Path | None = None):
        self.path = path or default_state_path()
        self._last_key: tuple[int, int] | None = None
        self._mismatch: object = None  # the last schema reported by _ignore
        self._malformed: str | None = None  # the last error reported by _reject

    def _ignore(self, e: SchemaMismatch) -> None:
        """Report a snapshot from another version of the mod, once per schema.

        It's usually the previous version's file, left from the last session
        until the mod writes its first snapshot, or the mod wasn't reinstalled
        after an update. Either way the app waits for one it can read.
        """
        if e.schema == self._mismatch:
            return
        self._mismatch = e.schema
        if isinstance(e.schema, int) and e.schema < SCHEMA:
            hint = "if it persists, re-run scripts/install_mod.ps1 to update the mod"
        else:
            hint = "if it persists, update the app to match the mod"
        print(f"Ignoring {self.path.name}: it has schema {e.schema!r}, and this app reads {SCHEMA} "
              f"({hint}).", file=sys.stderr, flush=True)

    def _reject(self, e: MalformedState) -> None:
        """Report a snapshot that couldn't be parsed, once while the same error repeats."""
        if str(e) == self._malformed:
            return
        self._malformed = str(e)
        print(f"Ignoring {self.path.name}: it isn't a valid snapshot ({e}).", file=sys.stderr, flush=True)

    def read(self) -> GameState | None:
        """Return the current state, or None if the file is missing or locked.

        Raises SchemaMismatch for a file from another version of the mod, and
        MalformedState for one that isn't JSON (the mod replaces the file whole,
        so it's never seen half-written) or whose fields don't have the expected
        shapes.
        """
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, PermissionError):
            return None
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as e:
            raise MalformedState(f"{type(e).__name__}: {e}") from e
        return GameState.from_json(data)

    def poll(self) -> GameState | None:
        """The state if the file changed since the last state returned, else None. Doesn't block."""
        try:
            st = self.path.stat()
        except (FileNotFoundError, PermissionError):
            return None
        key = (st.st_mtime_ns, st.st_size)
        if key == self._last_key:
            return None
        try:
            state = self.read()
        except SchemaMismatch as e:
            self._ignore(e)
            self._last_key = key  # not read again until it changes
            return None
        except MalformedState as e:
            self._reject(e)
            self._last_key = key
            return None
        if state is not None:
            self._last_key = key
            self._malformed = None  # report the next bad one, even if it's the same error
        return state
