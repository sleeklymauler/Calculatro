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


class Failures:
    """Logs what failed (a bug, not a cancellation) on stderr, once per run for each kind of error and place it's
    raised: a message's values may change, and the same failure would otherwise fill the log."""

    def __init__(self):
        self.logged: set[tuple] = set()

    def __call__(self, what: str, error: Exception) -> None:
        where = tuple((frame.f_code.co_filename, line) for frame, line in traceback.walk_tb(error.__traceback__))
        if (type(error), where) in self.logged:
            return
        self.logged.add((type(error), where))
        message = "".join(traceback.format_exception(error))
        try:
            print(f"{what} failed:\n{message}", file=sys.stderr, end="", flush=True)
        except Exception:
            pass  # a full disk, say: this is the last resort for errors, so one here would stop the app


class Outbox:
    """Delivers payloads to the mod's panel: the latest odds, with the latest search's result on them.

    `panel` returns False if it couldn't deliver one for a lock; that payload is then sent again every RETRY
    seconds (`retry`) until it is, or a newer one replaces it. It raises an `OSError` for any other failure, which
    is logged and retried the same way but only every RETRY_ERROR seconds (a newer payload is still tried at once).
    Each problem is logged when it starts, and once more when the file is written again."""

    def __init__(self, panel: Callable[[dict], object]):
        self.panel = panel
        self.odds: dict | None = None  # the latest odds payload, before the search's result goes on it
        self.unsent: dict | None = None  # the latest payload, until the panel gets it
        self.retry_at = 0.0
        self.unwritten = ""  # why the odds file couldn't be written, as last logged ("" once it has been)

    def show(self, odds: dict | None, search: dict | None) -> None:
        """Send `odds` (or, if None, the latest odds again) with `search` on them. Nothing before the first odds."""
        if odds is not None:
            self.odds = odds
        if self.odds is not None:
            self._send({**self.odds, "search": search} if search else self.odds)

    def retry(self) -> None:
        """Send again the payload the panel didn't get, once it's time to."""
        if self.unsent is not None and time.monotonic() >= self.retry_at:
            self._send(self.unsent)

    def _send(self, payload: dict) -> None:
        try:
            if self.panel(payload) is not False:
                if self.unwritten:
                    print("Odds file written again.", file=sys.stderr, flush=True)
                self.unsent, self.unwritten = None, ""
                return
            why, wait = "something else has it open", RETRY
        except OSError as error:
            why, wait = f"{type(error).__name__}: {error}", RETRY_ERROR
        if why != self.unwritten:
            print(f"Couldn't write the odds file ({why}); retrying.", file=sys.stderr, flush=True)
        self.unsent, self.retry_at, self.unwritten = payload, time.monotonic() + wait, why


class OddsJobs:
    """The odds of what each new state tracks (`tracked`), worked out in a job of their own.

    A new state cancels the running job, so the panel keeps up with the game however slow the odds are, and while
    the hand isn't `settled` none is started. Each row's finished odds are kept until `odds_basis` changes, so
    selecting the same cards again doesn't calculate them again. Each value stops at its `limits` (`build_report`),
    or once `watchdog` finds the app using too much memory, and is given up on (`worker.TooSlow`).

    A job that fails is logged (`failures`) and answered with odds without values, so the panel isn't left
    waiting."""

    def __init__(self, limits: Limits, watchdog: Watchdog, session: Session, failures: Failures):
        self.limits, self.watchdog, self.session, self.failures = limits, watchdog, session, failures
        self.job: Job[dict] | None = None
        self.state: GameState | None = None  # the state the job is for
        self.progress_sent = 0  # the job's progress last sent (`Control.updates`)
        self.next_partial = 0.0
        self.known: dict = {}  # finished rows for `known_for`. A job keeps the one it started with, so an
        self.known_for: tuple | None = None  # older job can't add to a newer state's.

    def update(self, state: GameState) -> dict:
        """Cancel the running job, and start one for `state`'s odds if it tracks anything and is settled. The
        payload to show at once: pending, or odds without values if nothing is tracked."""
        self.cancel()
        targets, queries = tracked(state)
        if not (targets or queries):
            return overlay.odds(state, None)
        if state.in_run and not settled(state):
            return overlay.pending(state)  # the panel keeps the last odds, greyed out, until the hand settles
        self.state = state
        basis = odds_basis(state)
        if basis != self.known_for:
            self.known, self.known_for = {}, basis
        self.job = Job(lambda control, known=self.known:
                       calculate(state, targets, queries, control, known=known, limits=self.limits,
                                 progress=control.progress, session=self.session), self.watchdog)
        self.progress_sent, self.next_partial = 0, time.monotonic() + PARTIAL_INTERVAL
        return overlay.pending(state)

    def poll(self, wait: float) -> dict | None:
        """Wait up to `wait` seconds for the job. The payload to show, if any: its odds once it's done (delivered
        once), or `partial` ones as values are found, at most one every PARTIAL_INTERVAL seconds (none if
        they're all found sooner)."""
        if self.job is None:
            time.sleep(wait)
            return None
        if self.job.wait(wait):
            finished, self.job = self.job, None
            try:
                return finished.result()
            except Exception as e:
                self.failures(f"Odds for snapshot {self.state.id}", e)
                return overlay.odds(self.state, None)
        control = self.job.control
        if control.updates != self.progress_sent and time.monotonic() >= self.next_partial:
            self.progress_sent, self.next_partial = control.updates, time.monotonic() + PARTIAL_INTERVAL
            return control.latest
        return None

    def forget_cancelled(self) -> None:
        """Drop the job if it was cancelled for a state that then failed: any other is still answered."""
        if self.job is not None and self.job.cancelled:
            self.job = None

    def cancel(self) -> None:
        if self.job is not None:
            self.job.cancel()
            self.job = None


class SearchJobs:
    """Best-discard searches, each in a job of its own.

    A state asking for a search (`GameState.search`, with a number not seen before) starts one. Only a change to
    what it depends on (`search_basis`, or the row's target) cancels it, not selecting cards; if the state still
    asks for it, it starts again. Its result (`found`, from `overlay.search`) goes on every payload until that
    changes, and the mod drops its copy once a payload comes without it.

    A search stops at its `limits`, or at the memory limit. One given up on answers from estimates if it can
    (`search_within`), and is answered the same at once when asked for again, until what it depends on changes.
    One that fails is logged (`failures`) and answered with no options, so the panel isn't left waiting."""

    def __init__(self, limits: Limits, watchdog: Watchdog, session: Session, failures: Failures):
        self.limits, self.watchdog, self.session, self.failures = limits, watchdog, session, failures
        self.job: Job[Search | TooSlow] | None = None
        self.asked: tuple[GameState, str, int, Target | CardQuery | None] | None = None  # basis, key, nonce, target
        self.given_up: dict[object, Search | TooSlow] = {}  # searches given up on for `given_up_for`, by `row_key`
        self.given_up_for: GameState | None = None
        self.found: dict | None = None  # the finished search's result, for the panel
        self.seen: tuple[str, int] | None = None  # (session, nonce) of the latest search asked for

    def update(self, state: GameState) -> None:
        """Drop the search if `state` changes what it depends on, and start the one `state` asks for if it's new.
        One already given up on, or for a row that isn't tracked, is answered at once."""
        basis = search_basis(state)
        if basis != self.given_up_for:
            self.given_up, self.given_up_for = {}, basis
        if self.asked is not None and (self.asked[0] != basis
                                       or row_key(search_target(state, self.asked[1])) != row_key(self.asked[3])):
            # What it was worked out from has changed (or the row's target, as for "Any target" when a hand is
            # added): drop it, and search again if the mod is still asking (it stops once it has a reply), so it
            # isn't left waiting for one.
            self.cancel()
            self.asked = self.found = self.seen = None
        if state.search is None:
            return
        key, nonce = state.search
        request = ((state.id or "").split(":")[0], nonce)
        if request == self.seen:
            return
        self.seen = request
        self.cancel()
        target = search_target(state, key)
        self.asked, self.found = (basis, key, nonce, target), None
        if target is None or row_key(target) in self.given_up:
            self.found = overlay.search(key, nonce, None if target is None else self.given_up[row_key(target)])
        else:
            self.job = Job(lambda control: search_within(state, target, control, self.limits, self.session),
                           self.watchdog)

    def poll(self) -> bool:
        """Whether the search has just finished, its result now in `found`."""
        if self.job is None or not self.job.wait(0):
            return False
        _, key, nonce, target = self.asked
        try:
            result = self.job.result()
        except Exception as e:
            self.failures(f"Best discard search for {key}", e)
            result = None
        self.job, self.found = None, overlay.search(key, nonce, result)
        if isinstance(result, TooSlow) or (isinstance(result, Search) and result.estimated is not None):
            self.given_up[row_key(target)] = result
        return True

    def cancel(self) -> None:
        if self.job is not None:
            self.job.cancel()
            self.job = None


def run_watch(source: StateSource, panel: Callable[[dict], object], *, poll_interval: float = 0.05,
              stop: Callable[[], bool] = lambda: False,
              limits: Limits = LIMITS, watchdog: Watchdog | None = None,
              memory_limit: Callable[[int | None], int] | None = None) -> None:
    """Work out the odds of what each new state tracks, and the best discards it asks for, in the background
    (`OddsJobs`, `SearchJobs`), for the mod's panel, until `stop()`.

    `panel` receives the payloads for the mod's odds panel (`Outbox`): a `pending` one as soon as a state arrives,
    then its odds (only the pending one while the hand isn't `settled`), partial ones as values are found, then
    all of them, each with the latest search's result on it.

    With `memory_limit`, each state sets the watchdog's limit to `memory_limit(state.memory_gb)`, the player's
    "Memory limit" setting. The setting is part of `odds_basis` and `search_basis`, so changing it starts afresh:
    what was given up on under the old limit is tried again.

    A state holding other rule jokers (`rule_jokers`) empties the engine's caches (`engine.clear_caches`), so what
    the old rules built doesn't keep its memory.

    Any error in the loop (a bad state that `GameState.from_json` didn't catch, say) is logged as a failed job is
    (`Failures`), and the app carries on with the next state. Once stopped, nothing is left running.
    """
    if watchdog is None:
        watchdog = Watchdog()  # time limits only
    session = Session()  # what the engine keeps between the jobs
    failures = Failures()
    outbox = Outbox(panel)
    odds = OddsJobs(limits, watchdog, session, failures)
    searches = SearchJobs(limits, watchdog, session, failures)
    rules_held: frozenset[str] | None = None  # `rule_jokers` of the latest state
    while not stop():
        try:
            outbox.retry()
            state = source.poll()
            if state is not None:
                if rule_jokers(state) != rules_held:
                    if rules_held is not None:
                        clear_caches()  # what the old rules built only holds memory now
                    rules_held = rule_jokers(state)
                if memory_limit is not None:
                    watchdog.memory_limit = memory_limit(state.memory_gb)
                searches.update(state)
                outbox.show(odds.update(state), searches.found)
            if searches.poll():
                outbox.show(None, searches.found)
            payload = odds.poll(poll_interval)
            if payload is not None:
                outbox.show(payload, searches.found)
        except Exception as e:
            # Something the mod would never send got past `GameState.from_json`.
            # Skip it like a malformed snapshot: the next one recovers.
            failures("Handling the game state", e)
            odds.forget_cancelled()
            time.sleep(poll_interval)
    odds.cancel()  # stopped: nothing is left calculating for a loop that's gone
    searches.cancel()


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
