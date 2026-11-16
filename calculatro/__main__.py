"""CLI: `python -m calculatro watch` writes the odds of what's tracked in game for the mod's
panel (the game starts it), and `odds` prints them for one state file, for debugging."""

import argparse
import os
import sys
import time
import traceback
from collections import Counter
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from threading import Event

from . import overlay, paths, process, text
from .cards import CardQuery, parse_query
from .engine import Session, clear_caches
from .hands import Target, combine, get_target
from .log import header, open_log
from .report import build_report, row_key
from .rules import RULE_JOKERS
from .search import Progress, Search, best_card_discards, best_discards, estimated_best
from .source import FileStateSource, StateSource
from .state import GameState, MalformedState, SchemaMismatch
from .worker import LIMITS, Control, Job, Limits, TooSlow, Watchdog, limited


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
    for written in state.tracked_cards:
        try:
            query = parse_query(written)
        except ValueError:
            continue
        if query.text not in [q.text for q in queries] and fits(len(targets), len(queries) + 1):
            queries.append(query)
    return targets, queries


def calculate(state: GameState, targets: list[Target], queries: list[CardQuery],
              cancel: Control | Event | None = None, known: dict | None = None, limits: Limits = LIMITS,
              progress: Callable[[dict], None] | None = None, session: Session | None = None) -> dict:
    """The odds as the payload for the mod's panel. `progress` is given the
    partial payloads as values are found. `known`, `limits`, `session`: see `build_report`."""
    report = build_report(state, targets, queries, cancel, known, limits,
                          None if progress is None else lambda partial: progress(overlay.odds(state, partial)),
                          session)
    return overlay.odds(state, report)


def search_target(state: GameState, key: str) -> Target | CardQuery | None:
    """What the panel's row `key` tracks: a hand, "Any target" of several, or a
    card query (no query's text is a hand's name)."""
    targets, queries = tracked(state)
    if key == "Any target":
        return combine(targets) if len(targets) > 1 else None
    return (next((t for t in targets if t.name == key), None)
            or next((q for q in queries if q.text == key), None))


def find_best(state: GameState, target: Target | CardQuery, cancel: Event | None = None,
              progress: Progress | None = None, session: Session | None = None) -> Search:
    """The best discards for a row's target hand or card query."""
    if isinstance(target, CardQuery):
        return best_card_discards(state, target, cancel, progress, session=session)
    return best_discards(state, target, cancel, progress, session=session)


def search_within(state: GameState, target: Target | CardQuery, control: Control | Event | None,
                  limits: Limits = LIMITS, session: Session | None = None) -> Search | TooSlow:
    """find_best, given up after `limits.search` (or at the memory limit). One
    given up on answers from estimates of what it had found
    (`search.estimated_best`, in about `limits.search_estimate`), marked with
    what stopped it; TooSlow if it can't. (Unmarked if they turn out exact.)"""
    progress = Progress()
    found = limited(control, limits.search, lambda cancel: find_best(state, target, cancel, progress, session))
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


def rule_jokers(state: GameState) -> frozenset[str]:
    """The jokers held that change the hands' rules, debuffed or not: when they change, `run_watch` empties the
    engine's caches (`engine.clear_caches`). A debuff doesn't count, since Crimson Heart moves its debuff every
    hand, and the next hand may want the same rules again."""
    return frozenset(j.key for j in state.jokers if j.key in RULE_JOKERS)


def odds_basis(state: GameState) -> tuple[GameState, Counter, tuple]:
    """What a row's odds depend on besides its target and selection: `search_basis`,
    but not the hand's order, which the odds don't look at. The observations go
    by id, not hand index (`Card.latent` says which hand card is which id), so a
    drag doesn't throw the finished rows away."""
    basis = search_basis(state)
    by_id = tuple("face down in hand" if o.hand is not None else o for o in basis.observed)
    return replace(basis, hand=[], observed=()), Counter(basis.hand), by_id


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
ALREADY_RUNNING = ("Calculatro is already running for this state file (the game starts it), and only one can "
                   "write the odds. To see the current state's odds here, run `python -m calculatro odds`.")
NOTHING_TRACKED = "Nothing tracked: add targets with the + on the Calculatro panel in game."


def run_watch(source: StateSource, panel: Callable[[dict], object], *, poll_interval: float = 0.05,
              stop: Callable[[], bool] = lambda: False,
              limits: Limits = LIMITS, watchdog: Watchdog | None = None,
              memory_limit: Callable[[int | None], int] | None = None) -> None:
    """Work out the odds of what each new state tracks (`tracked`) in the
    background, for the mod's panel.

    A new state cancels the running calculation, so the panel keeps up with the
    game however slow the odds are. Each row's finished odds are kept until
    `odds_basis` changes, so selecting the same cards again doesn't calculate
    them again.

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

    A state holding other rule jokers (`rule_jokers`) empties the engine's caches
    (`engine.clear_caches`), so what the old rules built doesn't keep its memory.

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
    session = Session()  # what the engine keeps between the jobs
    job: Job[dict] | None = None
    job_state: GameState | None = None  # the state the job is for
    progress_sent = 0  # the job's progress last sent (`Control.updates`)
    next_partial = 0.0
    known: dict = {}  # finished rows for `known_for`. A job keeps the one it started with, so an
    known_for: tuple | None = None  # older job can't add to a newer state's.
    searching: Job[Search | TooSlow] | None = None
    search_for: tuple[GameState, str, int, Target | CardQuery | None] | None = None  # basis, key, nonce, target
    given_up: dict[object, Search | TooSlow] = {}  # searches given up on for `given_up_for`, by `row_key`
    given_up_for: GameState | None = None
    found: dict | None = None  # the finished search's result, for the panel
    seen: tuple[str, int] | None = None  # (session, nonce) of the latest search asked for
    base: dict | None = None  # the latest payload, before the search's result goes on it
    unsent: dict | None = None  # the latest payload, until the panel gets it
    retry_at = 0.0
    unwritten = ""  # why the odds file couldn't be written, as last logged ("" once it has been)
    logged: set[tuple] = set()  # the failures reported so far, each only once (a message's values may change)
    rules_held: frozenset[str] | None = None  # `rule_jokers` of the latest state

    def failed(what: str, error: Exception) -> None:
        where = tuple((frame.f_code.co_filename, line) for frame, line in traceback.walk_tb(error.__traceback__))
        if (type(error), where) in logged:
            return
        logged.add((type(error), where))
        message = "".join(traceback.format_exception(error))
        try:
            print(f"{what} failed:\n{message}", file=sys.stderr, end="", flush=True)
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
                                       or row_key(search_target(state, search_for[1])) != row_key(search_for[3])):
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
        searching, search_for, found = None, (basis, key, nonce, target), None
        if target is None or row_key(target) in given_up:
            found = overlay.search(key, nonce, None if target is None else given_up[row_key(target)])
        else:
            searching = Job(lambda control, state=state, target=target:
                            search_within(state, target, control, limits, session), watchdog)

    while not stop():
        try:
            if unsent is not None and time.monotonic() >= retry_at:
                send(unsent)
            state = source.poll()
            if state is not None:
                if rule_jokers(state) != rules_held:
                    if rules_held is not None:
                        clear_caches()  # what the old rules built only holds memory now
                    rules_held = rule_jokers(state)
                if memory_limit is not None:
                    watchdog.memory_limit = memory_limit(state.memory_gb)
                start_search(state)
                if job is not None:
                    job.cancel()
                    job = None
                targets, queries = tracked(state)
                if (targets or queries) and state.in_run and not settled(state):
                    # The panel keeps the last odds, greyed out, until the hand settles.
                    show(overlay.pending(state))
                elif targets or queries:
                    show(overlay.pending(state))
                    job_state = state
                    basis = odds_basis(state)
                    if basis != known_for:
                        known, known_for = {}, basis
                    job = Job(lambda control, state=state, targets=targets, queries=queries, known=known:
                              calculate(state, targets, queries, control, known=known, limits=limits,
                                        progress=control.progress, session=session), watchdog)
                    progress_sent, next_partial = 0, time.monotonic() + PARTIAL_INTERVAL
                else:
                    show(overlay.odds(state, None))
            if searching is not None and searching.wait(0):
                _, key, nonce, target = search_for
                try:
                    result = searching.result()
                except Exception as e:
                    failed(f"Best discard search for {key}", e)
                    result = None
                searching, found = None, overlay.search(key, nonce, result)
                if isinstance(result, TooSlow) or (isinstance(result, Search) and result.estimated is not None):
                    given_up[row_key(target)] = result
                if base is not None:
                    show(base)
            if job is None:
                time.sleep(poll_interval)
                continue
            if job.wait(poll_interval):
                finished, job = job, None  # delivered once, even if that fails
                try:
                    payload = finished.result()
                except Exception as e:
                    failed(f"Odds for snapshot {job_state.id}", e)
                    payload = overlay.odds(job_state, None)
                show(payload)
            else:
                control = job.control
                if control.updates != progress_sent and time.monotonic() >= next_partial:
                    progress_sent, next_partial = control.updates, time.monotonic() + PARTIAL_INTERVAL
                    show(control.latest)
        except Exception as e:
            # Something the mod would never send got past `GameState.from_json`.
            # Skip it like a malformed snapshot: the next one recovers.
            failed("Handling the game state", e)
            if job is not None and job.cancelled:
                job = None  # cancelled for the state that failed; any other is still answered
            time.sleep(poll_interval)
    for running in (job, searching):  # stopped: nothing is left calculating for a loop that's gone
        if running is not None:
            running.cancel()


def _finish[T](job: Job[T]) -> T:
    """`job`'s result, waiting for it in steps so that Ctrl+C stops the wait (and the job)."""
    try:
        while not job.wait(0.1):
            pass
    except KeyboardInterrupt:
        job.cancel()
        raise
    return job.result()


def print_odds(path: Path | None, key: str | None, limits: Limits = LIMITS, watchdog: Watchdog | None = None) -> int:
    """Print the state in the file at `path`, the odds of what it tracks, and the
    best discard for the row `key` (by default the one the state asks for, if
    any), worked out with the same limits as in game (`watchdog` by default has
    the player's memory limit). For debugging: run by hand, or on a state file a
    player sent with a bug report. Writes no odds file."""
    source = FileStateSource(path)
    try:
        state = source.read()
    except (SchemaMismatch, MalformedState) as e:
        print(f"Can't read {source.path}: {e}", file=sys.stderr)
        return 1
    if state is None:
        print(f"Can't read {source.path}: it's missing, or something else has it open.", file=sys.stderr)
        return 1
    print(text.summarize(state))
    if not state.in_run:
        return 0
    if watchdog is None:
        watchdog = Watchdog(process.memory_used, process.memory_limit(state.memory_gb, process.installed_memory()))
    session = Session()  # shared by the report and the search, as in game
    targets, queries = tracked(state)
    if targets or queries:
        report = _finish(Job(lambda control: build_report(state, targets, queries, control, limits=limits,
                                                          session=session), watchdog))
        print(text.format_report(report) if report else "Nothing to calculate with this hand.")
    else:
        print(NOTHING_TRACKED)
    if key is None and state.search is not None:
        key = state.search[0]
    if key is not None:
        target = search_target(state, key)
        if target is None:
            print(f"Best discard for {key}: no such row is tracked.")
        else:
            found = _finish(Job(lambda control: search_within(state, target, control, limits, session), watchdog))
            print(text.format_search(state, key, found))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="calculatro")
    sub = parser.add_subparsers(dest="cmd", required=True)
    default_file = f"%%APPDATA%%/Balatro/{paths.DATA_DIR}/{paths.STATE_FILE}"
    watch = sub.add_parser("watch", help="write the odds for the in-game panel whenever the game state changes")
    watch.add_argument("--file", type=Path, help=f"state file (default: {default_file})")
    watch.add_argument("--game-pid", type=int,
                       help="run in the background for the game with this process id, as the mod "
                            "starts it: exit with the game, or at once if the app is already running")
    odds = sub.add_parser("odds", help="print a state file's state and odds once, for debugging")
    odds.add_argument("file", nargs="?", type=Path, help=f"state file (default: {default_file})")
    odds.add_argument("--search", metavar="ROW",
                      help="also find the best discard for this row: a hand's name, \"Any target\" or a card "
                           "query (default: the one the state asks for, if any)")
    args = parser.parse_args(argv)
    # Suit symbols: Windows pipes/redirects default to cp1252.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.cmd == "odds":
        try:
            return print_odds(args.file, args.search)
        except KeyboardInterrupt:
            return 130
    source = FileStateSource(args.file)
    odds_file = paths.odds_path(source.path)
    # Any instance claims its state file, so the game won't start a second one
    # beside it, and only one writes those odds: two would clash over the file.
    if not process.claim_instance(process.instance_name(source.path)):
        if args.game_pid is not None:
            return 0
        print(ALREADY_RUNNING, file=sys.stderr)
        return 1
    stop = lambda: False
    if args.game_pid is not None:
        # No console (pythonw): keep errors in a log beside the state file.
        # Open for the life of the process.
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
        try:
            sys.stderr = open_log(paths.log_path(source.path))
            print(header(source.path, args.game_pid), file=sys.stderr)
        except OSError:
            pass  # the odds are still worth having; printing to no stderr (None) does nothing
        stop = process.exited(args.game_pid)
        if stop():
            print(f"Game process {args.game_pid} isn't running or can't be opened: exiting.", file=sys.stderr)
    print(f"Watching {source.path} and writing the panel's odds (Ctrl+C to stop). Errors show here; "
          "`python -m calculatro odds` prints the current odds.")
    installed = process.installed_memory()
    try:
        run_watch(source, lambda payload: overlay.write(odds_file, payload), stop=stop,
                  watchdog=Watchdog(process.memory_used, process.memory_limit(None, installed)),
                  memory_limit=lambda gb: process.memory_limit(gb, installed))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
