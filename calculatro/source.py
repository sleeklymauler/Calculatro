"""Sources of live game state: the JSON file the mod writes."""

import json
import os
import sys
from pathlib import Path
from typing import Protocol

from .paths import default_state_path
from .state import SCHEMA, GameState, MalformedState, SchemaMismatch


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

    def _load(self) -> tuple[bytes, tuple[int, int]] | None:
        """The file's bytes and the key (mtime, size) of the very file they were read from.

        The key comes from the open handle, not the path: the mod replaces the
        file whole, so one replaced between a stat of the path and the read
        would otherwise be keyed as the older file, and read again on the next
        poll. None if the file is missing or locked.
        """
        try:
            with self.path.open("rb") as f:
                raw = f.read()
                st = os.fstat(f.fileno())
        except (FileNotFoundError, PermissionError):
            return None
        return raw, (st.st_mtime_ns, st.st_size)

    @staticmethod
    def _parse(raw: bytes) -> GameState:
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as e:
            raise MalformedState(f"{type(e).__name__}: {e}") from e
        return GameState.from_json(data)

    def read(self) -> GameState | None:
        """Return the current state, or None if the file is missing or locked.

        Raises SchemaMismatch for a file from another version of the mod, and
        MalformedState for one that isn't JSON (the mod replaces the file whole,
        so it's never seen half-written) or whose fields don't have the expected
        shapes.
        """
        loaded = self._load()
        return None if loaded is None else self._parse(loaded[0])

    def poll(self) -> GameState | None:
        """The state if the file changed since the last state returned, else None. Doesn't block."""
        try:
            st = self.path.stat()  # cheap check first: most polls find the file unchanged
        except (FileNotFoundError, PermissionError):
            return None
        if (st.st_mtime_ns, st.st_size) == self._last_key:
            return None
        loaded = self._load()
        if loaded is None:
            return None
        raw, key = loaded
        try:
            state = self._parse(raw)
        except SchemaMismatch as e:
            self._ignore(e)
            self._last_key = key  # not read again until it changes
            return None
        except MalformedState as e:
            self._reject(e)
            self._last_key = key
            return None
        self._last_key = key
        self._malformed = None  # report the next bad one, even if it's the same error
        return state
