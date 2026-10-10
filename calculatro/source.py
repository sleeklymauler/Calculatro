"""Sources of live game state: the JSON file the mod writes."""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

from .state import SCHEMA, GameState, SchemaMismatch


def default_state_path() -> Path:
    return Path(os.environ["APPDATA"]) / "Balatro" / "calculatro_state.json"


class StateSource(Protocol):
    def read(self) -> GameState | None: ...
    def poll(self) -> GameState | None: ...
    def watch(self) -> Iterator[GameState]: ...


class FileStateSource:
    """Polls the mod's state file and yields a GameState whenever it changes."""

    def __init__(self, path: Path | None = None, poll_interval: float = 0.1):
        self.path = path or default_state_path()
        self.poll_interval = poll_interval
        self._last_key: tuple[int, int] | None = None
        self._mismatch: object = None  # the last schema reported by _ignore

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

    def read(self) -> GameState | None:
        """Return the current state, or None if the file is missing or mid-write.

        Raises SchemaMismatch for a file from another version of the mod.
        """
        try:
            raw = self.path.read_text(encoding="utf-8")
            return GameState.from_json(json.loads(raw))
        except (FileNotFoundError, PermissionError, json.JSONDecodeError):
            return None

    def poll(self) -> GameState | None:
        """The state if the file changed since the last state returned, else None. Doesn't block."""
        try:
            st = self.path.stat()
        except FileNotFoundError:
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
        if state is not None:
            self._last_key = key
        return state

    def watch(self) -> Iterator[GameState]:
        while True:
            state = self.poll()
            if state is not None:
                yield state
            time.sleep(self.poll_interval)
