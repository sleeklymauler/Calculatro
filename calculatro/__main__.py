"""CLI: `python -m calculatro watch -t trips -c "2+ queens"` prints live game state and odds,
and writes them for the mod's in-game panel."""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path
from threading import Event

from . import overlay
from .cards import CardQuery, parse_query
from .hands import TARGETS, Target, get_target
from .report import Report, build_report
from .source import FileStateSource, StateSource
from .state import RANK_ORDER, RANK_SHORT, SUIT_SHORT, SUITS, GameState
from .worker import Job


def summarize(state: GameState) -> str:
    if not state.in_run:
        return "Not in a run."
    ranks = state.rank_counts()
    suits = state.suit_counts()
    rank_line = "  ".join(f"{RANK_SHORT.get(r, r)}:{ranks[r]}" for r in RANK_ORDER)
    suit_line = "  ".join(f"{SUIT_SHORT[s]}:{suits[s]}" for s in SUITS)
    special = len(state.deck) - sum(ranks.values())
    jokers = ", ".join(j.key + (" (debuffed)" if j.debuff else "") for j in state.jokers)
    lines = [
        f"State: {state.state}   Blind: {state.blind or '-'}",
        f"Hand size: {state.hand_size}   Hands left: {state.hands_left}   Discards left: {state.discards_left}",
        f"Hand ({len(state.hand)}): {' '.join(c.short() for c in state.hand)}",
        f"Deck ({len(state.deck)} cards remaining)",
        f"  Ranks: {rank_line}",
        f"  Suits: {suit_line}" + (f"   (+{special} stone)" if special else ""),
        f"Discard pile: {len(state.discard)}   Jokers: {jokers or '-'}",
    ]
    return "\n".join(lines)


def _pct(p: Fraction) -> str:
    return f"{float(p) * 100:6.2f}%"


def format_report(report: Report) -> str:
    """The report as text. Card odds have only a discard column."""
    hint = "Select cards in game to see the odds after discarding them."
    keep = report.targets
    if not report.discarded:
        if not keep:
            return hint
        width = max(len(row.name) for row in keep)
        draws = report.keep_draws
        lines = ["Odds (current hand" + (f", drawing {draws}" if draws else "") + "):"]
        lines += [f"  {row.name:<{width}}  {_pct(row.keep)}" for row in keep]
        return "\n".join([*lines, hint])

    card_rows = [(q.label, p) for q, p in report.cards]
    discarded = " ".join(c.short() for c in report.discarded)
    width = max(len(name) for name in [row.name for row in keep] + [name for name, _ in card_rows])
    # Card rows leave the keep column blank so the discard column lines up.
    blank = " " * len(f"{_pct(Fraction(0))}  ->  ") if keep else ""

    keep_desc = "keep hand" + (f" (draw {report.keep_draws})" if keep and report.keep_draws else "")
    discard_desc = f"discard {discarded} and draw {report.discard_draws}"
    lines = [f"Odds: {keep_desc}  ->  {discard_desc}" if keep else f"Odds: {discard_desc}"]
    lines += [f"  {row.name:<{width}}  {_pct(row.keep)}  ->  {_pct(row.discard)}" for row in keep]
    # Card odds cover only the drawn cards; label them apart from the whole-hand
    # target rows. With no targets the header already says what's drawn.
    if card_rows and keep:
        lines.append("  Drawn cards:")
    lines += [f"  {name:<{width}}  {blank}{_pct(p)}" for name, p in card_rows]
    if report.discards_left <= 0:
        lines.append("  (no discards left this round)")
    return "\n".join(lines)


def odds(state: GameState, targets: list[Target], queries: list[CardQuery] = (),
         cancel: Event | None = None) -> str:
    """Target odds for keeping the hand vs. discarding the in-game selection,
    plus card-query odds for the cards drawn after that discard, as text."""
    report = build_report(state, targets, queries, cancel)
    return format_report(report) if report else ""


def calculate(state: GameState, targets: list[Target], queries: list[CardQuery],
              cancel: Event | None = None) -> tuple[str, dict]:
    """The odds as text, and as the payload for the mod's panel."""
    report = build_report(state, targets, queries, cancel)
    return (format_report(report) if report else ""), overlay.odds(state, report)


CALCULATING = "Calculating odds..."


def run_watch(source: StateSource, targets: list[Target], queries: list[CardQuery],
              write: Callable[[str], None], panel: Callable[[dict], object] = lambda payload: None, *,
              patience: float = 0.25, poll_interval: float = 0.05,
              stop: Callable[[], bool] = lambda: False) -> None:
    """Print each new state and its odds, calculating the odds in the background.

    A new state cancels the running calculation, so the view keeps up with the
    game however slow the odds are. Odds ready within `patience` seconds print
    together with the state summary; slower ones print the summary first with
    a placeholder line, then follow directly below it.

    `panel` receives the payloads for the mod's odds panel: a `pending` one as
    soon as a state arrives, then its odds.
    """
    job: Job[tuple[str, dict]] | None = None
    pending = ""  # summary of the job's state, until it's printed
    deadline = 0.0
    while not stop():
        state = source.poll()
        if state is not None:
            if job is not None:
                job.cancel()
            job, pending = None, "\n" + "=" * 72 + "\n" + summarize(state)
            if targets or queries:
                panel(overlay.pending(state))
                job = Job(lambda cancel, state=state: calculate(state, targets, queries, cancel))
                deadline = time.monotonic() + patience
            else:
                write(pending)
                panel(overlay.odds(state, None, configured=False))
                pending = ""
        if job is None:
            time.sleep(poll_interval)
            continue
        timeout = poll_interval
        if pending:
            timeout = max(0.0, min(timeout, deadline - time.monotonic()))
        if job.wait(timeout):
            out, payload = job.result()
            if pending:
                write(pending + ("\n" + out if out else ""))
            elif out:
                write(out)
            panel(payload)
            job, pending = None, ""
        elif pending and time.monotonic() >= deadline:
            write(pending + "\n" + CALCULATING)
            pending = ""


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
    if args.cmd == "watch" and len(args.target) + len(args.cards) > overlay.MAX_TRACKED:
        parser.error(f"at most {overlay.MAX_TRACKED} targets and card queries in total "
                     f"(got {len(args.target) + len(args.cards)}), so the in-game panel fits")
    # Suit symbols: Windows pipes/redirects default to cp1252.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.cmd == "watch":
        source = FileStateSource(args.file)
        odds_file = overlay.odds_path(source.path)
        print(f"Watching {source.path} (Ctrl+C to stop)")
        try:
            run_watch(source, args.target, args.cards, lambda text: print(text, flush=True),
                      lambda payload: overlay.write(odds_file, payload))
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
