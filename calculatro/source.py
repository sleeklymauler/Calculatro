"""Sources of live game state. Phase 1 reads the JSON file the mod writes."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

from .state import GameState


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

    def read(self) -> GameState | None:
        """Return the current state, or None if the file is missing or mid-write."""
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
        state = self.read()
        if state is not None:
            self._last_key = key
        return state

    def watch(self) -> Iterator[GameState]:
        while True:
            state = self.poll()
            if state is not None:
                yield state
            time.sleep(self.poll_interval)
