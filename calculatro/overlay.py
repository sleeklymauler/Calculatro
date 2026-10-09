"""The odds file read by the mod's in-game panel (calculatro_odds.json).

Each payload echoes the id of the snapshot it answers, so the mod can tell
current odds from ones for an older state. A snapshot is answered first with
a `pending` payload, then with the odds once they're calculated.

Text is plain ASCII, except for the suit symbols (♠♥♣♦): Balatro's font has
none, so the mod draws those as the game's own suit icons.
"""

from __future__ import annotations

import json
import os
import re
import time
from fractions import Fraction
from pathlib import Path
from typing import Any

from .cards import CardQuery
from .report import Report
from .state import SUIT_SHORT, GameState

SCHEMA = 2  # `odds_schema` in calculatro.lua

# Most targets and card queries together. The panel fits 3 columns
# (`max_columns` in calculatro.lua; a 4th reaches the HUD sidebar) of 6 rows
# under the consumables: 18 rows, two of which may be the "Any target" row and
# the "Drawn cards" subheader.
MAX_TRACKED = 16

NO_DISCARDS = "No discards left"  # short: it sits beside the panel's title
NO_TARGETS = "No targets set: run with -t or -c"

# Suit words in query names ("Spade", "Foil Heart") and their symbols.
_SUIT_SYMBOLS = {suit[:-1]: symbol for suit, symbol in SUIT_SHORT.items()}
_SUIT_WORD = re.compile(r"\b(" + "|".join(_SUIT_SYMBOLS) + r")\b")

# Hand names too long for the panel's name column at full size.
SHORT_NAMES = {
    "Three of a Kind": "Trips",
    "Four of a Kind": "Quads",
    "Five of a Kind": "5 of a Kind",
    "Straight Flush": "Str. Flush",
}


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


def card_label(query: CardQuery) -> str:
    """Short label for a card query: "Queen: 2+", "♠: 0-1", "Q♠: 1+", "Face card: none"."""
    name = _SUIT_WORD.sub(lambda m: _SUIT_SYMBOLS[m[1]], query.filter.name)
    if query.count == 0 and query.op in ("==", "<="):
        count = "none"
    elif query.op == ">=":
        count = f"{query.count}+"
    elif query.op == "<=":
        count = f"0-{query.count}"
    else:
        count = str(query.count)
    return f"{name}: {count}"


def pending(state: GameState) -> dict[str, Any]:
    return {"schema": SCHEMA, "id": state.id, "pending": True}


def odds(state: GameState, report: Report | None, configured: bool = True) -> dict[str, Any]:
    """The panel's contents for `state`; `configured` is whether any targets or queries are set."""
    payload: dict[str, Any] = {"schema": SCHEMA, "id": state.id, "targets": [], "cards": []}
    if report is None:
        if not configured and state.in_run and state.hand:
            payload["message"] = NO_TARGETS
        return payload
    payload["selected"] = len(report.discarded)
    for row in report.targets:
        target: dict[str, Any] = {"name": SHORT_NAMES.get(row.name, row.name), "keep": percent(row.keep)}
        if row.discard is not None:
            target["discard"] = percent(row.discard)
            target["trend"] = (row.discard > row.keep) - (row.discard < row.keep)
        payload["targets"].append(target)
    for q, p in report.cards:
        card: dict[str, Any] = {"name": card_label(q)}
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
