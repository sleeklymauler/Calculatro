"""CLI: `python -m calculatro watch` prints live game state and the odds of what's tracked
in game, and writes the odds for the mod's in-game panel."""

from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from collections import Counter
from collections.abc import Callable
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from threading import Event

from . import overlay, process
from .cards import CardQuery, parse_query
from .hands import Target, combine, get_target
from .log import header, open_log
from .report import Approx, Report, Value, build_report, row_key
from .search import Progress, Search, best_card_discards, best_discards, estimated_best
from .source import FileStateSource, StateSource
from .state import RANK_ORDER, RANK_SHORT, SUIT_SHORT, SUITS, GameState
from .worker import LIMITS, MEMORY, TIME, Control, Job, Limits, TooSlow, Watchdog, limited


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
              cancel: Control | Event | None = None, known: dict | None = None, limits: Limits = LIMITS,
              progress: Callable[[dict], None] | None = None) -> tuple[str, dict]:
    """The odds as text, and as the payload for the mod's panel. `progress` is
    given the partial payloads as values are found. `known`, `limits`: see
    `build_report`."""
    report = build_report(state, targets, queries, cancel, known, limits,
                          None if progress is None else lambda partial: progress(overlay.odds(state, partial)))
    return (format_report(report) if report else ""), overlay.odds(state, report)


def search_target(state: GameState, key: str) -> Target | CardQuery | None:
    """What the panel's row `key` tracks: a hand, "Any target" of several, or a
    card query (no query's text is a hand's name)."""
    targets, queries = tracked(state)
    if key == "Any target":
        return combine(targets) if len(targets) > 1 else None
    return (next((t for t in targets if t.name == key), None)
            or next((q for q in queries if q.text == key), None))


def find_best(state: GameState, target: Target | CardQuery, cancel: Event | None = None,
              progress: Progress | None = None) -> Search:
    """The best discards for a row's target hand or card query."""
    if isinstance(target, CardQuery):
        return best_card_discards(state, target, cancel, progress)
    return best_discards(state, target, cancel, progress)


def search_within(state: GameState, target: Target | CardQuery, control: Control | Event | None,
                  limits: Limits = LIMITS) -> Search | TooSlow:
    """find_best, given up after `limits.search` (or at the memory limit). One
    given up on answers from estimates of what it had found
    (`search.estimated_best`, in about `limits.search_estimate`), marked with
    what stopped it; TooSlow if it can't. (Unmarked if they turn out exact.)"""
    progress = Progress()
    found = limited(control, limits.search, lambda cancel: find_best(state, target, cancel, progress))
    if not isinstance(found, TooSlow):
        return found
    # The estimates stop themselves; the limit is for ones that can't.
    estimated = limited(control, 2 * limits.search_estimate,
                        lambda cancel: estimated_best(progress, cancel, limits.search_estimate))
    if not isinstance(estimated, Search):
        return found
    if estimated.estimated is None:
        return estimated  # every choice that could be best was quick after all: exact
    return replace(estimated, estimated=replace(estimated.estimated, limit=found.limit))


def search_basis(state: GameState) -> GameState:
    """What a best-discard search depends on: the state, but not what's selected
    (the search selects cards itself), what's tracked or the game's state name.
    The observations are part of it: a new sort can change the odds."""
    return replace(state, id=None, state=None, hand=[replace(c, highlighted=False) for c in state.hand],
                   tracked_hands=[], tracked_cards=[], search=None)


def odds_basis(state: GameState) -> tuple[GameState, Counter, tuple]:
    """What a row's odds depend on besides its target and selection: `search_basis`,
    but not the hand's order, which the odds don't look at. The observations go
    by id, not hand index (`Card.latent` says which hand card is which id), so a
    drag doesn't throw the finished rows away."""
    basis = search_basis(state)
    by_id = tuple("face down in hand" if o.hand is not None else o for o in basis.observed)
    return replace(basis, hand=[], observed=()), Counter(basis.hand), by_id


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


CALCULATING = "Calculating odds..."
NOTHING_TRACKED = "Nothing tracked: add targets with the + on the Calculatro panel in game."
WAITING_FOR_HAND = "Odds once you're selecting cards."
ODDS_FAILED = "Couldn't calculate the odds (the error is on stderr)."


def settled(state: GameState) -> bool:
    """Whether the hand is the player's to choose from: odds are only worth
    working out while selecting cards (or with no state given, as in a
    hand-written file). While cards are being dealt, a played hand scores or a
    pack is open, the hand changes card by card, and each snapshot's odds would
    be thrown away by the next."""
    return state.state in (None, "SELECTING_HAND")


RETRY = 0.5  # seconds between attempts to resend odds the panel didn't get, while the file is locked
RETRY_ERROR = 10.0  # the same after another error (a missing folder, a full disk), which may last
PARTIAL_INTERVAL = 0.1  # seconds at least between payloads with some odds still to come (the mod reads ~10x/s)
ALREADY_RUNNING = ("Calculatro is already running (the game starts it) and updates the in-game "
                   "panel: this one only shows the odds here.")


def run_watch(source: StateSource, write: Callable[[str], None],
              panel: Callable[[dict], object] = lambda payload: None, *,
              patience: float = 0.25, poll_interval: float = 0.05,
              stop: Callable[[], bool] = lambda: False,
              limits: Limits = LIMITS, watchdog: Watchdog | None = None,
              memory_limit: Callable[[int | None], int] | None = None) -> None:
    """Print each new state and the odds of what it tracks (`tracked`), calculating
    them in the background.

    A new state cancels the running calculation, so the view keeps up with the
    game however slow the odds are. Odds ready within `patience` seconds print
    together with the state summary; slower ones print the summary first with
    a placeholder line, then follow directly below it. Each row's finished odds
    are kept until `odds_basis` changes, so selecting the same cards again
    doesn't calculate them again.

    `panel` receives the payloads for the mod's odds panel: a `pending` one as
    soon as a state arrives, then its odds (only the pending one while the
    hand isn't `settled`): `partial` ones as values are found, at most one
    every PARTIAL_INTERVAL seconds (none if they're all found sooner), then
    all of them. It returns False if it couldn't
    deliver one for a lock; that payload is then sent again every RETRY seconds until it
    is, or a newer one replaces it. It raises an `OSError` for any other failure,
    which is logged and retried the same way but only every RETRY_ERROR seconds
    (a newer payload is still tried at once). Each problem is logged when it
    starts, and once more when the file is written again.

    Each odds value, and each search, stops at its `limits` (`build_report`),
    or once `watchdog` finds the app using too much memory, and is given up on
    (`worker.TooSlow`). A search given up on answers from estimates if it can
    (`search_within`), and is answered the same at once when asked for again,
    until what it depends on changes. With `memory_limit`, each state sets the
    watchdog's limit to `memory_limit(state.memory_gb)`, the player's "Memory limit"
    setting. The setting is part of `odds_basis` and `search_basis`, so changing
    it starts afresh: what was given up on under the old limit is tried again.

    A state asking for a best-discard search (`GameState.search`, with a number
    not seen before) starts one in a job of its own. Only a change to what it
    depends on (`search_basis`, or the row's target) cancels it, not selecting
    cards; if the state still asks for it, it starts again. Its result goes on
    every payload (`overlay.search`) until that changes, and the mod drops its
    copy once a payload comes without it.

    A calculation or search that fails (a bug, not a cancellation) is logged on
    stderr, once per run for each kind of error and place it's raised, and
    answered as if there were nothing to show: the panel gets odds without
    values, a search no options, so neither is left waiting. The next state is
    calculated as usual.

    Any other error in the loop (a bad state that `GameState.from_json` didn't
    catch, say) is logged the same way, and the app carries on with the next state.
    """
    if watchdog is None:
        watchdog = Watchdog()  # time limits only
    job: Job[tuple[str, dict]] | None = None
    job_state: GameState | None = None  # the state the job is for
    progress_sent = 0  # the job's progress last sent (`Control.updates`)
    next_partial = 0.0
    known: dict = {}  # finished rows for `known_for`. A job keeps the one it started with, so an
    known_for: tuple | None = None  # older job can't add to a newer state's.
    searching: Job[Search | TooSlow] | None = None
    search_for: tuple[GameState, GameState, str, int, Target | CardQuery | None] | None = None
    # basis, state, key, nonce, target
    given_up: dict[object, Search | TooSlow] = {}  # searches given up on for `given_up_for`, by `row_key`
    given_up_for: GameState | None = None
    found: dict | None = None  # the finished search's result, for the panel
    seen: tuple[str, int] | None = None  # (session, nonce) of the latest search asked for
    base: dict | None = None  # the latest payload, before the search's result goes on it
    pending = ""  # summary of the job's state, until it's printed
    deadline = 0.0
    unsent: dict | None = None  # the latest payload, until the panel gets it
    retry_at = 0.0
    unwritten = ""  # why the odds file couldn't be written, as last logged ("" once it has been)
    logged: set[tuple] = set()  # the failures reported so far, each only once (a message's values may change)

    def failed(what: str, error: Exception) -> None:
        where = tuple((frame.f_code.co_filename, line) for frame, line in traceback.walk_tb(error.__traceback__))
        if (type(error), where) in logged:
            return
        logged.add((type(error), where))
        text = "".join(traceback.format_exception(error))
        try:
            print(f"{what} failed:\n{text}", file=sys.stderr, end="", flush=True)
        except Exception:
            pass  # a full disk, say: this is the last resort for errors, so one here would stop the app

    def send(payload: dict) -> None:
        nonlocal unsent, retry_at, unwritten
        try:
            if panel(payload) is not False:
                if unwritten:
                    print("Odds file written again.", file=sys.stderr, flush=True)
                unsent, unwritten = None, ""
                return
            why, wait = "something else has it open", RETRY
        except OSError as error:
            why, wait = f"{type(error).__name__}: {error}", RETRY_ERROR
        if why != unwritten:
            print(f"Couldn't write the odds file ({why}); retrying.", file=sys.stderr, flush=True)
        unsent, retry_at, unwritten = payload, time.monotonic() + wait, why

    def show(payload: dict) -> None:
        nonlocal base
        base = payload
        send({**payload, "search": found} if found else payload)

    def start_search(state: GameState) -> None:
        nonlocal searching, search_for, found, seen, given_up, given_up_for
        basis = search_basis(state)
        if basis != given_up_for:
            given_up, given_up_for = {}, basis
        if search_for is not None and (search_for[0] != basis
                                       or row_key(search_target(state, search_for[2])) != row_key(search_for[4])):
            # What it was worked out from has changed (or the row's target, as
            # for "Any target" when a hand is added): drop it, and search again
            # if the mod is still asking (it stops once it has a reply), so it
            # isn't left waiting for one.
            if searching is not None:
                searching.cancel()
            searching = search_for = found = seen = None
        if state.search is None:
            return
        key, nonce = state.search
        request = ((state.id or "").split(":")[0], nonce)
        if request == seen:
            return
        seen = request
        if searching is not None:
            searching.cancel()
        target = search_target(state, key)
        searching, search_for, found = None, (basis, state, key, nonce, target), None
        if target is None or row_key(target) in given_up:
            found = overlay.search(key, nonce, None if target is None else given_up[row_key(target)])
        else:
            searching = Job(lambda control, state=state, target=target: search_within(state, target, control, limits),
                            watchdog)

    while not stop():
        try:
            if unsent is not None and time.monotonic() >= retry_at:
                send(unsent)
            state = source.poll()
            if state is not None:
                if memory_limit is not None:
                    watchdog.memory_limit = memory_limit(state.memory_gb)
                start_search(state)
                if job is not None:
                    job.cancel()
                job, pending = None, "\n" + "=" * 72 + "\n" + summarize(state)
                targets, queries = tracked(state)
                if (targets or queries) and state.in_run and not settled(state):
                    # The panel keeps the last odds, greyed out, until the hand settles.
                    write(pending + "\n" + WAITING_FOR_HAND)
                    show(overlay.pending(state))
                    pending = ""
                elif targets or queries:
                    show(overlay.pending(state))
                    job_state = state
                    basis = odds_basis(state)
                    if basis != known_for:
                        known, known_for = {}, basis
                    job = Job(lambda control, state=state, targets=targets, queries=queries, known=known:
                              calculate(state, targets, queries, control, known=known, limits=limits,
                                        progress=control.progress), watchdog)
                    deadline = time.monotonic() + patience
                    progress_sent, next_partial = 0, time.monotonic() + PARTIAL_INTERVAL
                else:
                    write(pending + ("\n" + NOTHING_TRACKED if state.in_run else ""))
                    show(overlay.odds(state, None))
                    pending = ""
            if searching is not None and searching.wait(0):
                _, searched, key, nonce, target = search_for
                try:
                    result = searching.result()
                except Exception as e:
                    failed(f"Best discard search for {key}", e)
                    result = None
                searching, found = None, overlay.search(key, nonce, result)
                if isinstance(result, TooSlow) or (isinstance(result, Search) and result.estimated is not None):
                    given_up[row_key(target)] = result
                if result is not None:
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
                finished, summary = job, pending
                job, pending = None, ""  # delivered once, even if that fails
                try:
                    out, payload = finished.result()
                except Exception as e:
                    failed(f"Odds for snapshot {job_state.id}", e)
                    out, payload = ODDS_FAILED, overlay.odds(job_state, None)
                try:
                    if summary:
                        write(summary + ("\n" + out if out else ""))
                    elif out:
                        write(out)
                finally:
                    show(payload)  # the panel's answer doesn't depend on the console's
            else:
                control = job.control
                if control.updates != progress_sent and time.monotonic() >= next_partial:
                    progress_sent, next_partial = control.updates, time.monotonic() + PARTIAL_INTERVAL
                    show(control.latest)
                if pending and time.monotonic() >= deadline:
                    write(pending + "\n" + CALCULATING)
                    pending = ""
        except Exception as e:
            # Something the mod would never send got past `GameState.from_json`, or
            # printing failed. Skip it like a malformed snapshot: the next one recovers.
            failed("Handling the game state", e)
            if job is not None and job.cancelled:
                job = None  # cancelled for the state that failed; any other keeps its summary to print
            if job is None:
                pending = ""
            time.sleep(poll_interval)
    for running in (job, searching):  # stopped: nothing is left calculating for a loop that's gone
        if running is not None:
            running.cancel()


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
        # Any instance claims its state file, so the game won't start a second one
        # beside it, and only one writes those odds: two would clash over the file.
        first = process.claim_instance(process.instance_name(source.path))
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
            try:
                sys.stderr = open_log(source.path.parent / "calculatro.log")
                print(header(source.path, args.game_pid), file=sys.stderr)
            except OSError:
                pass  # the odds are still worth having; printing to no stderr (None) does nothing
            stop = process.exited(args.game_pid)
            if stop():
                print(f"Game process {args.game_pid} isn't running or can't be opened: exiting.", file=sys.stderr)
        print(f"Watching {source.path} (Ctrl+C to stop)")
        installed = process.installed_memory()
        try:
            run_watch(source, lambda text: print(text, flush=True), panel, stop=stop,
                      watchdog=Watchdog(process.memory_used, process.memory_limit(None, installed)),
                      memory_limit=lambda gb: process.memory_limit(gb, installed))
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
