"""CLI: `python -m calculatro watch` prints live game state and the odds of what's tracked
in game, and writes the odds for the mod's in-game panel."""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from threading import Event

from . import overlay, process
from .cards import CardQuery, parse_query
from .hands import Target, combine, get_target
from .report import Report, build_report
from .search import Search, best_discards
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
        f"State: {state.state}   Blind: {state.blind or '-'}"
        f"{' (disabled)' if state.blind_disabled else ''}",
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


def tracked(state: GameState) -> tuple[list[Target], list[CardQuery]]:
    """The targets and card queries set in game. Any the app doesn't know, repeats, and
    any past what the panel fits (`overlay.columns_needed`) are left out."""
    def fits(targets: int, queries: int) -> bool:
        return overlay.columns_needed(targets, queries) <= overlay.MAX_COLUMNS

    targets: list[Target] = []
    for name in state.tracked_hands:
        try:
            target = get_target(name)
        except KeyError:
            continue
        if target not in targets and fits(len(targets) + 1, 0):
            targets.append(target)
    queries: list[CardQuery] = []
    for text in state.tracked_cards:
        try:
            query = parse_query(text)
        except ValueError:
            continue
        if text not in [q.text for q in queries] and fits(len(targets), len(queries) + 1):
            queries.append(query)
    return targets, queries


def calculate(state: GameState, targets: list[Target], queries: list[CardQuery],
              cancel: Event | None = None) -> tuple[str, dict]:
    """The odds as text, and as the payload for the mod's panel."""
    report = build_report(state, targets, queries, cancel)
    return (format_report(report) if report else ""), overlay.odds(state, report)


def search_target(state: GameState, key: str) -> Target | None:
    """The target of the panel's row `key`: a tracked hand, or "Any target" of several."""
    targets, _ = tracked(state)
    if key == "Any target":
        return combine(targets) if len(targets) > 1 else None
    return next((t for t in targets if t.name == key), None)


def search_basis(state: GameState) -> GameState:
    """What a best-discard search depends on: the state, but not what's selected
    (the search selects cards itself), what's tracked or the game's state name."""
    return replace(state, id=None, state=None, hand=[replace(c, highlighted=False) for c in state.hand],
                   tracked_hands=[], tracked_cards=[], search=None)


def format_search(state: GameState, key: str, found: Search) -> str:
    best = found.options[0]
    what = f"discard {' '.join(state.hand[i].short() for i in best)}" if best else "keep the hand"
    more = f" (1 of {len(found.options)} options)" if len(found.options) > 1 else ""
    return f"Best discard for {key}: {what}, {_pct(found.probability).strip()}{more}"


CALCULATING = "Calculating odds..."
NOTHING_TRACKED = "Nothing tracked: add targets with the + on the Calculatro panel in game."
RETRY = 0.5  # seconds between attempts to resend odds the panel didn't get
ALREADY_RUNNING = ("Calculatro is already running (the game starts it) and updates the in-game "
                   "panel: this one only shows the odds here.")


def run_watch(source: StateSource, write: Callable[[str], None],
              panel: Callable[[dict], object] = lambda payload: None, *,
              patience: float = 0.25, poll_interval: float = 0.05,
              stop: Callable[[], bool] = lambda: False) -> None:
    """Print each new state and the odds of what it tracks (`tracked`), calculating
    them in the background.

    A new state cancels the running calculation, so the view keeps up with the
    game however slow the odds are. Odds ready within `patience` seconds print
    together with the state summary; slower ones print the summary first with
    a placeholder line, then follow directly below it.

    `panel` receives the payloads for the mod's odds panel: a `pending` one as
    soon as a state arrives, then its odds. It returns False if it couldn't
    deliver one; that payload is then sent again every RETRY seconds until it
    is, or a newer one replaces it.

    A state asking for a best-discard search (`GameState.search`, with a number
    not seen before) starts one in a job of its own. Only a change to what it
    depends on (`search_basis`) cancels it, not selecting cards. Its result goes
    on every payload (`overlay.search`) until that changes.
    """
    job: Job[tuple[str, dict]] | None = None
    searching: Job[Search] | None = None
    search_for: tuple[GameState, GameState, str, int] | None = None  # basis, state, key, nonce
    found: dict | None = None  # the finished search's result, for the panel
    seen: tuple[str, int] | None = None  # (session, nonce) of the latest search asked for
    base: dict | None = None  # the latest payload, before the search's result goes on it
    pending = ""  # summary of the job's state, until it's printed
    deadline = 0.0
    unsent: dict | None = None  # the latest payload, until the panel gets it
    retry_at = 0.0

    def send(payload: dict) -> None:
        nonlocal unsent, retry_at
        if panel(payload) is not False:
            if unsent is not None:
                print("Odds file written again.", file=sys.stderr, flush=True)
            unsent = None
            return
        if unsent is None:
            print("Couldn't write the odds file (something else has it open); retrying.",
                  file=sys.stderr, flush=True)
        unsent, retry_at = payload, time.monotonic() + RETRY

    def show(payload: dict) -> None:
        nonlocal base
        base = payload
        send({**payload, "search": found} if found else payload)

    def start_search(state: GameState) -> None:
        nonlocal searching, search_for, found, seen
        basis = search_basis(state)
        if search_for is not None and search_for[0] != basis:
            if searching is not None:
                searching.cancel()
            searching = search_for = found = None
        if state.search is None:
            return
        key, nonce = state.search
        request = ((state.id or "").split(":")[0], nonce)
        if request == seen:
            return
        seen = request
        if searching is not None:
            searching.cancel()
        searching, search_for, found = None, (basis, state, key, nonce), None
        target = search_target(state, key)
        if target is None:
            found = overlay.search(key, nonce, None)
        else:
            searching = Job(lambda cancel, state=state, target=target: best_discards(state, target, cancel))

    while not stop():
        if unsent is not None and time.monotonic() >= retry_at:
            send(unsent)
        state = source.poll()
        if state is not None:
            start_search(state)
            if job is not None:
                job.cancel()
            job, pending = None, "\n" + "=" * 72 + "\n" + summarize(state)
            targets, queries = tracked(state)
            if targets or queries:
                show(overlay.pending(state))
                job = Job(lambda cancel, state=state, targets=targets, queries=queries:
                          calculate(state, targets, queries, cancel))
                deadline = time.monotonic() + patience
            else:
                write(pending + ("\n" + NOTHING_TRACKED if state.in_run else ""))
                show(overlay.odds(state, None))
                pending = ""
        if searching is not None and searching.wait(0):
            _, searched, key, nonce = search_for
            result = searching.result()
            searching, found = None, overlay.search(key, nonce, result)
            write(format_search(searched, key, result))
            if base is not None:
                show(base)
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
            show(payload)
            job, pending = None, ""
        elif pending and time.monotonic() >= deadline:
            write(pending + "\n" + CALCULATING)
            pending = ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="calculatro")
    sub = parser.add_subparsers(dest="cmd", required=True)
    watch = sub.add_parser("watch", help="print the game state whenever it changes")
    watch.add_argument("--file", type=Path, help="state file (default: %%APPDATA%%/Balatro/calculatro_state.json)")
    watch.add_argument("--game-pid", type=int,
                       help="run in the background for the game with this process id, as the mod "
                            "starts it: exit with the game, or at once if the app is already running")
    args = parser.parse_args(argv)
    # Suit symbols: Windows pipes/redirects default to cp1252.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.cmd == "watch":
        source = FileStateSource(args.file)
        odds_file = overlay.odds_path(source.path)
        # Any instance claims it, so the game won't start a second one beside it,
        # and only one writes the panel's odds: two would clash over its file.
        first = process.claim_instance()
        if not first and args.game_pid is not None:
            return 0
        panel: Callable[[dict], object] = lambda payload: overlay.write(odds_file, payload)
        if not first:
            print(ALREADY_RUNNING, flush=True)
            panel = lambda payload: None
        stop = lambda: False
        if args.game_pid is not None:
            # No console (pythonw): keep errors in a log beside the state file.
            # Open for the life of the process.
            sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
            sys.stderr = open(source.path.parent / "calculatro.log", "w", encoding="utf-8", buffering=1)  # noqa: SIM115
            stop = process.exited(args.game_pid)
        print(f"Watching {source.path} (Ctrl+C to stop)")
        try:
            run_watch(source, lambda text: print(text, flush=True), panel, stop=stop)
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
