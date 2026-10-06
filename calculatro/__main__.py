"""CLI: `python -m calculatro watch` prints a live summary of the game state."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="calculatro")
    sub = parser.add_subparsers(dest="cmd", required=True)
    watch = sub.add_parser("watch", help="print the game state whenever it changes")
    watch.add_argument("--file", type=Path, help="state file (default: %%APPDATA%%/Balatro/calculatro_state.json)")
    args = parser.parse_args(argv)

    if args.cmd == "watch":
        source = FileStateSource(args.file)
        print(f"Watching {source.path} (Ctrl+C to stop)")
        try:
            for state in source.watch():
                print("\n" + "=" * 72 + "\n" + summarize(state), flush=True)
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
