"""The odds file read by the mod's in-game panel (calculatro_odds.json).

Each payload echoes the id of the snapshot it answers, so the mod can tell
current odds from ones for an older state. A snapshot is answered first with
a `pending` payload, then with the odds as they're calculated: `partial`
while some values are still to come, then all of them.

A value (a row's `keep` or `discard`) that isn't exact odds has a mark beside
it (`keep_mark`, `discard_mark`) saying what it is (`mark`): still to come, an
approximation, or given up on. Without either, the row has no such value
(nothing is selected, say).

The mod owns the list of what's tracked and names its rows itself, so each
row here is keyed by what the mod sent: a hand's name ("Three of a Kind", or
"Any target" for the combined row) or a card query's text. Text is ASCII.

Once a best-discard search the mod asked for has finished, every payload
carries its result (`search`), until the hand changes: the mod selects the
cards, which sends a new snapshot and so new payloads, and those mustn't lose
the options it cycles through.
"""

import json
import math
import os
import time
from fractions import Fraction
from pathlib import Path
from typing import Any

from .report import DRAWS, SAMPLE, Approx, Pending, Report, Value, interval
from .search import Search
from .state import GameState
from .worker import TIME, TooSlow

SCHEMA = 9  # `odds_schema` in the mod's core.lua

# The panel fits 3 columns (`max_columns` in the mod's tracked.lua; a 4th reaches
# the HUD sidebar) of 6 rows under the consumables. Target hands and card
# queries each fill columns of their own. The mod won't track more than fits
# (`Calculatro.columns_needed`).
ROWS_PER_COLUMN = 6
MAX_COLUMNS = 3


def columns_needed(targets: int, queries: int) -> int:
    """Columns the panel needs for this many targets and card queries."""
    hand_rows = targets + (targets > 1)  # with "Any target" above several
    return -(-hand_rows // ROWS_PER_COLUMN) - (-queries // ROWS_PER_COLUMN)


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


def _header(state: GameState) -> dict[str, Any]:
    """The schema, and the snapshot's id if it has one: the odds file never contains `null`."""
    return {"schema": SCHEMA} if state.id is None else {"schema": SCHEMA, "id": state.id}


def pending(state: GameState) -> dict[str, Any]:
    return {**_header(state), "pending": True}


TOO_SLOW = "?"  # a value given up on


def approximate(value: Approx) -> str:
    """An approximation in whole percents. From bounds, they're certain: its
    lower bound, rounded down, with a "+" ("80%+"), or below 1%, its upper
    bound with a "<" ("<6%"). An estimate is rounded ("84%"), but never to a
    misleading 0% or 100% ("<1%", ">99%"). The mod adds the asterisk."""
    if value.estimate is not None:
        rounded = math.floor(value.estimate * 100 + Fraction(1, 2))
        return "<1%" if rounded < 1 else ">99%" if rounded > 99 else f"{rounded}%"
    low = math.floor(value.low * 100)
    if low >= 1 or value.high == 1:
        return f"{low}%+"
    return f"<{math.floor(value.high * 100) + 1}%"


def mark(value: Value) -> dict[str, Any] | None:
    """What a value that isn't exact odds is, for the panel: still to come
    (`{"state": "pending"}`), an approximation (`{"state": "refining"}` while
    the exact odds are still being worked out, `"final"` once given up on,
    with its `how`, its range `low` to `high`, and if final the `limit`),
    or given up on with no approximation (`{"state": "final", "limit":
    "time"}` or `"memory"`, shown as TOO_SLOW). None for exact odds."""
    if isinstance(value, Pending):
        return {"state": "pending"}
    if isinstance(value, TooSlow):
        return {"state": "final", "limit": value.limit}
    if isinstance(value, Approx):
        out = {"state": "refining" if value.limit is None else "final", "how": value.how,
               "low": percent(value.low), "high": percent(value.high)}
        if value.limit is not None:
            out["limit"] = value.limit
        return out
    return None


def _put(out: dict[str, Any], name: str, value: Value) -> None:
    """`value` as `out[name]`, with its mark as `out[name + "_mark"]` (see mark)."""
    if isinstance(value, Fraction):
        out[name] = percent(value)
        return
    if isinstance(value, TooSlow):
        out[name] = TOO_SLOW
    elif isinstance(value, Approx):
        out[name] = approximate(value)
    out[name + "_mark"] = mark(value)


def _trend(keep: Value, discard: Value) -> int | None:
    """Whether discarding does better (1) or worse (-1) than keeping, or the same
    (0). None unless that's certain: both exact odds, or approximations whose
    ranges don't overlap."""
    a, b = interval(keep), interval(discard)
    if a is None or b is None:
        return None
    if b[0] > a[1]:
        return 1
    if b[1] < a[0]:
        return -1
    return 0 if a[0] == a[1] == b[0] == b[1] else None


def odds(state: GameState, report: Report | None) -> dict[str, Any]:
    """The panel's odds for `state`: none without a report (nothing tracked)."""
    payload: dict[str, Any] = {**_header(state), "targets": [], "cards": []}
    if report is None:
        return payload
    if report.partial:
        payload["partial"] = True
    for row in report.targets:
        target: dict[str, Any] = {"key": row.name}
        _put(target, "keep", row.keep)
        if row.discard is not None:
            _put(target, "discard", row.discard)
            if (trend := _trend(row.keep, row.discard)) is not None:
                target["trend"] = trend
        payload["targets"].append(target)
    for row in report.cards:
        # No keep value: the panel shows only the discard's, coloured by the trend.
        card: dict[str, Any] = {"key": row.query.text}
        if row.discard is not None:
            _put(card, "discard", row.discard)
            if (trend := _trend(row.keep, row.discard)) is not None:
                card["trend"] = trend
        payload["cards"].append(card)
    return payload


def search(key: str, nonce: int, found: Search | TooSlow | None) -> dict[str, Any]:
    """The result of the mod's search request `nonce` for the row `key`: the
    discards to cycle through, as hand indices (0-based), best first. No
    options if there was nothing to search (`key` isn't tracked), or if it was
    given up on, which its `mark` says (see mark). One given up on that
    answered from estimates has its options, and a `mark` saying so: `{state:
    "final", how, low, high, limit}`, the range being the best option's
    likely odds (one value if they're exact)."""
    if isinstance(found, TooSlow):
        return {"nonce": nonce, "key": key, "options": [], "mark": mark(found)}
    out: dict[str, Any] = {"nonce": nonce, "key": key, "options": found.options if found else []}
    if found is not None and (e := found.estimated) is not None:
        out["mark"] = {"state": "final", "how": DRAWS if e.draws else SAMPLE, "low": percent(e.low),
                       "high": percent(e.high), "limit": e.limit or TIME}
    return out


def write(path: Path, payload: dict[str, Any], attempts: int = 20) -> bool:
    """Replace the odds file atomically. False if a file stayed locked (a
    `PermissionError`): the mod kept it open throughout (it reads it briefly), or
    something else (antivirus) had it open, which soon passes. Any other `OSError`
    (a missing folder, a full disk) is raised, since trying again at once won't help."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        for _ in range(attempts):
            try:
                os.replace(tmp, path)
                return True
            except PermissionError:
                time.sleep(0.005)
    except PermissionError:
        pass
    return False
