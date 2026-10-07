"""CLI: `python -m calculatro watch -t trips -c "2+ queens"` prints live game state and odds."""

from __future__ import annotations

import argparse
import sys
from fractions import Fraction
from pathlib import Path

from .cards import CardQuery, parse_query
from .engine import DiscardOutcome, evaluate_cards, evaluate_discard
from .hands import TARGETS, Target, combine, get_target
from .source import FileStateSource
from .state import RANK_ORDER, RANK_SHORT, SUIT_SHORT, SUITS, GameState


def summarize(state: GameState) -> str:
    if not state.in_run:
        return "Not in a run."
    ranks = state.rank_counts()
    suits = state.suit_counts()
    rank_line = "  ".join(f"{RANK_SHORT.get(r, r)}:{ranks[r]}" for r in RANK_ORDER)
    suit_line = "  ".join(f"{SUIT_SHORT[s]}:{suits[s]}" for s in SUITS)
    special = len(state.deck) - sum(ranks.values())
    lines = [
        f"State: {state.state}   Blind: {state.blind or '-'}",
        f"Hand size: {state.hand_size}   Hands left: {state.hands_left}   Discards left: {state.discards_left}",
        f"Hand ({len(state.hand)}): {' '.join(c.short() for c in state.hand)}",
        f"Deck ({len(state.deck)} cards remaining)",
        f"  Ranks: {rank_line}",
        f"  Suits: {suit_line}" + (f"   (+{special} stone)" if special else ""),
        f"Discard pile: {len(state.discard)}   Jokers: {', '.join(state.jokers) or '-'}",
    ]
    return "\n".join(lines)


def _pct(p: Fraction) -> str:
    return f"{float(p) * 100:6.2f}%"


def odds(state: GameState, targets: list[Target], queries: list[CardQuery] = ()) -> str:
    """Target odds for keeping the hand vs. discarding the in-game selection,
    plus card-query odds for the cards drawn after that discard.

    Keeping the hand draws nothing (unless it isn't full), so card queries only
    have a discard column.
    """
    if not state.in_run or not state.hand or not (targets or queries):
        return ""
    hint = "Select cards in game to see the odds after discarding them."

    named = [(t.name, t) for t in targets]
    if len(targets) > 1:
        named.insert(0, ("Any target", combine(targets)))
    keep = [(name, evaluate_discard(state, t, [])) for name, t in named]

    selected = state.selected
    if not selected:
        if not keep:
            return hint
        width = max(len(name) for name, _ in keep)
        draws = keep[0][1].draws
        lines = ["Odds (current hand" + (f", drawing {draws}" if draws else "") + "):"]
        lines += [f"  {name:<{width}}  {_pct(o.probability)}" for name, o in keep]
        return "\n".join([*lines, hint])

    after = [evaluate_discard(state, t, selected) for _, t in named]
    card_rows = [(q.label, evaluate_cards(state, q, selected)) for q in queries]
    first = (after or [o for _, o in card_rows])[0]
    discarded = " ".join(c.short() for c in first.discarded)
    width = max(len(name) for name, _ in keep + card_rows)
    # Card rows leave the keep column blank so the discard column lines up.
    blank = " " * len(f"{_pct(Fraction(0))}  ->  ") if keep else ""

    keep_desc = "keep hand" + (f" (draw {keep[0][1].draws})" if keep and keep[0][1].draws else "")
    discard_desc = f"discard {discarded} and draw {first.draws}"
    lines = [f"Odds: {keep_desc}  ->  {discard_desc}" if keep else f"Odds: {discard_desc}"]
    lines += [f"  {name:<{width}}  {_pct(k.probability)}  ->  {_pct(a.probability)}"
              for (name, k), a in zip(keep, after)]
    # Card odds cover only the drawn cards; label them apart from the whole-hand
    # target rows. With no targets the header already says what's drawn.
    if card_rows and keep:
        lines.append("  Drawn cards:")
    lines += [f"  {name:<{width}}  {blank}{_pct(o.probability)}" for name, o in card_rows]
    if state.discards_left <= 0:
        lines.append("  (no discards left this round)")
    return "\n".join(lines)


def _target(name: str) -> Target:
    try:
        return get_target(name)
    except KeyError as e:
        raise argparse.ArgumentTypeError(str(e.args[0])) from None


def _query(text: str) -> CardQuery:
    try:
        return parse_query(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="calculatro")
    sub = parser.add_subparsers(dest="cmd", required=True)
    watch = sub.add_parser("watch", help="print the game state whenever it changes")
    watch.add_argument("--file", type=Path, help="state file (default: %%APPDATA%%/Balatro/calculatro_state.json)")
    watch.add_argument("-t", "--target", type=_target, action="append", default=[],
                       help=f"hand type to aim for; repeat for several ({', '.join(TARGETS)}, "
                            "or trips, quads, royal)")
    watch.add_argument("-c", "--cards", type=_query, action="append", default=[],
                       help='odds for the drawn cards, e.g. "queen", "2+ queens", "no face cards", '
                            '"exactly 1 spade", "2 of clubs"; repeat for several')
    args = parser.parse_args(argv)
    # Suit symbols: Windows pipes/redirects default to cp1252.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.cmd == "watch":
        source = FileStateSource(args.file)
        print(f"Watching {source.path} (Ctrl+C to stop)")
        try:
            for state in source.watch():
                out = summarize(state)
                if args.target or args.cards:
                    out += "\n" + odds(state, args.target, args.cards)
                print("\n" + "=" * 72 + "\n" + out, flush=True)
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
