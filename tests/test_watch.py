"""Background odds: engine cancellation, Job, and the watch loop."""

from __future__ import annotations

import gc
import json
import threading
import time
from dataclasses import replace

import pytest

import calculatro.__main__ as cli
from calculatro import report
from calculatro.cards import parse_query
from calculatro.engine import Cancelled, probability
from calculatro.hands import get_target
from calculatro.log import open_log
from calculatro.rules import Rules
from calculatro.state import SUITS, Card, GameState, Joker
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


def state(name: str, tracked_hands: tuple[str, ...] = ("Pair",), game_state: str = "SELECTING_HAND") -> GameState:
    """A state told apart by its blind's name."""
    return GameState(in_run=True, state=game_state, blind=name, tracked_hands=list(tracked_hands))


def watch(source, fake_odds, done, monkeypatch, timeout=5.0) -> list[str]:
    """Run the watch loop until `done(writes)`, returning everything it wrote."""
    monkeypatch.setattr(cli, "calculate", lambda st, t, q, cancel, known: (fake_odds(st, t, q, cancel), {}))
    writes: list[str] = []
    end = time.monotonic() + timeout
    cli.run_watch(source, writes.append, patience=0.05, poll_interval=0.005,
                  stop=lambda: done(writes) or time.monotonic() > end)
    assert done(writes), writes
    return writes


def test_no_odds_until_the_hand_settles(monkeypatch):
    """While cards are dealt (or a played hand scores, or a pack is open) the hand changes card by card: no odds are
    calculated, and the panel is told they're pending, until the player is selecting cards again."""
    calculated: list[str] = []
    source = FakeSource(state("A", game_state="DRAW_TO_HAND"), state("B", game_state="HAND_PLAYED"), state("C"))
    sent: list[dict] = []
    monkeypatch.setattr(cli, "calculate", lambda st, t, q, cancel, known: (
        calculated.append(st.blind) or f"odds {st.blind}", {"odds": st.blind}))
    writes: list[str] = []
    end = time.monotonic() + 5
    cli.run_watch(source, writes.append, sent.append, patience=0.05, poll_interval=0.005,
                  stop=lambda: any("odds C" in w for w in writes) or time.monotonic() > end)
    assert calculated == ["C"]
    assert writes[0].endswith("\n" + cli.WAITING_FOR_HAND) and writes[1].endswith("\n" + cli.WAITING_FOR_HAND)
    assert [p.get("pending") for p in sent[:2]] == [True, True] and sent[-1] == {"odds": "C"}


def test_fast_odds_print_with_summary(monkeypatch):
    writes = watch(FakeSource(state("A")), lambda st, t, q, cancel: f"odds {st.blind}",
                   lambda w: len(w) == 1, monkeypatch)
    assert "Blind: A" in writes[0] and writes[0].endswith("\nodds A")


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
    assert "Blind: A" in writes[0] and writes[0].endswith("\n" + cli.CALCULATING)
    assert writes[1] == "odds A"


def test_new_state_cancels_running_odds(monkeypatch):
    source = FakeSource(state("A"))
    cancelled = threading.Event()

    def odds(st, t, q, cancel, known=None):
        if st.blind == "B":
            return "odds B"
        while not cancel.is_set():
            time.sleep(0.001)
        cancelled.set()
        raise Cancelled

    def done(writes):
        if len(writes) == 1 and not source.queue and "Blind: B" not in writes[0]:
            source.queue.append(state("B"))  # the game moves on while A is still calculating
        return any("odds B" in w for w in writes)

    writes = watch(source, odds, done, monkeypatch)
    assert cancelled.wait(5)
    assert writes[0].endswith(cli.CALCULATING)
    assert "Blind: B" in writes[1] and writes[1].endswith("\nodds B")
    assert not any("odds A" in w for w in writes)


def test_failed_odds_logged_once_and_answered(monkeypatch, capsys):
    """A bug in the odds doesn't stop the app: it's logged (once while it repeats), the panel gets odds without
    values rather than waiting on them, and the next state is calculated as usual."""
    def odds(st, t, q, cancel, known=None):
        if st.blind != "C":
            raise ValueError("boom")
        return "odds C", {"odds": "C"}

    monkeypatch.setattr(cli, "calculate", odds)
    source, sent, writes = FakeSource(state("A"), state("B"), state("C")), [], []
    end = time.monotonic() + 5
    cli.run_watch(source, writes.append, sent.append, patience=0.05, poll_interval=0.005,
                  stop=lambda: sent[-1:] == [{"odds": "C"}] or time.monotonic() > end)
    assert sent[-1] == {"odds": "C"}
    answers = [p for p in sent if not p.get("pending")]
    assert answers[:2] == [{"schema": cli.overlay.SCHEMA, "id": None, "targets": [], "cards": []}] * 2
    assert writes[0].endswith("\n" + cli.ODDS_FAILED) and writes[1].endswith("\n" + cli.ODDS_FAILED)
    assert capsys.readouterr().err.count("ValueError: boom") == 1


def failing_odds(monkeypatch, fail) -> list[dict]:
    """The panel's payloads from running the watch loop over states A, B, X and Y, whose odds call `fail`, then C."""
    def odds(st, t, q, cancel, known=None):
        if st.blind == "C":
            return "odds C", {"odds": "C"}
        fail(st.blind)

    monkeypatch.setattr(cli, "calculate", odds)
    source, sent = FakeSource(*map(state, "ABXYC")), []
    end = time.monotonic() + 5
    cli.run_watch(source, lambda text: None, sent.append, patience=5, poll_interval=0.05,
                  stop=lambda: sent[-1:] == [{"odds": "C"}] or time.monotonic() > end)
    return sent


def test_failures_logged_once_though_alternating_or_with_changing_values(monkeypatch, capsys):
    """Comparing with only the last failure, or the whole text, would log these with every state."""
    def fail(blind):
        if blind in "AX":
            raise ValueError(f"boom {blind}")
        raise TypeError(f"bang {blind}")

    failing_odds(monkeypatch, fail)
    err = capsys.readouterr().err
    assert err.count("ValueError: boom") == 1 and err.count("TypeError: bang") == 1


def test_failure_that_cant_be_logged_doesnt_stop_the_app(monkeypatch):
    class Full:
        def write(self, text):
            raise OSError("disk full")

        def flush(self):
            pass

    def fail(blind):
        raise ValueError("boom")

    monkeypatch.setattr("sys.stderr", Full())
    assert failing_odds(monkeypatch, fail)[-1] == {"odds": "C"}


def test_failure_with_a_lone_surrogate_is_logged(monkeypatch, tmp_path):
    """The log's encoding mustn't raise on text a bad snapshot can hold."""
    def fail(blind):
        raise ValueError("bad \udc80 text")

    with open_log(tmp_path / "calculatro.log") as log:
        monkeypatch.setattr("sys.stderr", log)
        assert failing_odds(monkeypatch, fail)[-1] == {"odds": "C"}
    assert "ValueError: bad \\udc80 text" in (tmp_path / "calculatro.log").read_text(encoding="utf-8")


BAD_STATES = {
    # What `GameState.from_json` rejects now, in case another such field gets past it.
    "id-with-search": lambda: replace(state("A"), id=5, search=("Pair", 1)),
    "joker-key": lambda: replace(state("A"), jokers=[Joker(5)]),
    "hand-rank": lambda: replace(state("A"), hand=[Card(rank=["x"])]),
    "unseen-suit": lambda: replace(state("A"), unseen=[Card(suit=["x"])]),
    # A valid string that can't be printed or written: no type check catches these.
    "surrogate-blind": lambda: state("\ud800"),
    "surrogate-id": lambda: replace(state("A"), id="\ud800"),
}


@pytest.mark.parametrize("bad", BAD_STATES.values(), ids=BAD_STATES.keys())
def test_bad_state_logged_and_the_next_one_answered(tmp_path, monkeypatch, capsys, bad):
    """A state the loop can't handle doesn't stop the app, and the panel still gets the next one's odds."""
    monkeypatch.setattr(cli, "calculate", lambda st, t, q, cancel, known: (f"odds {st.blind}", {"odds": st.blind}))
    odds_file = tmp_path / "calculatro_odds.json"
    source, writes = FakeSource(bad(), state("B")), []
    end = time.monotonic() + 5
    cli.run_watch(source, lambda text: writes.append(text.encode("utf-8")),  # encoded like the real stdout
                  lambda payload: cli.overlay.write(odds_file, payload), patience=0.05, poll_interval=0.005,
                  stop=lambda: b"odds B" in b"".join(writes) or time.monotonic() > end)
    assert b"odds B" in b"".join(writes)
    assert json.loads(odds_file.read_text(encoding="utf-8")) == {"odds": "B"}
    assert "Handling the game state failed" in capsys.readouterr().err


def test_bad_state_after_a_running_job_isnt_a_failed_calculation(monkeypatch, capsys):
    """A bad state has cancelled the running job by the time it fails: that's not the job failing, so no failed odds
    are reported or sent for the state before it."""
    def odds(st, t, q, cancel, known=None):
        if st.blind == "A":
            cancel.wait(5)
            raise Cancelled
        return f"odds {st.blind}"

    writes = watch(FakeSource(state("A"), replace(state("X"), jokers=[Joker(5)]), state("B")), odds,
                   lambda w: any("odds B" in text for text in w), monkeypatch)
    assert not any(cli.ODDS_FAILED in text for text in writes)
    assert "Odds for snapshot" not in capsys.readouterr().err


def test_error_polling_leaves_the_running_job(monkeypatch, capsys):
    """An error unrelated to the running job doesn't lose its odds, or the summary printed with them."""
    release = threading.Event()

    class Broken(FakeSource):
        def poll(self):
            if not self.queue and not release.is_set():
                release.set()
                raise OSError("boom")
            return super().poll()

    def odds(st, t, q, cancel, known=None):
        release.wait(5)
        return "odds A"

    writes = watch(Broken(state("A")), odds, lambda w: any("odds A" in text for text in w), monkeypatch)
    assert any("Blind: A" in text and "odds A" in text for text in writes)
    assert "OSError: boom" in capsys.readouterr().err


def test_failure_printing_finished_odds_isnt_repeated(monkeypatch, capsys):
    """Odds that can't be printed are dropped, not printed again on every pass of the loop."""
    monkeypatch.setattr(cli, "calculate", lambda st, t, q, cancel, known: (f"odds {st.blind}", {"odds": st.blind}))
    attempts, writes = [], []

    def write(text):
        if "odds A" in text:
            attempts.append(text)
            raise OSError("boom")
        writes.append(text)

    source = FakeSource(state("A"))
    end = time.monotonic() + 5
    queued = []

    def stop():
        if attempts and not queued:
            queued.append(True)
            source.queue.append(state("B"))
        return any("odds B" in text for text in writes) or time.monotonic() > end

    cli.run_watch(source, write, patience=5, poll_interval=0.005, stop=stop)
    assert any("odds B" in text for text in writes)
    assert len(attempts) == 1
    assert capsys.readouterr().err.count("OSError: boom") == 1


def test_summary_only_without_targets(monkeypatch):
    writes: list[str] = []
    cli.run_watch(FakeSource(state("A", ())), writes.append, poll_interval=0.005,
                  stop=lambda: bool(writes))
    assert len(writes) == 1 and "Blind: A" in writes[0] and writes[0].endswith(cli.NOTHING_TRACKED)


def test_tracked_skips_unknown_repeated_and_overflowing():
    hands = ["Pair", "Pair", "Big Hand", "Three of a Kind"]
    cards = ["2+ queens", "2+ queens", "no such card", *[f"at least {n} spade" for n in range(1, 13)]]
    targets, queries = cli.tracked(GameState(in_run=True, tracked_hands=hands, tracked_cards=cards))
    assert [t.name for t in targets] == ["Pair", "Three of a Kind"]
    # 3 hand rows (with "Any target") take a column, leaving 2 of 6 for queries.
    assert [q.text for q in queries] == ["2+ queens", *[f"at least {n} spade" for n in range(1, 12)]]


# --- Best-discard searches ----------------------------------------------------

from fractions import Fraction  # noqa: E402

from calculatro.search import Search  # noqa: E402


def hand_state(n: int, selected=(), search=("Pair", 1), ranks=(9, 9, 2)) -> GameState:
    hand = [Card(rank=str(r), rank_id=r, suit=s, enhancement="c_base", highlighted=i in selected)
            for i, (r, s) in enumerate(zip(ranks, SUITS))]
    deck = [Card(rank=str(r), rank_id=r, suit="Hearts", enhancement="c_base") for r in range(3, 7)]
    return GameState(in_run=True, id=f"s:{n}", hand_size=3, discards_left=1, hand=hand, unseen=deck,
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
        # A new hand: the result goes (the mod stopped asking once it had it).
        source.queue.append(hand_state(4, ranks=(9, 9, 5), search=None))
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
        assert cancelled.wait(5)  # the cancelled search's thread may not have finished yet
        assert sent[-1]["search"] == {"nonce": 2, "key": "Pair", "options": [[]]}
    finally:
        stopped.set()
        t.join(5)


def test_search_started_again_when_its_basis_changes(monkeypatch):
    """A change the hand's card objects don't show (a Tarot changing a card in place, a joker sold) cancels the
    search; the mod is still waiting for its reply, so it runs again on the new state rather than never answering."""
    calls = []

    def search(state, target, cancel):
        calls.append(state.id)
        if state.id == "s:2":
            return Search([[0]], Fraction(1))
        while not cancel.is_set():
            time.sleep(0.001)
        raise Cancelled

    monkeypatch.setattr(cli, "best_discards", search)
    source, sent = FakeSource(hand_state(1)), []
    stopped, t = watch_thread(source, sent)
    try:
        wait_for(lambda: calls == ["s:1"])
        source.queue.append(hand_state(2, ranks=(9, 9, 3)))  # the same request, a card changed
        wait_for(lambda: "search" in sent[-1])
        assert sent[-1]["search"] == {"nonce": 1, "key": "Pair", "options": [[0]]}
        assert calls == ["s:1", "s:2"]
    finally:
        stopped.set()
        t.join(5)


def test_finished_search_dropped_and_redone_when_its_basis_changes(monkeypatch):
    calls = []

    def search(state, target, cancel):
        calls.append(state.id)
        return Search([[len(calls)]], Fraction(1))

    monkeypatch.setattr(cli, "best_discards", search)
    source, sent = FakeSource(hand_state(1)), []
    stopped, t = watch_thread(source, sent)
    try:
        wait_for(lambda: sent and "search" in sent[-1])
        # The mod had stopped asking: the result goes, and isn't worked out again.
        source.queue.append(replace(hand_state(2, search=None), hand_size=4))
        wait_for(lambda: sent[-1]["id"] == "s:2" and "targets" in sent[-1])
        assert not any("search" in p for p in sent if p["id"] == "s:2") and calls == ["s:1"]
        # Still asking (it hadn't read the reply yet): worked out again.
        source.queue.append(replace(hand_state(3), hand_size=5))
        wait_for(lambda: "search" in sent[-1])
        assert sent[-1]["search"]["options"] == [[2]] and calls == ["s:1", "s:3"]
    finally:
        stopped.set()
        t.join(5)


def test_any_target_search_follows_its_hands(monkeypatch):
    calls = []

    def search(state, target, cancel):
        calls.append((state.id, target.hands))
        return Search([[0]], Fraction(1))

    def at(n, hands, cards=()):
        return replace(hand_state(n, search=("Any target", 1)), tracked_hands=hands, tracked_cards=list(cards))

    monkeypatch.setattr(cli, "best_discards", search)
    source, sent = FakeSource(at(1, ["Pair", "Flush"])), []
    stopped, t = watch_thread(source, sent)
    try:
        wait_for(lambda: sent and "search" in sent[-1])
        source.queue.append(at(2, ["Pair", "Flush"], ["2+ queens"]))  # another row: it stands
        wait_for(lambda: sent[-1]["id"] == "s:2" and "targets" in sent[-1])
        assert all("search" in p for p in sent if p["id"] == "s:2") and len(calls) == 1
        source.queue.append(at(3, ["Pair", "Flush", "Straight"]))  # another hand: searched again
        wait_for(lambda: len(calls) == 2 and "search" in sent[-1])
        assert calls[1][0] == "s:3" and set(calls[1][1]) == {"Pair", "Flush", "Straight"}
    finally:
        stopped.set()
        t.join(5)


def test_failed_search_answered_without_options(monkeypatch, capsys):
    """A search that fails is logged and answered with no options, so the mod's button comes back; another click
    (a new nonce) searches again."""
    def search(state, target, cancel):
        if state.search[1] == 1:
            raise ValueError("boom")
        return Search([[0]], Fraction(1))

    monkeypatch.setattr(cli, "best_discards", search)
    source, sent = FakeSource(hand_state(1)), []
    stopped, t = watch_thread(source, sent)
    try:
        wait_for(lambda: sent and "search" in sent[-1])
        assert sent[-1]["search"] == {"nonce": 1, "key": "Pair", "options": []}
        assert "ValueError: boom" in capsys.readouterr().err
        source.queue.append(hand_state(2, search=("Pair", 2)))
        wait_for(lambda: sent[-1].get("search", {}).get("nonce") == 2)
        assert sent[-1]["search"]["options"] == [[0]]
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


def test_odds_basis():
    hand = [Card(rank="9", rank_id=9, suit="Spades"), Card(rank="2", rank_id=2, suit="Clubs")]
    base = GameState(in_run=True, id="s:1", state="SELECTING_HAND", hand=hand, discards_left=2)
    basis = cli.odds_basis(base)
    for same in (replace(base, id="s:2"), replace(base, state=None), replace(base, tracked_hands=["Pair"]),
                 replace(base, search=("Pair", 1)), replace(base, hand=hand[::-1]),
                 replace(base, hand=[replace(hand[0], highlighted=True), hand[1]])):
        assert cli.odds_basis(same) == basis
    for changed in (replace(base, discards_left=1), replace(base, jokers=[Joker("j_joker")]),
                    replace(base, unseen=hand[:1]), replace(base, blind="The Hook"),
                    replace(base, hand=hand[:1]), replace(base, hand=[replace(hand[0], debuff=True), hand[1]])):
        assert cli.odds_basis(changed) != basis


def test_selecting_the_same_cards_again_calculates_nothing(monkeypatch):
    """Finished rows last until something they depend on changes, and answer the snapshot asking for them."""
    calls: list[tuple] = []
    real = report.evaluate_discard

    def counted(state, target, discard, *rest):
        calls.append(tuple(state.hand[i].rank_id for i in discard))
        return real(state, target, discard, *rest)

    monkeypatch.setattr(report, "evaluate_discard", counted)
    hand = [Card(rank=str(r), rank_id=r, suit="Spades", enhancement="c_base") for r in (9, 10, 12)]
    unseen = [Card(rank=str(r), rank_id=r, suit="Hearts", enhancement="c_base") for r in (9, 10, 12, 13)]
    base = GameState(in_run=True, state="SELECTING_HAND", hand_size=3, hands_left=2, discards_left=2,
                     hand=hand, unseen=unseen, tracked_hands=["Pair"])

    def picked(n: int, ranks: tuple[int, ...], **changes) -> GameState:
        st = replace(base, id=f"s:{n}", **changes)
        return replace(st, hand=[replace(c, highlighted=c.rank_id in ranks) for c in st.hand])

    steps = [
        (picked(1, (9,)), [(), (9,)]),
        (picked(2, (10,)), [(10,)]),  # another selection: Keep is known
        (picked(3, (9,)), []),  # back to the first
        (picked(4, (9,), hand=hand[::-1]), []),  # the hand sorted
        (picked(5, (9,), tracked_hands=["Pair", "Pair"]), []),
        (picked(6, (9,), jokers=[Joker("j_joker")]), [(), (9,)]),  # a joker gained: nothing carries over
        (picked(7, (10,), jokers=[Joker("j_joker")]), [(10,)]),
    ]
    source, answers = FakeSource(steps[0][0]), []

    def panel(payload):
        if not payload.get("pending"):
            answers.append(payload)
            if len(answers) < len(steps):
                source.queue.append(steps[len(answers)][0])

    end = time.monotonic() + 10
    cli.run_watch(source, lambda text: None, panel, patience=0.05, poll_interval=0.005,
                  stop=lambda: len(answers) == len(steps) or time.monotonic() > end)
    assert [a["id"] for a in answers] == [st.id for st, _ in steps]
    expected = [call for _, step in steps for call in step]
    assert calls == expected
    assert answers[2]["targets"] == answers[0]["targets"] == answers[3]["targets"]
