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
    def watch(self) -> Iterator[GameState]: ...


class FileStateSource:
    """Polls the mod's state file and yields a GameState whenever it changes."""

    def __init__(self, path: Path | None = None, poll_interval: float = 0.1):
        self.path = path or default_state_path()
        self.poll_interval = poll_interval

    def read(self) -> GameState | None:
        """Return the current state, or None if the file is missing or mid-write."""
        try:
            raw = self.path.read_text(encoding="utf-8")
            return GameState.from_json(json.loads(raw))
        except (FileNotFoundError, PermissionError, json.JSONDecodeError):
            return None

    def watch(self) -> Iterator[GameState]:
        last_key: tuple[int, int] | None = None
        while True:
            try:
                st = self.path.stat()
                key = (st.st_mtime_ns, st.st_size)
            except FileNotFoundError:
                key = None
            if key is not None and key != last_key:
                state = self.read()
                if state is not None:
                    last_key = key
                    yield state
            time.sleep(self.poll_interval)
