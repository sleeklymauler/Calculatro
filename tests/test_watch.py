"""Background odds: engine cancellation, Job, and the watch loop."""

from __future__ import annotations

import gc
import threading
import time

import pytest

import calculatro.__main__ as cli
from calculatro.engine import Cancelled, probability
from calculatro.hands import get_target
from calculatro.rules import Rules
from calculatro.state import SUITS, Card, GameState
from calculatro.worker import Job


def test_probability_cancelled():
    deck = [Card(rank=str(r), rank_id=r, suit=s, enhancement="c_base") for s in SUITS for r in range(2, 15)]
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        probability(get_target("Straight Flush"), [], deck, 5, Rules(four_fingers=True), cancel)
    assert gc.isenabled()  # paused during the search only


def test_overlapping_searches_restore_gc():
    """A cancelled search finishing while another runs must not turn the collector back on early, or leave it off."""
    deck = [Card(rank=str(r), rank_id=r, suit=s, enhancement="c_base") for s in SUITS for r in range(2, 15)]
    started, release = threading.Event(), threading.Event()
    seen = []

    class Slow(threading.Event):
        def is_set(self):
            started.set()
            release.wait(5)
            seen.append(gc.isenabled())
            return False

    t = threading.Thread(target=probability, args=(get_target("Pair"), [], deck, 2, Rules(), Slow()))
    t.start()
    assert started.wait(5)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):  # starts and ends while the other search runs
        probability(get_target("Straight Flush"), [], deck, 5, Rules(), cancel)
    assert not gc.isenabled()
    release.set()
    t.join(5)
    assert seen and not any(seen)
    assert gc.isenabled()


def test_job_result_error_and_cancel():
    ok = Job(lambda cancel: 42)
    assert ok.wait(5) and ok.result() == 42

    def fail(cancel):
        raise ValueError("boom")
    failed = Job(fail)
    assert failed.wait(5)
    with pytest.raises(ValueError, match="boom"):
        failed.result()

    def until_cancelled(cancel):
        while not cancel.is_set():
            time.sleep(0.001)
        raise Cancelled
    slow = Job(until_cancelled)
    assert not slow.wait(0.05)
    slow.cancel()
    assert slow.wait(5) and slow.cancelled
    with pytest.raises(Cancelled):
        slow.result()


class FakeSource:
    """Hands out queued states, one per poll(); the test adds more as it goes."""

    def __init__(self, *states: GameState):
        self.queue = list(states)

    def poll(self) -> GameState | None:
        return self.queue.pop(0) if self.queue else None


def state(name: str, tracked_hands: tuple[str, ...] = ("Pair",)) -> GameState:
    return GameState(in_run=True, state=name, tracked_hands=list(tracked_hands))


def watch(source, fake_odds, done, monkeypatch, timeout=5.0) -> list[str]:
    """Run the watch loop until `done(writes)`, returning everything it wrote."""
    monkeypatch.setattr(cli, "calculate", lambda st, t, q, cancel: (fake_odds(st, t, q, cancel), {}))
    writes: list[str] = []
    end = time.monotonic() + timeout
    cli.run_watch(source, writes.append, patience=0.05, poll_interval=0.005,
                  stop=lambda: done(writes) or time.monotonic() > end)
    assert done(writes), writes
    return writes


def test_fast_odds_print_with_summary(monkeypatch):
    writes = watch(FakeSource(state("A")), lambda st, t, q, cancel: f"odds {st.state}",
                   lambda w: len(w) == 1, monkeypatch)
    assert "State: A" in writes[0] and writes[0].endswith("\nodds A")


def test_slow_odds_print_summary_first(monkeypatch):
    release = threading.Event()

    def slow(st, t, q, cancel):
        release.wait(5)
        return "odds A"

    def done(writes):
        if writes:
            release.set()  # only finish once the placeholder has been printed
        return len(writes) == 2

    writes = watch(FakeSource(state("A")), slow, done, monkeypatch)
    assert "State: A" in writes[0] and writes[0].endswith("\n" + cli.CALCULATING)
    assert writes[1] == "odds A"


def test_new_state_cancels_running_odds(monkeypatch):
    source = FakeSource(state("A"))
    cancelled = threading.Event()

    def odds(st, t, q, cancel):
        if st.state == "B":
            return "odds B"
        while not cancel.is_set():
            time.sleep(0.001)
        cancelled.set()
        raise Cancelled

    def done(writes):
        if len(writes) == 1 and not source.queue and "State: B" not in writes[0]:
            source.queue.append(state("B"))  # the game moves on while A is still calculating
        return any("odds B" in w for w in writes)

    writes = watch(source, odds, done, monkeypatch)
    assert cancelled.wait(5)
    assert writes[0].endswith(cli.CALCULATING)
    assert "State: B" in writes[1] and writes[1].endswith("\nodds B")
    assert not any("odds A" in w for w in writes)


def test_summary_only_without_targets(monkeypatch):
    writes: list[str] = []
    cli.run_watch(FakeSource(state("A", ())), writes.append, poll_interval=0.005,
                  stop=lambda: bool(writes))
    assert len(writes) == 1 and "State: A" in writes[0] and writes[0].endswith(cli.NOTHING_TRACKED)


def test_tracked_skips_unknown_repeated_and_overflowing():
    hands = ["Pair", "Pair", "Big Hand", "Three of a Kind"]
    cards = ["2+ queens", "2+ queens", "no such card", *[f"at least {n} spade" for n in range(1, 13)]]
    targets, queries = cli.tracked(GameState(in_run=True, tracked_hands=hands, tracked_cards=cards))
    assert [t.name for t in targets] == ["Pair", "Three of a Kind"]
    # 3 hand rows (with "Any target") take a column, leaving 2 of 6 for queries.
    assert [q.text for q in queries] == ["2+ queens", *[f"at least {n} spade" for n in range(1, 12)]]
