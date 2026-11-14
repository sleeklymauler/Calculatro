"""Where the mod and the app keep their files: a folder of their own in the game's save directory.

The mod declares the same names (`data_dir` and `data_files` in core.lua); `test_paths_match_the_mod` checks
they agree. The mod creates the folder as it starts, and moves the files of versions before it in.
"""

import os
from pathlib import Path

DATA_DIR = "calculatro_data"
STATE_FILE = "state.json"  # written by the mod
ODDS_FILE = "odds.json"  # written by the app, beside the state file
LOG_FILE = "calculatro.log"  # both add to it
SETTINGS_FILE = "settings.json"  # only the mod's


def data_dir(save_dir: Path) -> Path:
    """The folder in a save directory."""
    return save_dir / DATA_DIR


def default_state_path() -> Path:
    """The state file in the game's save directory."""
    return data_dir(Path(os.environ["APPDATA"]) / "Balatro") / STATE_FILE


def odds_path(state_path: Path) -> Path:
    """The odds file sits next to the state file."""
    return state_path.with_name(ODDS_FILE)


def log_path(state_path: Path) -> Path:
    """So does the log."""
    return state_path.with_name(LOG_FILE)
