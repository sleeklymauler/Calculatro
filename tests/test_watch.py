"""Background odds: engine cancellation, Job, and the watch loop."""

import gc
import json
import threading
import time
from dataclasses import replace
from fractions import Fraction

import pytest

import calculatro.__main__ as cli
from calculatro import report
from calculatro.cards import parse_query
from calculatro.engine import Cancelled, Estimate, probability
from calculatro.hands import get_target
from calculatro.log import open_log
from calculatro.rules import Rules
from calculatro.state import SCHEMA, SUITS, Card, GameState, Joker
from calculatro.worker import MEMORY, TIME, Control, Job, Limits, TooSlow, Watchdog, limited
from fakes import FakeSource, taking

pytestmark = pytest.mark.usefixtures("no_estimates")


@pytest.fixture(autouse=True)
def jobs_ended():
    """Each test's jobs have ended before the next test starts. One still running on a busy machine would call what
    the next test has patched (`report.evaluate_discard`, say), and count as that test's."""
    yield
    end = time.monotonic() + 5
    for thread in threading.enumerate():
        if thread.name == "job":
            thread.join(max(0.0, end - time.monotonic()))
            assert not thread.is_alive(), "a job outlived its test"


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


def test_job_pauses_gc():
    """The whole job, not only its searches: a pass of the collector doesn't check `cancel` (engine.gc_paused)."""
    assert gc.isenabled()
    job = Job(lambda control: gc.isenabled())
    assert job.wait(5)
    assert job.result() is False
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


def state(name: str, tracked_hands: tuple[str, ...] = ("Pair",), game_state: str = "SELECTING_HAND") -> GameState:
    """A state told apart by its blind's name."""
    return GameState(in_run=True, state=game_state, blind=name, tracked_hands=list(tracked_hands))


def watch(source, fake_odds, done, monkeypatch, timeout=5.0) -> list[dict]:
    """Run the watch loop until `done(sent)`, returning every payload it sent the panel. A state's odds are
    `{"odds": fake_odds(...)}`."""
    monkeypatch.setattr(cli, "calculate", lambda st, t, q, cancel, known, **_: {"odds": fake_odds(st, t, q, cancel)})
    sent: list[dict] = []
    end = time.monotonic() + timeout
    cli.run_watch(source, sent.append, poll_interval=0.005, stop=lambda: done(sent) or time.monotonic() > end)
    assert done(sent), sent
    return sent


def test_no_odds_until_the_hand_settles(monkeypatch):
    """While cards are dealt (or a played hand scores, or a pack is open) the hand changes card by card: no odds are
    calculated, and the panel is told they're pending, until the player is selecting cards again."""
    calculated: list[str] = []
    source = FakeSource(state("A", game_state="DRAW_TO_HAND"), state("B", game_state="HAND_PLAYED"), state("C"))
    sent: list[dict] = []
    monkeypatch.setattr(cli, "calculate", lambda st, t, q, cancel, known, **_: (
        calculated.append(st.blind) or {"odds": st.blind}))
    end = time.monotonic() + 5
    cli.run_watch(source, sent.append, poll_interval=0.005,
                  stop=lambda: sent[-1:] == [{"odds": "C"}] or time.monotonic() > end)
    assert calculated == ["C"]
    assert [p.get("pending") for p in sent[:2]] == [True, True] and sent[-1] == {"odds": "C"}


def test_new_state_cancels_running_odds(monkeypatch):
    source = FakeSource(state("A"))
    cancelled = threading.Event()

    def odds(st, t, q, cancel, known=None, **_):
        if st.blind == "B":
            return "odds B"
        while not cancel.is_set():
            time.sleep(0.001)
        cancelled.set()
        raise Cancelled

    def done(sent):
        if len(sent) == 1 and not source.queue:
            source.queue.append(state("B"))  # the game moves on while A is still calculating
        return {"odds": "odds B"} in sent

    sent = watch(source, odds, done, monkeypatch)
    assert cancelled.wait(5)
    assert sent[0].get("pending") and sent[-1] == {"odds": "odds B"}
    assert {"odds": "odds A"} not in sent


def test_failed_odds_logged_once_and_answered(monkeypatch, capsys):
    """A bug in the odds doesn't stop the app: it's logged (once while it repeats), the panel gets odds without
    values rather than waiting on them, and the next state is calculated as usual."""
    def odds(st, t, q, cancel, known=None, **_):
        if st.blind != "C":
            raise ValueError("boom")
        return {"odds": "C"}

    monkeypatch.setattr(cli, "calculate", odds)
    source = FakeSource(state("A"))
    sent = OneAtATime(source, [state("B"), state("C")])
    end = time.monotonic() + 5
    cli.run_watch(source, sent.append, poll_interval=0.005,
                  stop=lambda: sent[-1:] == [{"odds": "C"}] or time.monotonic() > end)
    assert sent[-1] == {"odds": "C"}
    answers = [p for p in sent if not p.get("pending")]
    assert answers[:2] == [{"schema": cli.overlay.SCHEMA, "targets": [], "cards": []}] * 2
    assert capsys.readouterr().err.count("ValueError: boom") == 1


class OneAtATime(list):
    """The panel's payloads, as a list whose `append` gives `source` the next of `states` once the last one is
    answered. If all of them were queued at once, each state would cancel the one before it whenever that one's job
    hadn't ended by the next poll, which a busy machine can make happen to any of them."""

    def __init__(self, source: FakeSource, states: list[GameState]):
        super().__init__()
        self.source, self.states = source, list(states)

    def append(self, payload: dict) -> None:
        super().append(payload)
        if not payload.get("pending") and self.states:
            self.source.queue.append(self.states.pop(0))


def failing_odds(monkeypatch, fail) -> list[dict]:
    """The panel's payloads from running the watch loop over states A, B, X and Y, whose odds call `fail`, then C."""
    def odds(st, t, q, cancel, known=None, **_):
        if st.blind == "C":
            return {"odds": "C"}
        fail(st.blind)

    monkeypatch.setattr(cli, "calculate", odds)
    source = FakeSource(state("A"))
    sent = OneAtATime(source, [*map(state, "BXYC")])
    end = time.monotonic() + 5
    cli.run_watch(source, sent.append, poll_interval=0.05,
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
    # A valid string that can't be written: no type check catches these.
    "surrogate-blind": lambda: state("\ud800"),
    "surrogate-id": lambda: replace(state("A"), id="\ud800"),
}


@pytest.mark.parametrize("bad", BAD_STATES.values(), ids=BAD_STATES.keys())
def test_bad_state_logged_and_the_next_one_answered(tmp_path, monkeypatch, capsys, bad):
    """A state the loop can't handle doesn't stop the app, and the panel still gets the next one's odds."""
    monkeypatch.setattr(cli, "calculate", lambda st, t, q, cancel, known, **_: {"odds": st.blind})
    odds_file = tmp_path / "odds.json"
    source, written = FakeSource(bad(), state("B")), []

    def panel(payload):
        written.append(payload)
        return cli.overlay.write(odds_file, payload)

    end = time.monotonic() + 5
    cli.run_watch(source, panel, poll_interval=0.005,
                  stop=lambda: {"odds": "B"} in written or time.monotonic() > end)
    assert json.loads(odds_file.read_text(encoding="utf-8")) == {"odds": "B"}
    # Some only failed in the console's summary, which is gone: those are answered like any other.
    err = capsys.readouterr().err
    fine = (BAD_STATES["joker-key"], BAD_STATES["unseen-suit"])
    assert ("Handling the game state failed" in err) == (bad not in fine)


def test_bad_state_after_a_running_job_isnt_a_failed_calculation(monkeypatch, capsys):
    """A bad state has cancelled the running job by the time it fails: that's not the job failing, so no failed odds
    are reported or sent for the state before it."""
    def odds(st, t, q, cancel, known=None, **_):
        if st.blind == "A":
            cancel.wait(5)
            raise Cancelled
        return f"odds {st.blind}"

    sent = watch(FakeSource(state("A"), replace(state("X"), jokers=[Joker(5)]), state("B")), odds,
                 lambda sent: {"odds": "odds B"} in sent, monkeypatch)
    assert all("odds" in p for p in sent if not p.get("pending"))  # none without values, as for a failure
    assert "Odds for snapshot" not in capsys.readouterr().err


def test_error_polling_leaves_the_running_job(monkeypatch, capsys):
    """An error unrelated to the running job doesn't lose its odds."""
    release = threading.Event()

    class Broken(FakeSource):
        def poll(self):
            if not self.queue and not release.is_set():
                release.set()
                raise OSError("boom")
            return super().poll()

    def odds(st, t, q, cancel, known=None, **_):
        release.wait(5)
        return "odds A"

    watch(Broken(state("A")), odds, lambda sent: {"odds": "odds A"} in sent, monkeypatch)
    assert "OSError: boom" in capsys.readouterr().err


def test_caches_emptied_when_the_rule_jokers_change(monkeypatch):
    """The engine's caches keep memory the next calculation's limit counts, so they're emptied when the jokers
    that change the rules do: not for the first state, another joker, or a debuff (Crimson Heart moves one every
    hand)."""
    four, shortcut, debuffed = Joker("j_four_fingers"), Joker("j_shortcut"), Joker("j_four_fingers", debuff=True)
    source = FakeSource(state("A"), replace(state("B"), jokers=[four]), replace(state("C"), jokers=[debuffed]),
                        replace(state("D"), jokers=[four, Joker("j_joker")]),
                        replace(state("E"), jokers=[four, shortcut]), replace(state("F"), jokers=[shortcut, four]),
                        state("G"))
    polled: list[str] = []  # the blind of each state handed out, the latest last
    poll = source.poll
    monkeypatch.setattr(source, "poll", lambda: (s := poll()) and (polled.append(s.blind) or s))
    cleared: list[str] = []
    monkeypatch.setattr(cli, "clear_caches", lambda: cleared.append(polled[-1]))
    watch(source, lambda st, t, q, cancel: f"odds {st.blind}", lambda w: polled[-1:] == ["G"], monkeypatch)
    assert cleared == ["B", "E", "G"]


def test_tracked_skips_unknown_repeated_and_overflowing():
    hands = ["Pair", "Pair", "Big Hand", "Three of a Kind"]
    cards = ["2+ queens", " 2+ queens ", "no such card", *[f"at least {n} spade" for n in range(1, 13)]]
    targets, queries = cli.tracked(GameState(in_run=True, tracked_hands=hands, tracked_cards=cards))
    assert [t.name for t in targets] == ["Pair", "Three of a Kind"]
    # 3 hand rows (with "Any target") take a column, leaving 2 of 6 for queries.
    assert [q.text for q in queries] == ["2+ queens", *[f"at least {n} spade" for n in range(1, 12)]]


# --- Best-discard searches ----------------------------------------------------


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


def watch_thread(source, sent, **kwargs):
    stopped = threading.Event()
    t = threading.Thread(target=cli.run_watch, args=(source, sent.append),
                         kwargs={"poll_interval": 0.005, "stop": stopped.is_set, **kwargs}, daemon=True)
    t.start()
    return stopped, t


def test_search_survives_selecting_and_rides_on_payloads(monkeypatch):
    release, calls = threading.Event(), []

    def search(state, target, cancel, progress=None, **_):
        calls.append(state.id)
        while not release.is_set():
            if cancel.is_set():
                raise Cancelled
            time.sleep(0.001)
        return Search([[2]], Fraction(1))

    monkeypatch.setattr(cli, "best_discards", search)
    source, sent = FakeSource(hand_state(1)), []
    stopped, t = watch_thread(source, sent)
    try:
        wait_for(lambda: any("targets" in p for p in sent))
        source.queue.append(hand_state(2, selected=(0,)))  # selecting cards doesn't cancel it
        wait_for(lambda: any(p["id"] == "s:2" and "targets" in p for p in sent))
        assert not any("search" in p for p in sent)
        release.set()
        wait_for(lambda: "search" in sent[-1])
        reply = {"nonce": 1, "key": "Pair", "options": [[2]]}
        assert sent[-1]["search"] == reply and sent[-1]["id"] == "s:2"
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

    def search(state, target, cancel, progress=None, **_):
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

    def search(state, target, cancel, progress=None, **_):
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

    def search(state, target, cancel, progress=None, **_):
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

    def search(state, target, cancel, progress=None, **_):
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


@pytest.mark.parametrize("query", ["at least 2 queen", "2+ nines", "no face"])
def test_card_query_search_survives_selecting(monkeypatch, query):
    """Each snapshot parses the row's query anew, and two parses of one may not be equal: that
    isn't a change of target, which would restart the search and lose the options it found."""
    release, calls = threading.Event(), []

    def search(state, target, cancel, progress=None, **_):
        calls.append(state.id)
        while not release.is_set():
            if cancel.is_set():
                raise Cancelled
            time.sleep(0.001)
        return Search([[2]], Fraction(1))

    def asking(n: int, selected=(), request=(query, 1)) -> GameState:
        return replace(hand_state(n, selected, request), tracked_hands=[], tracked_cards=[query])

    monkeypatch.setattr(cli, "best_card_discards", search)
    source, sent = FakeSource(asking(1)), []
    stopped, t = watch_thread(source, sent)
    try:
        wait_for(lambda: any(p["id"] == "s:1" and "cards" in p for p in sent))
        source.queue.append(asking(2, selected=(0,)))  # while it's searching
        wait_for(lambda: any(p["id"] == "s:2" and "cards" in p for p in sent))
        release.set()
        reply = {"nonce": 1, "key": query, "options": [[2]]}
        wait_for(lambda: sent[-1].get("search") == reply)
        source.queue.append(asking(3, selected=(2,), request=None))  # the search's own selection
        wait_for(lambda: sent[-1]["id"] == "s:3" and "cards" in sent[-1])
        assert all(p.get("search") == reply for p in sent if p["id"] == "s:3")
        assert calls == ["s:1"]
        # No longer tracked: the result goes.
        source.queue.append(replace(asking(4, selected=(2,), request=None), tracked_cards=["queen"]))
        wait_for(lambda: sent[-1]["id"] == "s:4" and "cards" in sent[-1])
        assert not any("search" in p for p in sent if p["id"] == "s:4")
    finally:
        stopped.set()
        t.join(5)


def test_failed_search_answered_without_options(monkeypatch, capsys):
    """A search that fails is logged and answered with no options, so the mod's button comes back; another click
    (a new nonce) searches again."""
    def search(state, target, cancel, progress=None, **_):
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


def with_sorts(hand: list[dict], observations: list[dict]) -> GameState:
    return GameState.from_json({"schema": SCHEMA, "in_run": True, "hand_size": len(hand), "hands_left": 1,
                                "discards_left": 1, "hand": hand, "unseen": [CLUB_9], "discard": [],
                                "observations": observations})


CLUB_9 = {"rank": "9", "rank_id": 9, "suit": "Clubs", "enhancement": "c_base"}
SPADE_10 = {"rank": "10", "rank_id": 10, "suit": "Spades", "enhancement": "c_base"}
SPADE_8 = {"rank": "8", "rank_id": 8, "suit": "Spades", "enhancement": "c_base"}


def test_odds_basis_with_sorts():
    # The face-down card between a 10 and an 8: dragging it moves its hand index,
    # not what the sort says, so the finished rows stay; a new sort changes them,
    # and the search's basis too.
    sort = [{"id": 0, "rank_id": 10, "suit": "Spades"}, {"id": 1, "hand": 1}, {"id": 2, "rank_id": 8, "suit": "Spades"}]
    base = with_sorts([SPADE_10, {"hidden": True}, SPADE_8], [{"mode": "desc", "cards": sort}])
    dragged = with_sorts([{"hidden": True}, SPADE_10, SPADE_8],
                         [{"mode": "desc", "cards": [sort[0], {"id": 1, "hand": 0}, sort[2]]}])
    assert cli.odds_basis(dragged) == cli.odds_basis(base)
    by_suit = [sort[0], sort[2], sort[1]]
    more = with_sorts([SPADE_10, {"hidden": True}, SPADE_8],
                      [{"mode": "desc", "cards": sort}, {"mode": "suit desc", "cards": by_suit}])
    none = with_sorts([SPADE_10, {"hidden": True}, SPADE_8], [])
    for changed in (more, none):
        assert cli.odds_basis(changed) != cli.odds_basis(base)
        assert cli.search_basis(changed) != cli.search_basis(base)


def test_selecting_the_same_cards_again_calculates_nothing(monkeypatch):
    """Finished rows last until something they depend on changes, and answer the snapshot asking for them."""
    calls: list[tuple] = []
    real = report.evaluate_discard

    def counted(state, target, discard, *rest, **kw):
        calls.append(tuple(state.hand[i].rank_id for i in discard))
        return real(state, target, discard, *rest, **kw)

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
        # The "Memory limit" setting changed: what was given up on under the old limit is tried again.
        (picked(8, (10,), jokers=[Joker("j_joker")], memory_gb=4), [(), (10,)]),
    ]
    source, answers = FakeSource(steps[0][0]), []

    def panel(payload):
        if not payload.get("pending") and not payload.get("partial"):  # partial ones come if the engine is slow
            answers.append(payload)
            if len(answers) < len(steps):
                source.queue.append(steps[len(answers)][0])

    end = time.monotonic() + 10
    # A value that missed its quick try would be calculated again in the exact pass: give it time on a busy machine.
    cli.run_watch(source, panel, poll_interval=0.005, limits=Limits(quick=5),
                  stop=lambda: len(answers) == len(steps) or time.monotonic() > end)
    assert [a["id"] for a in answers] == [st.id for st, _ in steps]
    expected = [call for _, step in steps for call in step]
    assert calls == expected
    assert answers[2]["targets"] == answers[0]["targets"] == answers[3]["targets"]


# --- Limits ------------------------------------------------------------------


def test_watchdog_stops_attempts_at_their_deadline_and_over_the_memory_limit():
    used = [100]
    watchdog = Watchdog(lambda: used[0], memory_limit=1000, period=3600)  # checked by hand after its first look
    control = Control(watchdog)
    with control.limited(3600) as long, control.limited(0) as due:
        watchdog.check()
        assert due.is_set() and due.reason == TIME and not long.is_set()
        used[0] = 2000
        watchdog.check()
        assert long.is_set() and long.reason == MEMORY
        # While the memory is given back, the next attempt isn't stopped for what the last one used...
        with control.limited(3600) as after:
            watchdog.check()
            assert not after.is_set()
            used[0] = 500
            watchdog.check()
            used[0] = 2000  # ...only for its own
            watchdog.check()
            assert after.is_set() and after.reason == MEMORY
        # Or once the grace has passed, if the memory was never seen to fall.
        with control.limited(3600) as unseen:
            watchdog.check()
            assert not unseen.is_set()
            watchdog.grace = 0
            watchdog.check()
            assert unseen.is_set() and unseen.reason == MEMORY


def test_each_state_sets_the_memory_limit_from_the_setting():
    watchdog = Watchdog()
    limits: list[int] = []
    source = FakeSource(*(replace(state(f"b{i}"), memory_gb=gb) for i, gb in enumerate((4, None, 1))))

    def stop():
        limits.append(watchdog.memory_limit)
        return not source.queue

    cli.run_watch(source, lambda payload: None, poll_interval=0.001, stop=stop, watchdog=watchdog,
                  memory_limit=lambda gb: (gb or 2) * 1000)
    assert limits == [0, 4000, 2000, 1000]  # none until the first state: the watchdog's own


def test_limited_gives_up_at_a_limit_but_not_when_cancelled():
    def until_stopped(cancel):
        while not cancel.is_set():
            time.sleep(0.001)
        raise Cancelled

    control = Control(Watchdog(period=0.005))
    assert limited(control, 0.05, until_stopped) == TooSlow(TIME)
    assert limited(control, 5, lambda cancel: 42) == 42
    threading.Timer(0.05, control.cancel).start()
    with pytest.raises(Cancelled):
        limited(control, 5, until_stopped)
    with pytest.raises(Cancelled):  # already cancelled
        limited(control, 5, until_stopped)
    assert limited(threading.Event(), 0.01, lambda cancel: 7) == 7  # no limits outside a job


def no_nulls(value) -> bool:
    """Whether a payload has no None anywhere (the mod's decoder has no null)."""
    if isinstance(value, dict):
        return all(no_nulls(v) for v in value.values())
    if isinstance(value, list):
        return all(no_nulls(v) for v in value)
    return value is not None


def test_quick_rows_are_sent_before_slow_ones_and_slow_ones_given_up(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(report, "evaluate_discard", taking({"Flush": 0.3, "Straight Flush": 10}, calls))
    hands = ["Pair", "Flush", "Straight Flush"]
    first = replace(hand_state(1, selected=(0,), search=None), tracked_hands=hands)
    source, sent = FakeSource(first), []
    stopped, t = watch_thread(source, sent, limits=Limits(quick=0.05, exact=0.4))
    try:
        wait_for(lambda: sent and "targets" in sent[-1] and not sent[-1].get("partial"))
        # Pair, done in the quick pass, while Flush is worked out.
        pending = {"state": "pending"}
        assert {"key": "Flush", "keep_mark": pending, "discard_mark": pending} in sent[1]["targets"]
        assert any({"key": "Pair", "keep": "50.0%", "discard": "33.3%", "trend": -1} in p["targets"]
                   and {"key": "Flush", "keep_mark": pending, "discard_mark": pending} in p["targets"]
                   for p in sent if p.get("partial"))
        rows = {r["key"]: r for r in sent[-1]["targets"]}
        assert rows["Flush"] == {"key": "Flush", "keep": "50.0%", "discard": "33.3%", "trend": -1}
        too_slow = {"state": "final", "limit": "time"}
        assert rows["Straight Flush"] == {"key": "Straight Flush", "keep": "?", "keep_mark": too_slow,
                                          "discard": "?", "discard_mark": too_slow}  # no trend
        assert all(no_nulls(p) for p in sent)
        # Given up on until something they depend on changes: the same cards again are answered at once.
        calls.clear()
        final = sent[-1]
        source.queue.append(replace(first, id="s:2"))
        wait_for(lambda: sent[-1]["id"] == "s:2" and "targets" in sent[-1])
        assert sent[-1]["targets"] == final["targets"]
        assert calls == [] and not any(p.get("partial") for p in sent if p["id"] == "s:2")
    finally:
        stopped.set()
        t.join(5)


def test_a_slow_any_target_is_approximated(monkeypatch):
    """A slow "Any target" is sent as its bounds from the hands' odds while its exact odds are worked out, and keeps
    them once those are given up on."""
    any_name = "Pair / Flush"
    odds = {"Pair": Fraction(1, 5), "Flush": Fraction(1, 10)}
    monkeypatch.setattr(report, "evaluate_discard", taking({any_name: 10}, odds=odds))
    first = replace(hand_state(1, selected=(0,), search=None), tracked_hands=["Pair", "Flush"])
    source, sent = FakeSource(first), []
    stopped, t = watch_thread(source, sent, limits=Limits(quick=0.05, exact=0.4))
    try:
        wait_for(lambda: sent and "targets" in sent[-1] and not sent[-1].get("partial"))
        refining = {"state": "refining", "how": "bounds", "low": "20.0%", "high": "30.0%"}
        assert any(p.get("partial") and p["targets"][0] == {
            "key": "Any target", "keep": "20%+", "keep_mark": refining, "discard": "20%+", "discard_mark": refining}
            for p in sent)
        final = {**refining, "state": "final", "limit": "time"}
        assert sent[-1]["targets"][0] == {"key": "Any target", "keep": "20%+", "keep_mark": final,
                                          "discard": "20%+", "discard_mark": final}  # no trend: they overlap
        assert all(no_nulls(p) for p in sent)
    finally:
        stopped.set()
        t.join(5)


def test_all_quick_rows_send_no_partial_payload(monkeypatch):
    monkeypatch.setattr(report, "evaluate_discard", taking({}))
    source, sent = FakeSource(replace(hand_state(1, selected=(0,), search=None), tracked_hands=["Pair", "Flush"])), []
    stopped, t = watch_thread(source, sent)
    try:
        wait_for(lambda: sent and "targets" in sent[-1])
        assert not any(p.get("partial") for p in sent)
    finally:
        stopped.set()
        t.join(5)


def test_search_given_up_answers_from_estimates(monkeypatch):
    """A search given up on at its limit answers from estimates of the choices it had, marked, and the same at once
    when asked for again."""
    calls: list[str] = []
    guesses = {(): Estimate(0.4, 0.35, 0.45, 1000, True), (0,): Estimate(0.6, 0.55, 0.65, 1000, True)}

    def search(state, target, cancel, progress=None, **_):
        calls.append(state.id)
        progress.start(state, [[0], [1], [2]], None, [None, (1, 0, 0)],
                       lambda discard, cancel, seconds: guesses[tuple(discard)])
        while not cancel.is_set():
            time.sleep(0.001)
        raise Cancelled

    monkeypatch.setattr(cli, "best_discards", search)
    source, sent = FakeSource(hand_state(1)), []
    stopped, t = watch_thread(source, sent, limits=Limits(search=0.1, search_estimate=0.5))
    try:
        wait_for(lambda: sent and "search" in sent[-1])
        estimated = {"nonce": 1, "key": "Pair", "options": [[0]],
                     "mark": {"state": "final", "how": "draws", "low": "55.0%", "high": "65.0%", "limit": "time"}}
        assert sent[-1]["search"] == estimated and no_nulls(sent[-1])
        source.queue.append(hand_state(2, selected=(0,), search=("Pair", 2)))
        wait_for(lambda: sent[-1].get("search", {}).get("nonce") == 2)
        assert sent[-1]["search"] == {**estimated, "nonce": 2} and calls == ["s:1"]
    finally:
        stopped.set()
        t.join(5)


def test_search_given_up_at_its_limit_and_answered_at_once_after(monkeypatch):
    calls: list[str] = []

    def search(state, target, cancel, progress=None, **_):
        calls.append(state.id)
        while not cancel.is_set():
            time.sleep(0.001)
        raise Cancelled

    monkeypatch.setattr(cli, "best_discards", search)
    source, sent = FakeSource(hand_state(1)), []
    stopped, t = watch_thread(source, sent, limits=Limits(search=0.1))
    try:
        wait_for(lambda: sent and "search" in sent[-1])
        gave_up = {"nonce": 1, "key": "Pair", "options": [], "mark": {"state": "final", "limit": "time"}}
        assert sent[-1]["search"] == gave_up
        # Asked for again: answered at once.
        source.queue.append(hand_state(2, selected=(0,), search=("Pair", 2)))
        wait_for(lambda: sent[-1].get("search", {}).get("nonce") == 2)
        assert sent[-1]["search"] == {**gave_up, "nonce": 2} and calls == ["s:1"]
        # Once what it depends on changes, it's searched again.
        source.queue.append(hand_state(3, ranks=(9, 9, 5), search=("Pair", 3)))
        wait_for(lambda: len(calls) == 2)
    finally:
        stopped.set()
        t.join(5)


# Each of run_watch's parts on its own.

from calculatro import overlay  # noqa: E402


def test_outbox_puts_the_search_on_the_latest_odds_and_retries_what_didnt_arrive(monkeypatch):
    monkeypatch.setattr(cli, "RETRY", 0.0)
    sent, locked = [], [False]
    outbox = cli.Outbox(lambda payload: False if locked[0] else sent.append(payload))
    outbox.show(None, {"nonce": 1})
    assert sent == []  # nothing to put a search on before the first odds
    outbox.show({"id": "a"}, None)
    outbox.show(None, {"nonce": 1})  # a search finished: the same odds again, with it
    assert sent == [{"id": "a"}, {"id": "a", "search": {"nonce": 1}}]
    locked[0] = True
    outbox.show({"id": "b"}, {"nonce": 1})
    outbox.show({"id": "c"}, None)  # a newer payload replaces the one waiting
    locked[0] = False
    outbox.retry()
    outbox.retry()  # delivered once
    assert sent[2:] == [{"id": "c"}]


def test_failures_logged_once_for_each_kind_and_place(capsys):
    failures = cli.Failures()
    for value in (1, 2):
        try:
            raise ValueError(value)
        except ValueError as e:
            failures("Something", e)
    err = capsys.readouterr().err
    assert err.count("Something failed") == 1 and "ValueError: 1" in err


def test_odds_jobs_answer_each_state_and_keep_finished_rows(monkeypatch):
    knowns: list[dict] = []

    def calculate(state, targets, queries, cancel, known, **_):
        knowns.append(known)
        return {"id": state.id, "rows": [t.name for t in targets]}

    monkeypatch.setattr(cli, "calculate", calculate)
    odds = cli.OddsJobs(cli.LIMITS, Watchdog(), cli.Session(), cli.Failures())
    first = hand_state(1, search=None)
    assert odds.update(first) == overlay.pending(first)
    assert odds.poll(5) == {"id": "s:1", "rows": ["Pair"]}
    assert odds.job is None and odds.poll(0) is None  # delivered once
    # Selecting cards keeps the finished rows; a new hand starts afresh.
    odds.update(hand_state(2, selected=(0,), search=None))
    odds.poll(5)
    odds.update(hand_state(3, ranks=(9, 9, 5), search=None))
    odds.poll(5)
    assert knowns[0] is knowns[1] and knowns[2] is not knowns[0]
    # Nothing tracked, or a hand still being dealt: answered at once, no job.
    untracked = replace(first, tracked_hands=[])
    assert odds.update(untracked) == overlay.odds(untracked, None) and odds.job is None
    dealing = replace(first, state="DRAW_TO_HAND")
    assert odds.update(dealing) == overlay.pending(dealing) and odds.job is None
    assert len(knowns) == 3


def test_odds_jobs_answer_a_failed_job_without_values(monkeypatch, capsys):
    def calculate(state, *_, **__):
        raise ValueError("boom")

    monkeypatch.setattr(cli, "calculate", calculate)
    odds = cli.OddsJobs(cli.LIMITS, Watchdog(), cli.Session(), cli.Failures())
    state = hand_state(1, search=None)
    odds.update(state)
    assert odds.poll(5) == overlay.odds(state, None)
    assert "Odds for snapshot s:1 failed" in capsys.readouterr().err


def test_search_jobs_search_each_request_once_and_answer_one_given_up_on_at_once(monkeypatch):
    calls: list[str] = []

    def search_within(state, *_):
        calls.append(state.id)
        return TooSlow(TIME)

    monkeypatch.setattr(cli, "search_within", search_within)
    searches = cli.SearchJobs(cli.LIMITS, Watchdog(), cli.Session(), cli.Failures())
    assert not searches.poll()
    searches.update(hand_state(1))
    wait_for(searches.poll)
    gave_up = {"nonce": 1, "key": "Pair", "options": [], "mark": {"state": "final", "limit": "time"}}
    assert searches.found == gave_up and not searches.poll()
    searches.update(hand_state(2, selected=(0,)))  # the same request: kept
    assert searches.found == gave_up and searches.job is None
    searches.update(hand_state(3, selected=(0,), search=("Pair", 2)))  # asked again: answered at once
    assert searches.found == {**gave_up, "nonce": 2} and searches.job is None and calls == ["s:1"]
    searches.update(hand_state(4, ranks=(9, 9, 5), search=None))  # a new hand drops it
    assert searches.found is None
