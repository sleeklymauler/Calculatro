"""Background odds: engine cancellation, Job, and the watch loop."""

from __future__ import annotations

import gc
import threading
import time

import pytest

import calculatro.__main__ as cli
from calculatro.cards import parse_query
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


# --- Best-discard searches ----------------------------------------------------

from dataclasses import replace  # noqa: E402
from fractions import Fraction  # noqa: E402

from calculatro.search import Search  # noqa: E402


def hand_state(n: int, selected=(), search=("Pair", 1), ranks=(9, 9, 2)) -> GameState:
    hand = [Card(rank=str(r), rank_id=r, suit=s, enhancement="c_base", highlighted=i in selected)
            for i, (r, s) in enumerate(zip(ranks, SUITS))]
    deck = [Card(rank=str(r), rank_id=r, suit="Hearts", enhancement="c_base") for r in range(3, 7)]
    return GameState(in_run=True, id=f"s:{n}", hand_size=3, discards_left=1, hand=hand, deck=deck,
                     tracked_hands=["Pair"], search=search)


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end
        time.sleep(0.005)


def watch_thread(source, sent, writes=None):
    stopped = threading.Event()
    t = threading.Thread(target=cli.run_watch, args=(source, (writes if writes is not None else []).append,
                                                       sent.append),
                         kwargs={"poll_interval": 0.005, "stop": stopped.is_set}, daemon=True)
    t.start()
    return stopped, t


def test_search_survives_selecting_and_rides_on_payloads(monkeypatch):
    release, calls = threading.Event(), []

    def search(state, target, cancel):
        calls.append(state.id)
        while not release.is_set():
            if cancel.is_set():
                raise Cancelled
            time.sleep(0.001)
        return Search([[2]], Fraction(1))

    monkeypatch.setattr(cli, "best_discards", search)
    source, sent, writes = FakeSource(hand_state(1)), [], []
    stopped, t = watch_thread(source, sent, writes)
    try:
        wait_for(lambda: any("targets" in p for p in sent))
        source.queue.append(hand_state(2, selected=(0,)))  # selecting cards doesn't cancel it
        wait_for(lambda: any(p["id"] == "s:2" and "targets" in p for p in sent))
        assert not any("search" in p for p in sent)
        release.set()
        wait_for(lambda: "search" in sent[-1])
        reply = {"nonce": 1, "key": "Pair", "options": [[2]]}
        assert sent[-1]["search"] == reply and sent[-1]["id"] == "s:2"
        assert any(w.startswith("Best discard for Pair: discard 2") for w in writes)
        source.queue.append(hand_state(3, selected=(2,)))  # the search's own selection
        wait_for(lambda: sent[-1]["id"] == "s:3" and "targets" in sent[-1])
        assert all(p.get("search") == reply for p in sent if p["id"] == "s:3")
        source.queue.append(hand_state(4, ranks=(9, 9, 5)))  # a new hand: the result goes
        wait_for(lambda: sent[-1]["id"] == "s:4" and "targets" in sent[-1])
        assert not any("search" in p for p in sent if p["id"] == "s:4")
        assert calls == ["s:1"]  # the same request isn't searched again
    finally:
        stopped.set()
        t.join(5)


def test_new_hand_cancels_search(monkeypatch):
    cancelled = threading.Event()

    def search(state, target, cancel):
        if state.id == "s:2":
            return Search([[]], Fraction(1))
        while not cancel.is_set():
            time.sleep(0.001)
        cancelled.set()
        raise Cancelled

    monkeypatch.setattr(cli, "best_discards", search)
    source, sent = FakeSource(hand_state(1)), []
    stopped, t = watch_thread(source, sent)
    try:
        wait_for(lambda: any("targets" in p for p in sent))
        source.queue.append(hand_state(2, ranks=(9, 9, 5), search=("Pair", 2)))
        wait_for(lambda: "search" in sent[-1])
        assert cancelled.is_set()
        assert sent[-1]["search"] == {"nonce": 2, "key": "Pair", "options": [[]]}
    finally:
        stopped.set()
        t.join(5)


def test_search_for_an_untracked_row_has_no_options():
    source, sent = FakeSource(hand_state(1, search=("Flush", 1))), []
    stopped, t = watch_thread(source, sent)
    try:
        wait_for(lambda: sent)
        assert sent[0]["search"] == {"nonce": 1, "key": "Flush", "options": []}
    finally:
        stopped.set()
        t.join(5)


def test_search_target():
    state = GameState(in_run=True, tracked_hands=["Pair", "Flush"])
    assert cli.search_target(state, "Flush") == get_target("Flush")
    assert cli.search_target(state, "Any target").hands == ("Pair", "Flush") or \
        set(cli.search_target(state, "Any target").hands) == {"Pair", "Flush"}
    assert cli.search_target(state, "Straight") is None
    assert cli.search_target(replace(state, tracked_hands=["Pair"]), "Any target") is None
    with_cards = replace(state, tracked_cards=["2+ queens"])
    assert cli.search_target(with_cards, "2+ queens").label == parse_query("2+ queens").label
    assert cli.search_target(with_cards, "3+ queens") is None
