"""The game state, odds and best discards as text, for `calculatro odds`: debugging a state file
by hand, or one a player sent with a bug report. The panel's own text is `overlay.py`'s."""

from fractions import Fraction

from . import overlay
from .report import Approx, Report, Value
from .search import Search
from .state import RANK_ORDER, RANK_SHORT, SUIT_SHORT, SUITS, GameState
from .worker import MEMORY, TIME, TooSlow


def summarize(state: GameState) -> str:
    if not state.in_run:
        return "Not in a run."
    ranks = state.rank_counts()
    suits = state.suit_counts()
    rank_line = "  ".join(f"{RANK_SHORT.get(r, r)}:{ranks[r]}" for r in RANK_ORDER)
    suit_line = "  ".join(f"{SUIT_SHORT[s]}:{suits[s]}" for s in SUITS)
    special = len(state.unseen) - sum(ranks.values())
    jokers = ", ".join(j.key + (" (debuffed)" if j.debuff else "") for j in state.jokers)
    lines = [
        f"State: {state.state}   Blind: {state.blind or '-'}"
        f"{' (disabled)' if state.blind_disabled else ''}",
        f"Hand size: {state.hand_size}   Hands left: {state.hands_left}   Discards left: {state.discards_left}",
        f"Hand ({len(state.hand)}): {' '.join(c.short() for c in state.hand)}",
        f"Unseen ({state.deck_size} cards in deck"
        + (f", {state.hidden} face down in hand" if state.hidden else "")
        + (f", {state.discarded_hidden} discarded face down" if state.discarded_hidden else "") + ")",
        f"  Ranks: {rank_line}",
        f"  Suits: {suit_line}" + (f"   (+{special} stone)" if special else ""),
        f"Discard pile: {len(state.discard)}   Jokers: {jokers or '-'}",
    ]
    return "\n".join(lines)


def _pct(p: Fraction) -> str:
    return f"{float(p) * 100:6.2f}%"


def _too_slow(limit: str) -> str:
    return "too slow (too much memory)" if limit == MEMORY else "too slow"


def _value(p: Value) -> str:
    """A value as `_pct` puts it, or as wide: "?" for one given up on, and an
    approximation as the panel shows it ("80%+*")."""
    if isinstance(p, TooSlow):
        return f"{overlay.TOO_SLOW:>7}"
    if isinstance(p, Approx):
        return f"{overlay.approximate(p) + '*':>7}"
    return _pct(p)


def _notes(keep: Value, after: Value | None) -> str:
    """What a line's values that aren't exact odds need saying: each
    approximation's range (with which it is, when there are two values, and
    "likely" if it's an estimate's), and why the exact odds were given up on."""
    named = [("keep", keep)] + ([("discard", after)] if after is not None else [])
    notes = [("*" if after is None else f"* {name}") + ("" if v.certain else " likely")
             + f" {_pct(v.low).strip()} to {_pct(v.high).strip()}" for name, v in named if isinstance(v, Approx)]
    slow = [v.limit for _, v in named if isinstance(v, TooSlow | Approx) and v.limit]
    if slow:
        notes.append(_too_slow(slow[0]))
    return f"  ({'; '.join(notes)})" if notes else ""


def format_report(report: Report) -> str:
    """The report as text: target hands, then card queries under "In hand"."""
    hint = ("Select cards in game to see the odds after discarding them." if report.redraw
            else "Last hand, with no discards left: nothing is drawn after it.")
    rows = [(row.name, row.keep, row.discard) for row in report.targets]
    cards = [(row.query.label, row.keep, row.discard) for row in report.cards]
    width = max(len(name) for name, _, _ in rows + cards)

    def lines_of(odds: list[tuple[str, Value, Value | None]]) -> list[str]:
        def line(name: str, keep: Value, after: Value | None) -> str:
            return (f"  {name:<{width}}  {_value(keep)}" + (f"  ->  {_value(after)}" if after is not None else "")
                    + _notes(keep, after))
        return [line(*odds_row) for odds_row in odds]

    # Card queries count the final hand like targets, but are labelled apart.
    body = lines_of(rows) + (["  In hand:"] if rows and cards else []) + lines_of(cards)

    if not report.discarded:
        return "\n".join(["Odds (current hand):", *body, hint])

    discarded = " ".join(c.short() for c in report.discarded)
    discard_desc = f"discard {discarded} and draw {report.discard_draws}"
    lines = [f"Odds: keep hand  ->  {discard_desc}", *body]
    if report.discards_left <= 0:
        lines.append("  (no discards left this round)")
    return "\n".join(lines)


def format_search(state: GameState, key: str, found: Search | TooSlow) -> str:
    if isinstance(found, TooSlow):
        return f"Best discard for {key}: {_too_slow(found.limit)}, given up on"
    best = found.options[0]
    what = f"discard {' '.join(state.hand[i].short() for i in best)}" if best else "keep the hand"
    more = [f"1 of {len(found.options)} options"] if len(found.options) > 1 else []
    odds = _pct(found.probability).strip()
    if (e := found.estimated) is not None:
        odds += "*"
        more.append(f"* by estimates, {_too_slow(e.limit or TIME)} to compare exactly"
                    + ("" if e.low == e.high else f"; likely {_pct(e.low).strip()} to {_pct(e.high).strip()}"))
    return f"Best discard for {key}: {what}, {odds}" + (f" ({'; '.join(more)})" if more else "")
