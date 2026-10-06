"""CLI: `python -m calculatro watch --target trips` prints live game state and odds."""

from __future__ import annotations

import argparse
import sys
from fractions import Fraction
from pathlib import Path

from .engine import evaluate_discard
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


def odds(state: GameState, targets: list[Target]) -> str:
    """Odds of each target for the hand as it stands and after discarding the selection."""
    if not state.in_run or not state.hand or not targets:
        return ""
    names = [t.name for t in targets]
    if len(targets) > 1:
        targets = [combine(targets), *targets]
        names = ["Any target", *names]
    width = max(len(n) for n in names)

    keep = [evaluate_discard(state, t, []) for t in targets]
    selected = state.selected
    if not selected:
        lines = ["Odds (current hand" + (f", drawing {keep[0].draws}" if keep[0].draws else "") + ")"]
        lines += [f"  {name:<{width}}  {_pct(k.probability)}" for name, k in zip(names, keep)]
        lines.append("  Select cards in game to see the odds after discarding them.")
        return "\n".join(lines)

    after = [evaluate_discard(state, t, selected) for t in targets]
    discarded = " ".join(c.short() for c in after[0].discarded)
    lines = [f"Odds: keep hand  ->  discard {discarded} and draw {after[0].draws}"]
    lines += [f"  {name:<{width}}  {_pct(k.probability)}  ->  {_pct(a.probability)}"
              for name, k, a in zip(names, keep, after)]
    if state.discards_left <= 0:
        lines.append("  (no discards left this round)")
    return "\n".join(lines)


def _target(name: str) -> Target:
    try:
        return get_target(name)
    except KeyError as e:
        raise argparse.ArgumentTypeError(str(e.args[0])) from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="calculatro")
    sub = parser.add_subparsers(dest="cmd", required=True)
    watch = sub.add_parser("watch", help="print the game state whenever it changes")
    watch.add_argument("--file", type=Path, help="state file (default: %%APPDATA%%/Balatro/calculatro_state.json)")
    watch.add_argument("-t", "--target", type=_target, action="append", default=[],
                       help=f"hand type to aim for; repeat for several ({', '.join(TARGETS)}, "
                            "or trips, quads, royal)")
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
                if args.target:
                    out += "\n" + odds(state, args.target)
                print("\n" + "=" * 72 + "\n" + out, flush=True)
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
