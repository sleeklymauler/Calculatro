"""The odds file read by the mod's in-game panel (calculatro_odds.json).

Each payload echoes the id of the snapshot it answers, so the mod can tell
current odds from ones for an older state. A snapshot is answered first with
a `pending` payload, then with the odds once they're calculated.

The mod owns the list of what's tracked and names its rows itself, so each
row here is keyed by what the mod sent: a hand's name ("Three of a Kind", or
"Any target" for the combined row) or a card query's text. Text is ASCII.
"""

from __future__ import annotations

import json
import os
import time
from fractions import Fraction
from pathlib import Path
from typing import Any

from .report import Report
from .state import GameState

SCHEMA = 3  # `odds_schema` in calculatro.lua

# The panel fits 3 columns (`max_columns` in calculatro.lua; a 4th reaches
# the HUD sidebar) of 6 rows under the consumables. Target hands and card
# queries each fill columns of their own. The mod won't track more than fits
# (`Calculatro.columns_needed`).
ROWS_PER_COLUMN = 6
MAX_COLUMNS = 3


def columns_needed(targets: int, queries: int) -> int:
    """Columns the panel needs for this many targets and card queries."""
    hand_rows = targets + (targets > 1)  # with "Any target" above several
    return -(-hand_rows // ROWS_PER_COLUMN) - (-queries // ROWS_PER_COLUMN)

NO_DISCARDS = "No discards left"  # short: it sits beside the panel's title


def odds_path(state_path: Path) -> Path:
    """The odds file sits next to the state file."""
    return state_path.with_name("calculatro_odds.json")


def percent(p: Fraction) -> str:
    """One decimal, but never rounded to a misleading 0% or 100%."""
    if p == 0:
        return "0%"
    if p == 1:
        return "100%"
    value = float(p) * 100
    if value < 0.1:
        return "<0.1%"
    if value > 99.9:
        return ">99.9%"
    return f"{value:.1f}%"


def pending(state: GameState) -> dict[str, Any]:
    return {"schema": SCHEMA, "id": state.id, "pending": True}


def odds(state: GameState, report: Report | None) -> dict[str, Any]:
    """The panel's odds for `state`: none without a report (nothing tracked)."""
    payload: dict[str, Any] = {"schema": SCHEMA, "id": state.id, "targets": [], "cards": []}
    if report is None:
        return payload
    for row in report.targets:
        target: dict[str, Any] = {"key": row.name, "keep": percent(row.keep)}
        if row.discard is not None:
            target["discard"] = percent(row.discard)
            target["trend"] = (row.discard > row.keep) - (row.discard < row.keep)
        payload["targets"].append(target)
    for q, p in report.cards:
        card: dict[str, Any] = {"key": q.text}
        if p is not None:
            card["discard"] = percent(p)
        payload["cards"].append(card)
    if report.discarded and report.discards_left <= 0:
        payload["message"] = NO_DISCARDS
    return payload


def write(path: Path, payload: dict[str, Any], attempts: int = 20) -> bool:
    """Replace the odds file atomically. False if the mod kept it open throughout (it reads it briefly)."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    for _ in range(attempts):
        try:
            os.replace(tmp, path)
            return True
        except PermissionError:
            time.sleep(0.005)
    return False
