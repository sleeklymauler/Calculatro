"""The odds panel and target editor: the app's odds file, and the mod reading it and drawing the panel."""

from __future__ import annotations

import json
import re
import sys
import time
from fractions import Fraction

import pytest

import calculatro.__main__ as cli
from calculatro import overlay
from calculatro.cards import CardQuery, parse_query
from calculatro.hands import get_target
from calculatro.report import PENDING, SAMPLE, Approx, CardOdds, Report, TargetOdds, build_report
from calculatro.rules import Rules
from calculatro.search import Search
from calculatro.state import SUIT_SHORT, SUITS, Card, GameState
from calculatro.worker import MEMORY, TIME, TooSlow
from test_state import lua  # noqa: F401  (fixture: the mod running against a mocked game)
from test_watch import FakeSource

pytest.importorskip("lupa")

TEXTS = r"""
return function(node)  -- text nodes of a UI definition, in order, as 'text@colour', icons as '[atlas x,y]',
  local out = {}       -- the arrow drawn in boxes as '->@colour', and other drawn icons as '[name]'
  local function walk(n)
    if n.config and n.config.glyph then out[#out + 1] = '[' .. n.config.glyph .. ']'; return end
    if n.n == G.UIT.T and n.config.text ~= '' then  -- the title row's empty message slot shows nothing
      out[#out + 1] = n.config.text .. '@' .. tostring(n.config.colour)
    end
    if n.n == G.UIT.R and n.nodes[1] and n.nodes[1].n == G.UIT.B then
      out[#out + 1] = '->@' .. tostring(n.nodes[1].config.colour)
    end
    if n.n == G.UIT.O then
      local s = n.config.object
      out[#out + 1] = string.format('[%s %d,%d]', s.atlas, s.pos.x, s.pos.y)
    end
    for _, child in ipairs(n.nodes or {}) do walk(child) end
  end
  walk(node)
  return out
end
"""


def read_state(tmp_path) -> GameState:
    return GameState.from_json(json.loads((tmp_path / "calculatro_state.json").read_text(encoding="utf-8")))


def panel_texts(rt) -> list[str] | None:
    """Text of the live panel, or None if there is none."""
    box = rt.eval("BOXES[#BOXES]")
    if box is None or box.REMOVED:
        return None
    return list(rt.execute(TEXTS)(box.definition).values())


_SUIT_SYMBOLS = {suit[:-1]: symbol for suit, symbol in SUIT_SHORT.items()}


def label_for(query: CardQuery | str) -> str:
    """How the panel names a card query: "Queen: 2+", "♠: 0-1", "Q♠: 1+", "Face card: none"."""
    q = parse_query(query) if isinstance(query, str) else query
    name = re.sub(r"\b(" + "|".join(_SUIT_SYMBOLS) + r")\b", lambda m: _SUIT_SYMBOLS[m[1]], q.filter.name)
    if q.count == 0 and q.op in ("==", "<="):
        count = "none"
    elif q.op == "between":
        count = f"{q.count}-{q.high}"
    else:
        count = {">=": f"{q.count}+", "<=": f"0-{q.count}", "==": str(q.count)}[q.op]
    return f"{name}: {count}"


def track(lua, targets=(), queries=()):
    """Set what the run tracks, as the editor would."""
    rt, _ = lua
    rt.execute("G.GAME.calculatro = {hands = {}, cards = {}}")
    tracked = rt.eval("G.GAME.calculatro")
    for i, t in enumerate(targets, 1):
        tracked.hands[i] = get_target(t).name
    for i, q in enumerate(queries, 1):
        tracked.cards[i] = rt.table_from({"query": q, "label": label_for(q)})


def tracked(rt) -> tuple[list[str], list[str]]:
    t = rt.eval("G.GAME.calculatro")
    return list(t.hands.values()), [c.query for c in t.cards.values()]


def answer(lua, tmp_path, targets=("trips", "pair"), queries=("at most 1 nine",)):
    """Track these, then do what the app does: read the mod's state, write its odds."""
    _, mod = lua
    track(lua, targets, queries)
    mod.write_snapshot()
    reply(tmp_path)


def reply(tmp_path):
    """The app's odds for the mod's latest state."""
    state = read_state(tmp_path)
    _, payload = cli.calculate(state, *cli.tracked(state))
    assert overlay.write(tmp_path / "calculatro_odds.json", payload)


FIND = r"""
return function(node, id)  -- the node with this id
  local function walk(n)
    if n.config and n.config.id == id then return n end
    for _, c in ipairs(n.nodes or {}) do local found = walk(c); if found then return found end end
  end
  return walk(node)
end
"""


def find(rt, id: str):
    return rt.execute(FIND)(rt.eval("BOXES[#BOXES]").definition, id)


PATH = r"""
return function(node, id)  -- child indexes down to the node with this id
  local function walk(n, path)
    if n.config and n.config.id == id then return path end
    for i, c in ipairs(n.nodes or {}) do
      local found = walk(c, path .. '/' .. i); if found then return found end
    end
  end
  return walk(node, '')
end
"""


def path(rt, id: str) -> str | None:
    """Where a node of the live panel sits in its definition, as child indexes."""
    return rt.execute(PATH)(rt.eval("BOXES[#BOXES]").definition, id)


def click(lua, id: str, dt: float = 0.01) -> bool:
    """Click a button of the live panel, as the game would; False if it's inert."""
    rt, mod = lua
    node = find(rt, id)
    assert node is not None, id
    if not node.config.button:
        return False
    rt.eval("G.FUNCS")[node.config.button](node)
    mod.update(dt)
    return True


# --- Python side -----------------------------------------------------------

def test_percent_never_rounds_to_certainty():
    assert overlay.percent(Fraction(0)) == "0%"
    assert overlay.percent(Fraction(1)) == "100%"
    assert overlay.percent(Fraction(1, 3)) == "33.3%"
    assert overlay.percent(Fraction(1, 10**6)) == "<0.1%"
    assert overlay.percent(1 - Fraction(1, 10**6)) == ">99.9%"


def test_approximations_show_whole_percents_that_are_certain():
    assert overlay.approximate(Approx(Fraction(4, 5), Fraction(1))) == "80%+"
    assert overlay.approximate(Approx(Fraction(7999, 10000), Fraction(9, 10))) == "79%+"  # never rounded up
    assert overlay.approximate(Approx(Fraction(0), Fraction(1, 2))) == "<51%"  # 50% itself is possible
    assert overlay.approximate(Approx(Fraction(1, 1000), Fraction(1, 200))) == "<1%"
    assert overlay.approximate(Approx(Fraction(1, 1000), Fraction(1))) == "0%+"


def sample(estimate: Fraction, low: Fraction, high: Fraction, limit: str | None = None) -> Approx:
    return Approx(low, high, SAMPLE, limit, estimate)


def test_estimates_show_rounded_whole_percents():
    """An estimate isn't certain anyway, so it's rounded, not bounded, but never to 0% or 100%."""
    assert overlay.approximate(sample(Fraction(845, 1000), Fraction(8, 10), Fraction(9, 10))) == "85%"
    assert overlay.approximate(sample(Fraction(844, 1000), Fraction(8, 10), Fraction(9, 10))) == "84%"
    assert overlay.approximate(sample(Fraction(4, 1000), Fraction(0), Fraction(1, 100))) == "<1%"
    assert overlay.approximate(sample(Fraction(0), Fraction(0), Fraction(1, 100))) == "<1%"
    assert overlay.approximate(sample(Fraction(996, 1000), Fraction(99, 100), Fraction(1))) == ">99%"


def test_estimates_are_marked_and_never_compared():
    """An estimate's range is only likely, so discarding isn't called better or worse from it, even when the ranges
    are far apart."""
    keep, discard = sample(Fraction(1, 10), Fraction(1, 20), Fraction(3, 20)), sample(
        Fraction(9, 10), Fraction(17, 20), Fraction(19, 20), TIME)
    rows = [TargetOdds("Pair", keep, discard), TargetOdds("Flush", Fraction(0), keep)]
    payload = overlay.odds(GameState(id="s:1", in_run=True), Report(rows, [], [], 0, 1, partial=True))
    assert payload["targets"][0] == {
        "key": "Pair", "keep": "10%",
        "keep_mark": {"state": "refining", "how": "sample", "low": "5.0%", "high": "15.0%"},
        "discard": "90%",
        "discard_mark": {"state": "final", "how": "sample", "low": "85.0%", "high": "95.0%", "limit": "time"}}
    assert "trend" not in payload["targets"][1]


def test_approximations_are_marked_and_compared_only_when_certain():
    """An approximation's mark has its range, and whether the exact odds are still to come; discarding is better or
    worse than keeping only when their ranges don't overlap."""
    approx, final = Approx(Fraction(1, 5), Fraction(3, 10)), Approx(Fraction(1, 2), Fraction(3, 5), limit=MEMORY)
    rows = [TargetOdds("Any target", approx, final), TargetOdds("Pair", approx, Fraction(1, 4)),
            TargetOdds("Flush", Fraction(1, 10), approx), TargetOdds("Straight", Fraction(1, 4), Fraction(1, 4))]
    report = Report(rows, [CardOdds(parse_query("no face"), PENDING, approx)], [], 0, 1, partial=True)
    payload = overlay.odds(GameState(id="s:1", in_run=True), report)
    assert payload["partial"] is True
    assert payload["targets"][0] == {
        "key": "Any target", "keep": "20%+",
        "keep_mark": {"state": "refining", "how": "bounds", "low": "20.0%", "high": "30.0%"},
        "discard": "50%+",
        "discard_mark": {"state": "final", "how": "bounds", "low": "50.0%", "high": "60.0%", "limit": "memory"},
        "trend": 1}
    assert "trend" not in payload["targets"][1]  # 25% is in the range
    assert payload["targets"][2]["trend"] == 1 and payload["targets"][3]["trend"] == 0
    assert payload["cards"][0] == {"key": "no face", "discard": "20%+", "discard_mark": overlay.mark(approx)}


def test_card_labels_are_short_and_ascii_but_for_suits():
    labels = {q: label_for(q) for q in
              ["2+ queens", "at most 1 spade", "no face cards", "exactly 2 hearts", "QS", "at most 0 clubs",
               "foil diamonds", "10 of hearts", "no face spades"]}
    assert labels == {
        "2+ queens": "Queen: 2+",
        "at most 1 spade": "♠: 0-1",
        "no face cards": "Face card: none",
        "exactly 2 hearts": "♥: 2",
        "QS": "Q♠: 1+",
        "at most 0 clubs": "♣: none",
        "foil diamonds": "Foil ♦: 1+",
        "10 of hearts": "T♥: 1+",
        "no face spades": "Face ♠: none",
    }


BUILDER_QUERIES = r"""
return function(mod, counts, rank_sets, suit_sets)  -- every query the builder makes, with its label
  local out = {}
  local O = mod.OPTIONS
  for _, cc in ipairs(counts) do
    for _, ranks in ipairs(rank_sets) do for _, suits in ipairs(suit_sets) do
      for special = 1, #O.special do for _, negate in ipairs({false, true}) do
        if negate and special == 1 then goto skip end  -- "not" needs a special
        mod.reset_builder()
        local b = mod.builder
        b.cmp, b.count, b.high, b.special, b.negate = cc[1], cc[2], cc[3] or 2, special, negate
        b.ranks, b.suits = {}, {}
        for _, r in ipairs(ranks) do b.ranks[r] = true end
        for _, s in ipairs(suits) do b.suits[s] = true end
        local query, label = mod.builder_query()
        out[#out + 1] = {query or false, label or false, O.special[special].text, negate}
        ::skip::
      end end
    end end
  end
  return out
end
"""

PRESETS = [(), (11, 12, 13), (2, 3, 4, 5, 6, 7, 8, 9, 10, 14), (2, 4, 6, 8, 10), (3, 5, 7, 9, 14),
           (2, 3, 5, 8, 14), (2, 3, 4, 5), (4, 10)]
RANK_SETS = [*PRESETS, *((r,) for r in range(2, 15)), (12, 13), (2, 3, 4, 5, 9), (2, 14), (10, 11, 12, 13),
             tuple(range(2, 15))]
SUIT_SETS = [tuple(s for i, s in enumerate(SUITS) if mask >> i & 1) for mask in range(16)]


def builder_queries(lua, counts, rank_sets=RANK_SETS, suit_sets=SUIT_SETS):
    rt, mod = lua
    lists = [rt.table_from([rt.table_from(x) for x in xs]) for xs in (counts, rank_sets, suit_sets)]
    rows = rt.execute(BUILDER_QUERIES)(mod, *lists)
    return [tuple(r.values()) for r in rows.values()]


def _with_special(special: str) -> list[Card]:
    """Cards of every rank and suit that have a builder special ("Bonus", "Red Seal", "Any edition", ...)."""
    enhancements = {e: f"m_{e.lower()}" for e in ("Bonus", "Mult", "Wild", "Glass", "Steel", "Gold", "Lucky")}
    editions = {"Foil": "foil", "Holographic": "holo", "Polychrome": "polychrome", "Negative": "negative",
                "Any edition": "foil"}
    kwargs = ({"enhancement": enhancements.get(special, "m_bonus" if special == "Enhanced" else "c_base")}
              | ({"edition": editions[special]} if special in editions else {})
              | ({"seal": special.removesuffix(" Seal")} if special.endswith(" Seal") else {})
              | ({"seal": "Red"} if special == "Sealed" else {}))
    return [Card(rank=str(r), rank_id=r, suit=s, **kwargs) for r in range(2, 15) for s in SUITS]


def test_builder_queries_parse_as_labelled(lua):
    # Every preset and some other rank sets, every set of suits, every special
    # and its negation: the app parses the query, names it as the label does,
    # and matches the cards the chips chose. Under Stone, ranks and suits are
    # any, since Stone Cards have none.
    plain = [Card(rank=str(r), rank_id=r, suit=s, enhancement="c_base") for r in range(2, 15) for s in SUITS]
    stone = Card(enhancement="m_stone")
    per_set = 2 * len(lua[1].OPTIONS.special) - 1  # each special, and each but Any negated
    rows = iter(builder_queries(lua, [(1, 2)]))
    for chosen_ranks in RANK_SETS:
        for chosen_suits in SUIT_SETS:
            for query, label, special, negate in (next(rows) for _ in range(per_set)):
                ranks, suits = chosen_ranks, chosen_suits
                if special == "Stone" and not negate:
                    ranks, suits = (), ()
                if not ranks or len(ranks) == 13:
                    ranks = range(2, 15)
                any_rank_or_suit = len(ranks) == 13 and len(suits) in (0, 4)
                if any_rank_or_suit and special == "Any":
                    assert query is False and label is False  # every card matches: nothing to add
                    continue
                q = parse_query(query)
                assert label_for(q) == label, query
                assert all(ch.isascii() or ch in "♠♥♣♦" for ch in label)
                # Plain cards are ruled out by a special, and by none negated.
                expected = [c.rank_id in ranks and (not suits or c.suit in suits) and (special == "Any" or negate)
                            for c in plain]
                assert [q.filter.test(c, Rules()) for c in plain] == expected, query
                # A Stone Card only with any suit, any rank or Non-face (it isn't a face card without
                # Pareidolia), and a special it has, or one it hasn't negated.
                stone_rank = len(ranks) == 13 or tuple(ranks) == PRESETS[2]
                has_special = special in ("Any", "Stone", "Enhanced")
                assert q.filter.test(stone, Rules()) == (stone_rank and len(suits) in (0, 4)
                                                         and has_special != negate), query
                # And some card can match it.
                if special == "Any" or negate:
                    cards = plain
                else:
                    cards = [stone] if special == "Stone" else _with_special(special)
                assert any(q.filter.test(c, Rules()) for c in cards), query
    assert next(rows, None) is None
    # Every count, with one rank.
    counts = [(1, n) for n in range(1, 9)] + [(2, n) for n in range(9)] + [(3, n) for n in range(8)]
    counts += [(4, n, h) for n in range(1, 8) for h in range(n + 1, 9)]
    rows = builder_queries(lua, counts, [(2,)], [()])
    for (query, label, *_), count in zip((r for r in rows if r[2] == "Any"), counts, strict=True):
        q = parse_query(query)
        op = {1: ">=", 2: "==", 3: "<=", 4: "between"}[count[0]]
        assert (q.op, q.count, q.high, label) == (op, count[1], count[2] if op == "between" else None, label_for(q))


def test_builder_reads_back_its_queries(lua):
    # Each query the builder makes sets it to choices that make that query
    # again, so tracked queries can be edited; others can't.
    _, mod = lua
    counts = [(1, 2), (2, 0), (3, 7), (4, 1, 3)]
    queries = [q for q, *_ in builder_queries(lua, counts) if q]
    made = lua[0].execute("""return function(mod, queries)
        local out = {}
        for i, q in ipairs(queries) do
            local b = mod.builder_for(q)
            if b then mod.builder = b end
            out[i] = b and mod.builder_query() or false
        end
        mod.reset_builder()
        return out
    end""")(mod, lua[0].table_from(queries))
    assert list(made.values()) == queries
    for query in ("QS", "queen", "2+ queens", "at least 1 queens", "at least 1 not queen", "exactly 2 of spades",
                  "at least 1 queen of", "at most 1 nine"):
        assert mod.builder_for(query) is None, query


def test_builder_cycles_wrap_and_keep_counts_in_range(lua):
    _, mod = lua
    mod.cycle("preset", -1)
    assert mod.rank_choice() == "10 or 4"
    mod.cycle("preset", 1)
    assert mod.rank_choice() == "Any"
    mod.cycle("count", -1)  # at least 1 -> at least 8
    assert (mod.builder.cmp, mod.builder.count) == (1, 8)
    mod.cycle("cmp", -1)  # between: 1..7, and its top above that
    assert (mod.builder.cmp, mod.builder.count, mod.builder.high) == (4, 7, 8)
    mod.cycle("high", 1)  # wraps to just above the count
    assert mod.builder.high == 8
    mod.cycle("count", -1)
    mod.cycle("high", -1)
    assert (mod.builder.count, mod.builder.high) == (6, 7)
    mod.cycle("high", -1)  # not down to the count: wraps to 8
    assert mod.builder.high == 8
    mod.cycle("count", 1)
    mod.cycle("count", 1)  # wraps to 1: from 0 it would be "at most"
    assert (mod.builder.count, mod.builder.high) == (1, 8)
    mod.cycle("cmp", -1)  # at most: 0..7
    assert (mod.builder.cmp, mod.builder.count) == (3, 1)
    mod.cycle("cmp", -1)  # exactly 1
    mod.cycle("cmp", -1)  # at least: 1..8
    assert (mod.builder.cmp, mod.builder.count) == (1, 1)


def test_panel_draws_suits_as_icons(lua, tmp_path):
    rt, mod = lua
    mod.update(1.0)
    answer(lua, tmp_path, targets=(), queries=("QS", "at most 1 heart", "foil diamonds", "clubs", "queen"))
    mod.update(0.1)
    texts = panel_texts(rt)
    names = texts[texts.index("Discard@muted") + 1:]
    rows = [t for t in names if "%" not in t and t not in ("[x]", "[search]", "[edit]")]
    assert rows == ["Q@light", "[ui_1 3,1]", ": 1+@light",
                    "[ui_1 0,1]", ": 0-1@light",
                    "Foil @light", "[ui_1 1,1]", ": 1+@light",
                    "[ui_1 2,1]", ": 1+@light",
                    "Queen: 1+@light"]
    # Each icon on a white tile, so the black suits show on the dark inset.
    tiles = rt.execute("""return function(node)
      local out = {}
      local function walk(n)
        for _, c in ipairs(n.nodes or {}) do
          if c.n == G.UIT.O then out[#out + 1] = n.config.colour end
          walk(c)
        end
      end
      walk(node)
      return out
    end""")(rt.eval("BOXES[#BOXES]").definition)
    assert list(tiles.values()) == ["white"] * 4
    # The high-contrast icons with the colourblind option.
    rt.execute("G.SETTINGS.colourblind_option = true")
    assert "[ui_2 3,1]" in list(rt.execute(TEXTS)(mod.definition(mod.panel_model())).values())


def test_payloads(lua, tmp_path):
    _, mod = lua
    mod.write_snapshot()
    state = read_state(tmp_path)
    assert state.id == mod.state_id and state.selected == [2, 3]
    report = build_report(state, [get_target("trips"), get_target("pair")],
                          [parse_query("no face"), parse_query("at most 1 nine")])
    payload = overlay.odds(state, report)
    assert payload == {
        "schema": overlay.SCHEMA, "id": state.id,
        "targets": [
            {"key": "Any target", "keep": "100%", "discard": "100%", "trend": 0},
            {"key": "Three of a Kind", "keep": "20.0%", "discard": "100%", "trend": 1},
            {"key": "Pair", "keep": "100%", "discard": "100%", "trend": 0},
        ],
        # Kept, the queens are face cards and the nines two nines.
        "cards": [{"key": "no face", "discard": "0%", "trend": 0},
                  {"key": "at most 1 nine", "discard": "100%", "trend": 1}],
    }
    assert overlay.pending(state) == {"schema": overlay.SCHEMA, "id": state.id, "pending": True}

    out_of_discards = GameState(**{**state.__dict__, "discards_left": 0})
    payload = overlay.odds(out_of_discards, build_report(out_of_discards, [get_target("trips")]))
    assert "message" not in payload
    assert "discard" in payload["targets"][0]  # played as a discard, with another hand after it
    # On the last hand nothing is drawn after the selection, so the panel shows '-'.
    last_hand = GameState(**{**out_of_discards.__dict__, "hands_left": 1})
    payload = overlay.odds(last_hand, build_report(last_hand, [get_target("trips")], [parse_query("no face")]))
    assert payload["targets"] == [{"key": "Three of a Kind", "keep": "20.0%"}]
    assert payload["cards"] == [{"key": "no face"}]
    unselected = GameState(**{**state.__dict__, "hand": [c.__class__(**{**c.__dict__, "highlighted": False})
                                                          for c in state.hand]})
    payload = overlay.odds(unselected, build_report(unselected, [get_target("trips")]))
    assert "message" not in payload
    assert payload["targets"] == [{"key": "Three of a Kind", "keep": "20.0%"}]
    assert overlay.odds(state, None) == {"schema": overlay.SCHEMA, "id": state.id, "targets": [], "cards": []}


def test_watch_sends_pending_then_odds():
    state = GameState(in_run=True, id="s:1", hand_size=1, tracked_hands=["Pair"])
    sent: list[dict] = []
    cli.run_watch(FakeSource(state), lambda text: None, sent.append,
                  poll_interval=0.005, stop=lambda: len(sent) >= 2)
    assert sent == [overlay.pending(state), overlay.odds(state, None)]

    sent.clear()
    untracked = GameState(in_run=True, id="s:2", hand_size=1)
    cli.run_watch(FakeSource(untracked), lambda text: None, sent.append,
                  poll_interval=0.005, stop=lambda: bool(sent))
    assert sent == [overlay.odds(untracked, None)]


def test_watch_resends_odds_the_panel_missed(monkeypatch, capsys):
    """A payload that couldn't be written is sent again, without a new state to prompt it."""
    monkeypatch.setattr(cli, "RETRY", 0.01)
    state = GameState(in_run=True, id="s:1", hand_size=1, tracked_hands=["Pair"])
    attempts: list[dict] = []
    delivered: list[dict] = []

    def panel(payload):
        attempts.append(payload)
        if len(attempts) <= 3:  # the pending payload, the odds, and their first retry
            return False
        delivered.append(payload)
        return True

    end = time.monotonic() + 5
    cli.run_watch(FakeSource(state), lambda text: None, panel,
                  poll_interval=0.005, stop=lambda: bool(delivered) or time.monotonic() > end)
    assert attempts[0] == overlay.pending(state)
    assert delivered == [overlay.odds(state, None)]  # the odds replaced the pending payload
    err = capsys.readouterr().err
    assert err.count("Couldn't write the odds file") == 1 and "written again" in err


def test_watch_drops_missed_odds_for_a_newer_state(monkeypatch):
    monkeypatch.setattr(cli, "RETRY", 0.01)
    old = GameState(in_run=True, id="s:1", hand_size=1, tracked_hands=["Pair"])
    new = GameState(in_run=True, id="s:2", hand_size=1)
    source = FakeSource(old)
    delivered: list[dict] = []

    def panel(payload):
        if payload["id"] == "s:1":
            source.queue[:] = [new]  # the game moves on while the file is locked
            return False
        delivered.append(payload)
        return True

    end = time.monotonic() + 5
    cli.run_watch(source, lambda text: None, panel, poll_interval=0.005,
                  stop=lambda: bool(delivered) or time.monotonic() > end)
    assert delivered == [overlay.odds(new, None)]


def test_write_fails_softly(tmp_path, monkeypatch):
    path = tmp_path / "calculatro_odds.json"
    payload = {"schema": overlay.SCHEMA, "id": "s:1", "pending": True}
    (tmp_path / "calculatro_odds.json.tmp").mkdir()  # the temporary file can't be written
    assert overlay.write(path, payload) is False
    (tmp_path / "calculatro_odds.json.tmp").rmdir()

    monkeypatch.setattr(overlay.time, "sleep", lambda s: None)

    def locked(src, dst):
        raise PermissionError
    monkeypatch.setattr(overlay.os, "replace", locked)
    assert overlay.write(path, payload) is False

    def gone(src, dst):  # e.g. removed by something else
        raise FileNotFoundError
    monkeypatch.setattr(overlay.os, "replace", gone)
    assert overlay.write(path, payload) is False


# --- Lua side --------------------------------------------------------------

def test_decode_round_trip(lua):
    rt, _ = lua
    mod = rt.eval("require('calculatro.json')")
    lua_error = sys.modules[type(rt).__module__].LuaError  # each lupa runtime has its own
    text = ' {"s":"a\\"b\\\\c\\n\\t\\/\\u00e9\\u4e2d","n":-1.5e2,"i":42,"t":true,"f":false,\n' \
           '  "list":[1,"x",{"k":"v"}],"empty":[]} '
    decoded = mod.decode(text)
    assert json.loads(mod.encode(decoded)) == json.loads(text)  # "empty" stays an array
    assert decoded.s == 'a"b\\c\n\t/é中'
    for bad in ['{"a":1', '{"a" 1}', '[1,]', '"open', '{"a":nul}', '1 2']:
        with pytest.raises(lua_error):
            mod.decode(bad)


def test_snapshot_ids_change_with_the_state(lua, tmp_path):
    rt, mod = lua
    mod.write_snapshot()
    first = read_state(tmp_path).id
    mod.write_snapshot()
    assert read_state(tmp_path).id == first  # unchanged: not rewritten
    rt.execute("G.hand.cards[1].highlighted = true")
    mod.write_snapshot()
    second = read_state(tmp_path).id
    assert second != first and second == mod.state_id
    assert first.split(":")[0] == second.split(":")[0] == mod.session


def test_in_blind(lua):
    rt, mod = lua
    for state, shown in [("SELECTING_HAND", True), ("DRAW_TO_HAND", True), ("HAND_PLAYED", False),
                         ("SHOP", False), ("BLIND_SELECT", False), ("ROUND_EVAL", False), ("TAROT_PACK", False)]:
        rt.execute(f"G.STATE = G.STATES.{state}")
        assert mod.in_blind() is shown, state
    # Using a consumable: shown if it was used during the blind.
    rt.execute("G.STATE = G.STATES.PLAY_TAROT; G.TAROT_INTERRUPT = G.STATES.SELECTING_HAND")
    assert mod.in_blind()
    rt.execute("G.TAROT_INTERRUPT = G.STATES.SHOP")
    assert not mod.in_blind()
    rt.execute("G.STATE = G.STATES.SELECTING_HAND; G.OVERLAY_MENU = {}")  # run info, deck view, options
    assert not mod.in_blind()
    rt.execute("G.OVERLAY_MENU = nil; G.STAGE = G.STAGES.MAIN_MENU")
    assert not mod.in_blind()


def test_panel_shows_current_odds(lua, tmp_path):
    rt, mod = lua
    mod.update(1.0)
    assert panel_texts(rt) == ["CALCULATRO@light", "+@light", "[settings]", "Nothing tracked@muted"]
    answer(lua, tmp_path)
    mod.update(0.1)
    assert panel_texts(rt) == [
        "CALCULATRO@light", "+@light", "[settings]",
        "Hands@muted", "Keep@muted", "Discard@muted",
        "Any target@light", "100%@light", "->@muted", "100%@light", "[search]",  # can't be removed
        "Trips@light", "20.0%@light", "->@muted", "100%@green", "[search]", "[x]",
        "Pair@light", "100%@light", "->@muted", "100%@light", "[search]", "[x]",
        "In hand@muted", "Discard@muted",  # a column of their own
        "9: 0-1@light", "100%@green", "[search]", "[edit]", "[x]",
    ]
    box = rt.eval("BOXES[#BOXES]")
    assert rt.eval("BOXES[#BOXES].config.major == G.deck") and box.config.align == "tm"
    assert box.alignment.offset.x == pytest.approx((2.25 - 2 * 3.25) / 2)  # right edge on the deck's
    # Above the raised hand's cards, highlighted ones included.
    assert box.alignment.offset.y == pytest.approx(-(1.9 + 0.2 + 0.5 + 0.1))

    boxes = rt.eval("#BOXES")
    mod.update(0.1)
    assert rt.eval("#BOXES") == boxes  # unchanged: not rebuilt



def test_new_odds_update_the_panel_in_place(lua, tmp_path):
    """Rebuilding the panel takes the game tens of milliseconds, a hitch each time the odds change: when only the odds
    values or the title row's message change, the panel keeps its box and lays it out again."""
    rt, mod = lua
    mod.update(1.0)
    answer(lua, tmp_path, queries=())  # the two 9s are selected: Discard values
    mod.update(0.1)
    boxes = rt.eval("#BOXES")
    rt.execute("RECALCULATED = 0; BOXES[#BOXES].recalculate = function() RECALCULATED = RECALCULATED + 1 end")
    before = panel_texts(rt)

    rt.execute("G.hand.cards[1].highlighted = true")  # the Queen of Hearts too
    mod.update(0.2)  # new snapshot
    mod.update(0.4)  # no answer yet
    assert panel_texts(rt)[3] == "Calculating...@muted"
    answer(lua, tmp_path, queries=())
    mod.update(0.1)
    after = panel_texts(rt)
    assert "Calculating...@muted" not in after and after != before
    assert rt.eval("#BOXES") == boxes  # the same box throughout
    assert rt.eval("RECALCULATED") >= 1
    assert after == rebuilt_panel_texts(lua)


def test_updating_values_makes_no_sprites(lua, tmp_path):
    """The suit icons in card query labels are sprites, which the game keeps track of until they're removed: an update
    of the values in place must not make new ones it then throws away, or every change of odds would leak some."""
    rt, mod = lua
    rt.execute("SPRITES = 0; local make = Sprite; function Sprite(...) SPRITES = SPRITES + 1; return make(...) end")
    mod.update(1.0)
    answer(lua, tmp_path, queries=("QS", "at most 1 heart"))
    mod.update(0.1)
    boxes, sprites = rt.eval("#BOXES"), rt.eval("SPRITES")
    assert sprites >= 2
    before = panel_texts(rt)

    for highlighted in ("true", "false", "true"):  # the Queen of Hearts too, or not
        rt.execute(f"G.hand.cards[1].highlighted = {highlighted}")
        mod.update(0.2)  # new snapshot
        answer(lua, tmp_path, queries=("QS", "at most 1 heart"))
        mod.update(0.1)
    assert panel_texts(rt) != before
    assert rt.eval("#BOXES") == boxes  # the same box throughout
    assert rt.eval("SPRITES") == sprites


def rebuilt_panel_texts(lua) -> list[str]:
    """The panel's text when built afresh for the current odds."""
    rt, mod = lua
    mod.panel_layout = None  # forces a rebuild
    mod.panel_key = None
    mod.refresh_panel()
    return panel_texts(rt)


def test_panel_remembers_how_it_fit(lua, tmp_path):
    """Fitting columns under the consumables tries a box per row count; a layout that's been fitted is built once."""
    rt, mod = lua
    mod.update(1.0)
    targets = ("pair", "two pair", "trips", "straight", "flush", "full house", "quads",
               "straight flush", "five of a kind", "flush house", "flush five")
    rt.execute("G.hand.cards[3].highlighted = false; G.hand.cards[4].highlighted = false")
    answer(lua, tmp_path, targets=targets, queries=())
    mod.update(0.1)
    expected = columns(rt, rt.eval("BOXES[#BOXES]").definition)
    rt.execute("G.hand.cards[1].highlighted = true")  # Discard values and their arrows: another layout
    mod.update(0.2)
    answer(lua, tmp_path, targets=targets, queries=())
    mod.update(0.1)
    rt.execute("G.hand.cards[1].highlighted = false")  # back to the first one
    mod.update(0.2)
    answer(lua, tmp_path, targets=targets, queries=())
    before = rt.eval("#BOXES")
    mod.update(0.1)
    assert rt.eval("#BOXES") - before == 1
    assert columns(rt, rt.eval("BOXES[#BOXES]").definition) == expected


COLUMNS = r"""
return function(node)  -- names in each odds column (the column's first label), in order
  local columns = {}
  local function first_texts(n, out)
    if n.n == G.UIT.R then
      local first
      local function find(m) if first then return end
        if m.n == G.UIT.T then first = m.config.text end
        for _, c in ipairs(m.nodes or {}) do find(c) end
      end
      find(n)
      out[#out + 1] = first
      return
    end
    for _, c in ipairs(n.nodes or {}) do first_texts(c, out) end
  end
  local function walk(n)
    if n.config and n.config.id == 'calculatro_column' then
      local out = {}
      first_texts(n, out)
      columns[#columns + 1] = table.concat(out, '|')
    end
    for _, c in ipairs(n.nodes or {}) do walk(c) end
  end
  walk(node)
  return columns
end
"""


def columns(rt, definition) -> list[list[str]]:
    return [c.split("|") for c in rt.execute(COLUMNS)(definition).values()]


def test_panel_spills_into_columns(lua, tmp_path):
    rt, mod = lua
    mod.update(1.0)
    targets = ("pair", "two pair", "trips", "straight", "flush", "full house", "quads",
               "straight flush", "five of a kind", "flush house", "flush five")
    answer(lua, tmp_path, targets=targets, queries=("queen",))
    before = rt.eval("#BOXES")
    mod.update(0.1)
    box = rt.eval("BOXES[#BOXES]")
    # 12 hand rows (11 targets and "Any target") and a query: columns of 6
    # (2.6 tall) are the tallest to fit in the 2.85 under the consumables, so
    # each is filled first; the query starts a column of its own.
    assert columns(rt, box.definition) == [
        ["Hands", "Any target", "Pair", "Two Pair", "Trips", "Straight", "Flush"],
        ["Hands", "Full House", "Quads", "Str. Flush", "5 of a Kind", "Flush House", "Flush Five"],
        ["In hand", "Queen: 1+"],
    ]
    assert box.T.h <= mod.max_panel_height()
    assert box.alignment.offset.x == pytest.approx((2.25 - 3 * 3.25) / 2)  # grows leftwards
    assert rt.eval("#BOXES") - before == 7  # tried 12 rows per column, then 11, ... 6


def test_panel_fills_a_column_before_starting_another(lua, tmp_path):
    rt, mod = lua
    mod.update(1.0)
    answer(lua, tmp_path, targets=("pair", "two pair", "trips", "straight", "flush", "full house"), queries=())
    mod.update(0.1)
    # 7 rows: 6 in the first column, not 4 and 3.
    assert [len(c) - 1 for c in columns(rt, rt.eval("BOXES[#BOXES]").definition)] == [6, 1]


@pytest.mark.parametrize("targets, queries, layout", [
    (("pair", "two pair", "trips", "straight", "flush"), (), [7]),  # 6 rows: one full column
    (("pair", "two pair", "trips", "straight", "flush", "full house", "quads",
      "straight flush", "five of a kind", "flush house", "flush five"), ("queen",), [7, 7, 2]),  # 12 rows and a query
])
def test_message_does_not_change_the_panel(lua, tmp_path, targets, queries, layout):
    rt, mod = lua
    mod.update(1.0)
    answer(lua, tmp_path, targets=targets, queries=queries)
    mod.update(0.1)
    assert panel_texts(rt)[3] == "Hands@muted"  # no message
    box = rt.eval("BOXES[#BOXES]")
    assert [len(c) for c in columns(rt, box.definition)] == layout

    rt.execute("G.hand.cards[1].highlighted = true")
    mod.update(0.2)  # new snapshot
    mod.update(0.4)  # no answer yet: "Calculating..." beside the title
    assert panel_texts(rt)[3] == "Calculating...@muted"
    calculating = rt.eval("BOXES[#BOXES]")
    assert [len(c) for c in columns(rt, calculating.definition)] == layout
    assert (calculating.T.w, calculating.T.h) == (box.T.w, box.T.h)


def test_panel_height_measured_from_the_count_label(lua):
    rt, mod = lua
    lift = 1.9 + 0.2 + 0.5 + 0.1
    # Until the consumables' label exists, it's estimated to hang 0.6 below them.
    assert mod.max_panel_height() == pytest.approx(8.9 - lift - (2.75 + 0.6))
    rt.execute("G.consumeables.children = {area_uibox = {T = {y = -0.15, h = 2.9}}}")
    assert mod.max_panel_height() == pytest.approx(8.9 - lift - (2.75 - 0.03))
    rt.execute("G.consumeables.children.area_uibox.REMOVED = true")
    assert mod.max_panel_height() == pytest.approx(8.9 - lift - (2.75 + 0.6))


def test_odds_inset_stays_dark_in_boss_blinds(lua, tmp_path):
    rt, mod = lua
    mod.update(1.0)
    answer(lua, tmp_path)
    mod.update(0.1)
    find = rt.execute(r"""
      return function(n)
        local function walk(m)
          if m.config and m.config.id == 'calculatro_inset' then return m end
          for _, c in ipairs(m.nodes or {}) do local f = walk(c); if f then return f end end
        end
        return walk(n)
      end""")
    colour = find(rt.eval("BOXES[#BOXES]").definition).config.colour
    # G.C.BLACK darkened 5%, as BOSS_MAIN is outside boss blinds, never BOSS_MAIN itself.
    assert list(colour.values()) == pytest.approx([0.19, 0.38, 0.57, 1])


def test_columns_needed():
    assert overlay.columns_needed(0, 0) == 0
    assert overlay.columns_needed(1, 0) == 1
    assert overlay.columns_needed(5, 6) == 2  # 6 hand rows with "Any target"
    assert overlay.columns_needed(6, 1) == 3  # 7 hand rows
    assert overlay.columns_needed(11, 6) == 3
    assert overlay.columns_needed(5, 12) == 3
    assert overlay.columns_needed(0, 18) == 3
    assert overlay.columns_needed(6, 12) == 4
    assert overlay.columns_needed(2, 13) == 4


def test_mod_and_app_agree_on_columns_needed(lua):
    _, mod = lua
    for hands in range(13):
        for cards in range(20):
            assert mod.columns_needed(hands, cards) == overlay.columns_needed(hands, cards), (hands, cards)
    assert mod.max_columns == overlay.MAX_COLUMNS


def test_panel_never_wider_than_three_columns(lua, tmp_path):
    rt, mod = lua
    rt.execute("G.consumeables.T.h = 5")  # leaves 0.6: nothing fits
    mod.update(1.0)
    answer(lua, tmp_path, targets=("pair", "two pair", "trips", "straight", "flush"), queries=("queen",))
    mod.update(0.1)
    assert len(columns(rt, rt.eval("BOXES[#BOXES]").definition)) == 3


def test_hands_and_cards_in_columns_of_their_own(lua, tmp_path):
    rt, mod = lua
    mod.update(1.0)
    answer(lua, tmp_path, targets=("pair", "two pair", "trips", "straight"), queries=("queen", "no face"))
    mod.update(0.1)
    # 7 rows would fit one column of 7, but the cards start their own.
    assert columns(rt, rt.eval("BOXES[#BOXES]").definition) == [
        ["Hands", "Any target", "Pair", "Two Pair", "Trips", "Straight"],
        ["In hand", "Queen: 1+", "Face card: none"],
    ]
    # Each section fills its own columns: the hands' last isn't topped up with cards.
    assert columns(rt, mod.definition(mod.panel_model(), 3)) == [
        ["Hands", "Any target", "Pair", "Two Pair"],
        ["Hands", "Trips", "Straight"],
        ["In hand", "Queen: 1+", "Face card: none"],
    ]


def test_panel_without_selection(lua, tmp_path):
    rt, mod = lua
    rt.execute("for _, c in ipairs(G.hand.cards) do c.highlighted = nil end")
    mod.update(1.0)
    answer(lua, tmp_path, targets=("trips",))
    mod.update(0.1)
    assert panel_texts(rt) == [
        "CALCULATRO@light", "+@light", "[settings]", "Hands@muted", "Keep@muted", "Discard@muted",
        "Trips@light", "20.0%@light", "-@light", "[search]", "[x]",
        "In hand@muted", "Discard@muted", "9: 0-1@light", "-@light", "[search]", "[edit]", "[x]",
    ]


def test_panel_with_only_card_queries(lua, tmp_path):
    rt, mod = lua
    rt.execute("for _, c in ipairs(G.hand.cards) do c.highlighted = nil end")
    mod.update(1.0)
    answer(lua, tmp_path, targets=(), queries=("no face",))
    mod.update(0.1)
    # Listed without a selection too, so the panel doesn't vanish between discards.
    assert panel_texts(rt) == [
        "CALCULATRO@light", "+@light", "[settings]", "In hand@muted", "Discard@muted",
        "Face card: none@light", "-@light", "[search]", "[edit]", "[x]",
    ]
    rt.execute("G.hand.cards[1].highlighted = true")
    mod.update(0.2)
    answer(lua, tmp_path, targets=(), queries=("no face",))
    mod.update(0.1)
    assert panel_texts(rt)[-5:] == ["Face card: none@light", "0%@light", "[search]", "[edit]", "[x]"]


CELL_WIDTHS = r"""
return function(node)  -- maxw of the cell around each label, keyed by its text
  local out = {}
  local function walk(n, w)
    if n.n == G.UIT.T then out[n.config.text] = w end
    for _, c in ipairs(n.nodes or {}) do walk(c, n.config and n.config.maxw or w) end
  end
  walk(node)
  return out
end
"""


@pytest.mark.parametrize("targets", [(), ("trips",)])
def test_card_names_use_the_keep_columns_space(lua, tmp_path, targets):
    # Text wider than its cell is shrunk, and modifier labels are too long for
    # a hand name's cell; card rows have no Keep value, so they take its space,
    # but for their pencil's.
    rt, mod = lua
    mod.update(1.0)
    answer(lua, tmp_path, targets=targets, queries=("lucky", "sealed"))
    mod.update(0.1)
    widths = rt.execute(CELL_WIDTHS)(rt.eval("BOXES[#BOXES]").definition)
    assert widths["Lucky card: 1+"] == widths["Sealed card: 1+"] == pytest.approx(1.4 + 0.75 + 0.25 - 0.3)
    if targets:
        assert widths["Trips"] == pytest.approx(1.4)


ARROW_SIZE = r"""
return function(node)  -- the arrow's width, height and left margin, and the widths of its cell and the next
  local function size(arrow)
    local w, h = 0, 0
    for _, b in ipairs(arrow.nodes) do w, h = w + b.config.w, math.max(h, b.config.h) end
    return w, h
  end
  local found
  local function walk(n)
    for i, c in ipairs(n.nodes or {}) do
      local inner = c.nodes and c.nodes[1]
      local boxes = inner and inner.nodes and inner.nodes[2]
      local icon = inner and inner.config and inner.config.glyph  -- drawn icons' rows are boxes too
      if not found and not icon and boxes and boxes.nodes and boxes.nodes[1] and boxes.nodes[1].n == G.UIT.B then
        local w, h = size(boxes)
        found = {w, h, inner.nodes[1].config.minw, c.config.minw, n.nodes[i + 1].config.minw}
      end
      walk(c)
    end
  end
  walk(node)
  return found
end
"""


def test_arrow_fits_its_row(lua, tmp_path):
    # Drawn in font pixels (0.02 units at the panel's text scale), and shorter
    # than the digits (11 pixels), so the row doesn't grow. It takes the gap
    # and the left of the discard cell, which keeps room for ">99.9%" (37
    # pixels), and sits midway between the Keep value (whose '%' ends a pixel
    # short of its cell) and a usual discard value like "88.3%" (31 pixels).
    rt, mod = lua
    mod.update(1.0)
    answer(lua, tmp_path)
    mod.update(0.1)
    w, h, left, cell_w, value_w = rt.execute(ARROW_SIZE)(rt.eval("BOXES[#BOXES]").definition).values()
    px = 0.02
    assert w == pytest.approx(9 * px) and h == pytest.approx(8 * px) and h < 11 * px
    assert cell_w + value_w == pytest.approx(0.25 + 0.95) and value_w >= 37 * px
    assert left + w < cell_w
    assert left + px == pytest.approx((cell_w - left - w) + (value_w - 31 * px))


def test_panel_greys_out_old_odds(lua, tmp_path):
    rt, mod = lua
    mod.update(1.0)
    answer(lua, tmp_path, queries=())
    mod.update(0.1)
    rt.execute("G.hand.cards[1].highlighted = true")
    mod.update(0.2)  # new snapshot; the app acknowledges it
    overlay.write(tmp_path / "calculatro_odds.json", overlay.pending(read_state(tmp_path)))
    mod.update(0.1)
    texts = panel_texts(rt)
    assert "20.0%@light" in texts and "Discard@muted" in texts  # old odds, not yet greyed
    mod.update(0.3)
    texts = panel_texts(rt)
    assert "20.0%@stale" in texts and texts[3] == "Calculating...@muted"  # beside the title
    answer(lua, tmp_path, queries=())
    mod.update(0.1)
    texts = panel_texts(rt)
    assert not any(t.endswith("@stale") for t in texts) and "Calculating...@muted" not in texts


def app_payload(tmp_path) -> dict:
    """The app's odds for the mod's latest state, for a test to change before writing."""
    state = read_state(tmp_path)
    return cli.calculate(state, *cli.tracked(state))[1]


def write_payload(tmp_path, payload: dict) -> None:
    assert overlay.write(tmp_path / "calculatro_odds.json", payload)


def rows_of(payload: dict) -> dict[str, dict]:
    return {r["key"]: r for r in payload["targets"] + payload["cards"]}


def test_partial_odds_grey_only_the_values_still_to_come(lua, tmp_path):
    """A partial answer's values are shown at once; those it doesn't have yet keep the ones before, greyed out after a
    while, each on its own, and the title row says it's calculating until the rest arrive."""
    rt, mod = lua
    mod.update(1.0)
    answer(lua, tmp_path, queries=("no face",))
    mod.update(0.1)
    assert "20.0%@light" in panel_texts(rt) and "100%@green" in panel_texts(rt)  # Trips: Keep, Discard
    boxes = rt.eval("#BOXES")
    rt.execute("G.hand.cards[1].highlighted = true")  # the Queen of Hearts too
    mod.update(0.2)  # new snapshot
    final = app_payload(tmp_path)
    partial = json.loads(json.dumps(final))
    partial["partial"] = True
    trips, card = rows_of(partial)["Three of a Kind"], rows_of(partial)["no face"]
    for row in (trips, card):
        del row["discard"], row["trend"]
        row["discard_mark"] = {"state": "pending"}
    write_payload(tmp_path, partial)
    mod.update(0.1)
    texts = panel_texts(rt)
    assert "Calculating...@muted" not in texts and not any(t.endswith("@stale") for t in texts)
    def trips_row(texts):
        return texts[texts.index("Trips@light"):][:4]
    assert trips_row(texts) == ["Trips@light", "20.0%@light", "->@muted", "100%@green"]  # as before
    mod.update(0.3)
    texts = panel_texts(rt)
    assert texts[3] == "Calculating...@muted"
    assert trips_row(texts) == ["Trips@light", "20.0%@light", "->@muted", "100%@stale"]
    assert sum(t.endswith("@stale") for t in texts) == 2  # Trips' and the card query's Discard
    write_payload(tmp_path, final)
    mod.update(0.1)
    texts = panel_texts(rt)
    assert not any(t.endswith("@stale") for t in texts) and "Calculating...@muted" not in texts
    assert rt.eval("#BOXES") == boxes  # updated in place throughout


def test_a_value_still_to_come_without_one_before_keeps_its_arrow(lua, tmp_path):
    """A Discard value to come has its arrow already, so the panel isn't rebuilt when it arrives."""
    rt, mod = lua
    mod.update(1.0)
    rt.execute("for _, c in ipairs(G.hand.cards) do c.highlighted = false end")
    answer(lua, tmp_path, targets=("trips",), queries=())
    mod.update(0.1)
    assert "->@muted" not in panel_texts(rt)
    rt.execute("G.hand.cards[1].highlighted = true")
    mod.update(0.2)
    final = app_payload(tmp_path)
    partial = {**final, "partial": True,
               "targets": [{"key": "Three of a Kind", "keep": "20.0%", "discard_mark": {"state": "pending"}}]}
    write_payload(tmp_path, partial)
    mod.update(0.1)
    texts = panel_texts(rt)
    assert texts[texts.index("Trips@light"):][:4] == ["Trips@light", "20.0%@light", "->@muted", "-@light"]
    boxes = rt.eval("#BOXES")
    write_payload(tmp_path, final)
    mod.update(0.1)
    assert rt.eval("#BOXES") == boxes


TOO_SLOW_POPUP = r"""
return function(mod, popup)  -- the texts of the popup shown while hovering a cell with this one
  UIElement = {hover = function(self) end}
  local e = {config = {calculatro_popup = popup}}
  mod.hover_popups(e)
  e:hover()
  return e.config.h_popup
end
"""


def test_values_given_up_on_show_a_question_mark_with_a_popup(lua, tmp_path):
    rt, mod = lua
    mod.update(1.0)
    answer(lua, tmp_path, queries=("no face",))
    mod.update(0.1)
    boxes = rt.eval("#BOXES")
    payload = app_payload(tmp_path)
    trips, card = rows_of(payload)["Three of a Kind"], rows_of(payload)["no face"]
    trips.update(keep="?", keep_mark={"state": "final", "limit": "time"})
    del trips["trend"]
    card.update(discard="?", discard_mark={"state": "final", "limit": "memory"})
    del card["trend"]
    write_payload(tmp_path, payload)
    mod.update(0.1)
    texts = panel_texts(rt)
    trips_at = texts.index("Trips@light")
    # Neither colour: a value given up on can't be compared.
    assert texts[trips_at:trips_at + 4] == ["Trips@light", "?@light", "->@muted", "100%@light"]
    assert "?@light" in texts[texts.index("In hand@muted"):]
    assert rt.eval("#BOXES") > boxes  # the popups are on the cells: rebuilt
    keep = find(rt, "calculatro_keep:Three of a Kind")
    assert keep.config.collideable and keep.config.calculatro_popup.name == "Too slow"
    popup = rt.execute(TOO_SLOW_POPUP)(mod, keep.config.calculatro_popup)
    assert [t.split("@")[0] for t in rt.execute(TEXTS)(popup).values()] == [
        "Too slow", "Working these odds out exactly", "would take too long, so the", "app has given up on them."]
    popup = rt.execute(TOO_SLOW_POPUP)(mod, find(rt, "calculatro_card:no face").config.calculatro_popup)
    assert [t.split("@")[0] for t in rt.execute(TEXTS)(popup).values()][1:3] == [
        "Working these odds out exactly", "would use too much memory, so"]
    assert find(rt, "calculatro_discard:Three of a Kind") is None  # exact values have none
    assert all(len(t.split("@")[0]) <= 31 for t in rt.execute(TEXTS)(popup).values())


def popup_lines(rt, mod, id: str) -> list[str]:
    popup = rt.execute(TOO_SLOW_POPUP)(mod, find(rt, id).config.calculatro_popup)
    return [t.split("@")[0] for t in rt.execute(TEXTS)(popup).values()]


def test_approximations_have_an_asterisk_and_a_popup(lua, tmp_path):
    """An approximation shows with an asterisk, in the neutral colour unless the comparison is certain, and a popup with
    its range. As it narrows, it's updated in place, popup and all; once the exact odds arrive, its popup goes."""
    rt, mod = lua
    mod.update(1.0)
    answer(lua, tmp_path, queries=())
    mod.update(0.1)
    exact = app_payload(tmp_path)
    payload = json.loads(json.dumps(exact))
    any_target = rows_of(payload)["Any target"]
    any_target.update(keep="20%+", keep_mark={"state": "refining", "how": "bounds", "low": "20.0%", "high": "100%"},
                      discard="<51%", discard_mark={"state": "refining", "how": "bounds", "low": "0%", "high": "50.0%"})
    any_target.pop("trend", None)
    payload["partial"] = True
    write_payload(tmp_path, payload)
    mod.update(0.1)
    texts = panel_texts(rt)
    at = texts.index("Any target@light")
    assert texts[at:at + 4] == ["Any target@light", "20%+*@light", "->@muted", "<51%*@light"]
    assert popup_lines(rt, mod, "calculatro_keep:Any target") == [
        "Approximate", "Between 20.0% and 100%.", "From each hand's own odds.", "Still working out the exact",
        "odds."]
    boxes = rt.eval("#BOXES")
    any_target["keep_mark"] = {**any_target["keep_mark"], "high": "40.0%"}
    any_target["discard_mark"] = {"state": "final", "how": "bounds", "low": "0%", "high": "50.0%", "limit": "time"}
    write_payload(tmp_path, payload)
    mod.update(0.1)
    assert rt.eval("#BOXES") == boxes  # in place
    assert popup_lines(rt, mod, "calculatro_keep:Any target")[1] == "Between 20.0% and 40.0%."
    lines = popup_lines(rt, mod, "calculatro_discard:Any target")
    assert lines[3:] == ["Working them out exactly would", "take too long, so the app has", "given up on them."]
    assert all(len(line) <= 31 for line in lines)
    write_payload(tmp_path, exact)
    mod.update(0.1)
    assert rt.eval("#BOXES") > boxes  # the popups have gone: rebuilt
    assert find(rt, "calculatro_keep:Any target") is None
    assert not any("*" in t for t in panel_texts(rt))


def test_estimates_say_their_range_is_only_likely(lua, tmp_path):
    rt, mod = lua
    mod.update(1.0)
    answer(lua, tmp_path, queries=())
    mod.update(0.1)
    payload = json.loads(json.dumps(app_payload(tmp_path)))
    pair = rows_of(payload)["Pair"]
    pair.update(keep="84%", keep_mark={"state": "refining", "how": "sample", "low": "83.4%", "high": "84.8%"})
    payload["partial"] = True
    write_payload(tmp_path, payload)
    mod.update(0.1)
    texts = panel_texts(rt)
    assert texts[texts.index("Pair@light") + 1] == "84%*@light"
    lines = popup_lines(rt, mod, "calculatro_keep:Pair")
    assert lines == ["Approximate", "Likely 83.4% to 84.8%.", "Estimated from a sample of the",
                     "deals the hand's sorts allow.", "Still working out the exact", "odds."]
    assert all(len(line) <= 31 for line in lines)


def test_search_given_up_says_so_on_the_title_row(lua, tmp_path):
    rt, mod = lua
    search_ready(lua, tmp_path)
    assert click(lua, "calculatro_search_Pair")
    payload = app_payload(tmp_path)
    payload["search"] = overlay.search("Pair", 1, TooSlow(TIME))
    write_payload(tmp_path, payload)
    mod.update(0.1)
    assert message(rt) == "Too slow" and selected(rt) == [2, 3]  # nothing selected for it
    popup = rt.execute(TOO_SLOW_POPUP)(mod, find(rt, "calculatro_message").config.calculatro_popup)
    assert [t.split("@")[0] for t in rt.execute(TEXTS)(popup).values()] == [
        "Too slow", "Comparing every discard would", "take too long, so the app has", "given up on this search."]
    # Until the selection changes.
    rt.execute("G.hand.cards[1].highlighted = true")
    mod.update(0.1)
    assert message(rt) is None and find(rt, "calculatro_message") is None
    # The button asks again (the app answers at once).
    assert click(lua, "calculatro_search_Pair")
    assert read_state(tmp_path).search == ("Pair", 2) and message(rt) == "Searching..."


def test_new_round_drops_the_last_rounds_odds(lua, tmp_path):
    """While a blind's first hand is dealt the app sends no odds: the panel shows '-', not the last round's."""
    rt, mod = lua
    rt.execute("G.GAME.round = 1")
    mod.update(1.0)
    answer(lua, tmp_path, queries=())
    late = (tmp_path / "calculatro_odds.json").read_text(encoding="utf-8")
    mod.update(0.1)
    assert "20.0%@light" in panel_texts(rt)
    rt.execute("G.GAME.round = 2; G.hand.cards[1].highlighted = true")
    mod.update(0.2)  # the next blind's snapshot; the app acknowledges it
    overlay.write(tmp_path / "calculatro_odds.json", overlay.pending(read_state(tmp_path)))
    mod.update(0.1)
    texts = panel_texts(rt)
    assert not any("%" in t for t in texts) and texts.count("-@light") == 6
    (tmp_path / "calculatro_odds.json").write_text(late, encoding="utf-8")  # the last round's, arriving late
    mod.update(0.3)
    texts = panel_texts(rt)
    assert not any("%" in t for t in texts) and texts[3] == "Calculating...@muted"
    answer(lua, tmp_path, queries=())  # once the hand is dealt
    mod.update(0.1)
    texts = panel_texts(rt)
    assert any(t.endswith("%@light") for t in texts) and "Calculating...@muted" not in texts


STRUCK = r"""
return function(node)  -- text struck through (hands the boss won't let score), in order
  local out = {}
  local function walk(n)
    if n.n == G.UIT.T and n.config.calculatro_strike then out[#out + 1] = n.config.text end
    for _, child in ipairs(n.nodes or {}) do walk(child) end
  end
  walk(node)
  return out
end
"""


def struck(rt) -> list[str]:
    return list(rt.execute(STRUCK)(rt.eval("BOXES[#BOXES]").definition).values())


def hand_rows(rt) -> list[str]:
    """The target rows' names and values, from the live panel."""
    texts = panel_texts(rt)
    return texts[texts.index("Discard@muted") + 1:]


@pytest.mark.parametrize("blind, banned", [
    ("{name = 'Big Blind'}", []),
    # The Eye: hand types already played.
    ("{name = 'The Eye', hands = {['Pair'] = true, ['Straight Flush'] = true, ['Flush'] = false}}",
     ["Pair", "Str. Flush"]),
    ("{name = 'The Eye', hands = {['Pair'] = true}, disabled = true}", []),
    # The Mouth: every hand type but the first played, nothing before then.
    ("{name = 'The Mouth', only_hand = false}", []),
    ("{name = 'The Mouth', only_hand = 'Straight Flush'}", ["Trips", "Pair"]),
    ("{name = 'The Mouth', only_hand = 'Flush'}", ["Any target", "Trips", "Pair", "Str. Flush"]),
])
def test_panel_strikes_out_hands_the_boss_bans(lua, tmp_path, blind, banned):
    rt, mod = lua
    rt.execute(f"G.GAME.blind = {blind}")
    answer(lua, tmp_path, targets=("trips", "pair", "straight flush"), queries=())
    mod.update(0.1)
    assert struck(rt) == banned
    rows = hand_rows(rt)
    names = ["Any target", "Trips", "Pair", "Str. Flush"]
    assert [r for r in rows if r.split("@")[0] in names] == [
        f"{name}@{'muted' if name in banned else 'light'}" for name in names]
    # The odds are still shown, as usual.
    assert "20.0%@light" in rows and not any(r.endswith("@stale") or r.startswith("-@") for r in rows)


def test_panel_strikes_out_hands_once_played(lua, tmp_path):
    rt, mod = lua
    rt.execute("G.GAME.blind = {name = 'The Eye', hands = {['Pair'] = false}}")
    answer(lua, tmp_path, targets=("pair",), queries=())
    mod.update(0.1)
    assert struck(rt) == []
    rt.execute("G.GAME.blind.hands['Pair'] = true")
    mod.update(0.1)
    assert struck(rt) == ["Pair"]


def test_strike_runs_through_the_lowercase_letters(lua):
    rt, mod = lua
    drawn = rt.execute(r"""
      local Calculatro = ...
      local drawn = {}
      prep_draw = function() end
      love = {graphics = {
        setColor = function(c) drawn[#drawn + 1] = {colour = c} end,
        rectangle = function(_, x, y, w, h) local d = drawn[#drawn]; d.x, d.y, d.w, d.h = x, y, w, h end,
        pop = function() end,
      }}
      G.TILESIZE, G.SETTINGS.GRAPHICS = 20, {shadows = 'On'}
      local font = {FONTSCALE = 0.1, TEXT_OFFSET = {x = 10, y = -20}, FONT = {getHeight = function() return 200 end}}
      local e = {UIT = G.UIT.T, VT = {w = 1.2}, states = {visible = true}, shadow_parrallax = {x = -2, y = -4},
                 config = {calculatro_strike = true, scale = 0.32, colour = {1, 1, 1, 1}, text_drawable = {},
                           lang = {font = font}},
                 draw_self = function() drawn[#drawn + 1] = 'text' end}
      Calculatro.strike_through(e)
      e:draw_self()
      return drawn
    """, mod)
    shadow, text, line = drawn[1], drawn[2], drawn[3]
    assert text == "text"  # between the line's shadow and the line
    px = 0.02  # a font pixel at the panel's text scale
    assert (line.x, line.w) == (0, 1.2) and line.h == pytest.approx(px)
    # m6x11's baseline is 12 of its 16 pixels down the em, drawn 1.6 pixels
    # (TEXT_OFFSET.y) above the box; its lowercase letters are 6 pixels tall.
    baseline = (12 - 1.6) * px
    assert line.y == pytest.approx(baseline - 4 * px)  # their middle pixel row (of 0-5 from the top, the 2nd)
    assert shadow.colour[4] == pytest.approx(0.3) and shadow.x > 0 and shadow.y > line.y


COUNT_STARTS = """
STARTS = 0
return function() STARTS = STARTS + 1; return true end
"""


def test_panel_without_the_app(lua, tmp_path):
    rt, mod = lua
    mod.launch = rt.execute(COUNT_STARTS)
    (tmp_path / "calculatro_odds.json").write_text(json.dumps(
        {"schema": overlay.SCHEMA, "id": "an-earlier-session:4", "targets": [{"key": "Pair", "keep": "1%"}]}))
    track(lua, targets=("pair",))
    mod.update(0.5)
    # What's tracked shows at once, without odds until the app answers.
    rows = ["Hands@muted", "Keep@muted", "Discard@muted", "Pair@light", "-@light", "-@light", "[search]", "[x]"]
    assert panel_texts(rt) == ["CALCULATRO@light", "+@light", "[settings]", *rows]
    mod.update(0.5)
    stale = [r.replace("-@light", "-@stale") for r in rows]
    assert panel_texts(rt) == ["CALCULATRO@light", "+@light", "[settings]", "Calculating...@muted", *stale]
    mod.update(2.0)
    assert panel_texts(rt) == ["CALCULATRO@light", "+@light", "[settings]", "Waiting for app@muted", *stale]


def title_message(rt) -> str:
    return panel_texts(rt)[3]


def test_silent_app_is_started_again_then_given_up_on(lua, tmp_path):
    rt, mod = lua
    mod.launch = rt.execute(COUNT_STARTS)
    track(lua, targets=("pair",))
    mod.update(0.5)
    assert rt.eval("STARTS") == 1
    mod.update(4.0)
    assert rt.eval("STARTS") == 1
    assert title_message(rt) == "Waiting for app@muted"
    mod.update(0.5)  # 5 seconds of silence
    assert rt.eval("STARTS") == 2
    assert title_message(rt) == "Waiting for app@muted"
    mod.update(15.0)
    assert rt.eval("STARTS") == 3
    assert title_message(rt) == "App didn't start@muted"
    log = (tmp_path / "calculatro.log").read_text(encoding="utf-8")
    assert "not answering after 3 starts" in log
    mod.update(45.0)
    assert rt.eval("STARTS") == 4
    mod.update(1000.0)
    assert rt.eval("STARTS") == 4  # no more: each one stalls a frame
    assert (tmp_path / "calculatro.log").read_text(encoding="utf-8") == log  # once


def test_answer_clears_the_failure_and_starts_are_counted_afresh(lua, tmp_path):
    rt, mod = lua
    mod.launch = rt.execute(COUNT_STARTS)
    track(lua, targets=("pair",))
    for wait in (0.5, 5.0, 15.0, 45.0):
        mod.update(wait)
    assert rt.eval("STARTS") == 4
    reply(tmp_path)  # started by hand, say
    mod.update(0.1)
    assert "App didn't start@muted" not in panel_texts(rt)
    # It stops again: the next snapshot goes unanswered.
    track(lua, targets=("pair", "trips"))
    mod.update(0.2)
    assert rt.eval("STARTS") == 4
    mod.update(2.0)
    assert title_message(rt) == "Waiting for app@muted"
    mod.update(3.0)
    assert rt.eval("STARTS") == 5


def test_answering_app_is_left_alone(lua, tmp_path):
    rt, mod = lua
    mod.launch = rt.execute(COUNT_STARTS)
    track(lua, targets=("pair",))
    mod.update(0.5)
    reply(tmp_path)
    for _ in range(100):
        mod.update(1.0)
    assert rt.eval("STARTS") == 1


def test_no_snapshot_written_is_not_called_calculating(lua, tmp_path):
    # Nothing for the app to answer, which isn't the same as waiting for its answer.
    rt, mod = lua
    mod.launch = rt.eval("function() error('could not start it') end")
    track(lua, targets=("pair",))
    (tmp_path / "calculatro_state.json.tmp").mkdir()
    mod.update(0.5)
    mod.update(2.0)
    assert mod.state_id is None
    assert title_message(rt) == "App didn't start@muted"


def test_app_windows_refused_to_start_is_reported_and_not_retried(lua, tmp_path):
    rt, mod = lua
    rt.execute(r"""
      love.filesystem.getUserDirectory = function() return 'C:/Users/Some One/' end
      STARTS = 0
    """)
    mod.launch = rt.execute(r"""
      return function()
        STARTS = STARTS + 1
        error('could not start C:\\Users\\Some One\\venv\\pythonw.exe (ShellExecuteW error 2)')
      end
    """)
    track(lua, targets=("pair",))
    mod.update(0.5)
    mod.update(2.0)
    assert title_message(rt) == "App didn't start@muted"  # at the first timeout, without waiting for restarts
    mod.update(1000.0)
    assert rt.eval("STARTS") == 1
    log = (tmp_path / "calculatro.log").read_text(encoding="utf-8")
    assert log.startswith("=== Calculatro mod, session ")
    assert r"could not start ~\venv\pythonw.exe (ShellExecuteW error 2)" in log
    assert "Some One" not in log


def test_nothing_to_start_says_so(lua):
    rt, mod = lua
    track(lua, targets=("pair",))
    mod.update(0.5)
    mod.update(2.0)
    assert title_message(rt) == "App didn't start@muted"


@pytest.mark.parametrize("text, logged", [
    (r"C:\Users\Some One\x.exe", r"~\x.exe"),
    ("c:/users/some one/x.exe", "~/x.exe"),  # however it's written
    (r"'C:\Users\Some One': missing", "'~': missing"),
    (r"C:\Users\Some One", "~"),
    (r"C:\Users\Some Oneself\x.exe", r"C:\Users\Some Oneself\x.exe"),  # someone else's
    (r"a C:\Users\Some One\x and C:\Users\Some One\y", r"a ~\x and ~\y"),
])
def test_mod_log_hides_the_home_folder(lua, tmp_path, text, logged):
    rt, mod = lua
    rt.execute("love.filesystem.getUserDirectory = function() return [[C:\\Users\\Some One]] end")
    mod.log_error(text)
    assert (tmp_path / "calculatro.log").read_text(encoding="utf-8").splitlines()[1] == f"[mod] {logged}"


def test_mod_adds_to_the_log_the_app_has_open(lua, tmp_path):
    _, mod = lua
    with cli.open_log(tmp_path / "calculatro.log") as log:
        print("from the app", file=log)
        mod.log_error("from the mod")
        print("from the app again", file=log)
    lines = (tmp_path / "calculatro.log").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "from the app" and lines[2] == "[mod] from the mod" and lines[3] == "from the app again"


def test_broken_step_is_logged_once(lua, tmp_path):
    rt, mod = lua
    mod.launch = rt.execute(COUNT_STARTS)
    mod.check_round = rt.eval("function() error('oops') end")
    for _ in range(3):
        mod.update(0.1)
    log = (tmp_path / "calculatro.log").read_text(encoding="utf-8")
    assert log.count("round failed") == 1 and "oops" in log


def test_nothing_tracked_needs_no_app(lua):
    rt, mod = lua
    mod.update(5.0)
    assert panel_texts(rt) == ["CALCULATRO@light", "+@light", "[settings]", "Nothing tracked@muted"]


def test_panel_hidden_outside_blinds_and_by_hotkey(lua, tmp_path):
    rt, mod = lua
    mod.update(1.0)
    answer(lua, tmp_path)
    mod.update(0.1)
    assert panel_texts(rt)
    rt.execute("G.STATE = G.STATES.SHOP")
    mod.update(0.1)
    assert panel_texts(rt) is None
    mod.key_press("c")  # ignored outside blinds
    rt.execute("G.STATE = G.STATES.SELECTING_HAND")
    mod.update(0.1)
    assert panel_texts(rt)

    mod.key_press("c")
    mod.update(0.1)
    assert panel_texts(rt) is None
    rt.execute("G.CONTROLLER.text_input_hook = {}")
    mod.key_press("c")  # typing
    mod.update(0.1)
    assert panel_texts(rt) is None
    rt.execute("G.CONTROLLER.text_input_hook = nil")
    mod.key_press("c")
    mod.update(0.1)
    assert panel_texts(rt)


def test_panel_rebuilt_after_the_game_removes_it(lua, tmp_path):
    rt, mod = lua
    mod.update(1.0)
    answer(lua, tmp_path)
    mod.update(0.1)
    rt.execute("BOXES[#BOXES].REMOVED = true")  # deleting the run removes every UI box
    mod.update(0.1)
    assert panel_texts(rt)
    boxes = rt.eval("#BOXES")
    rt.execute("G.deck = {cards = G.deck.cards, T = {w = 2.25, y = 8.9}}")  # a new run's deck
    mod.update(0.1)
    assert rt.eval("#BOXES") == boxes + 1 and rt.eval("BOXES[#BOXES].config.major == G.deck")


# --- Buttons and the target editor ----------------------------------------

ALL_HANDS = ("pair", "two pair", "trips", "straight", "flush", "full house", "quads",
             "straight flush", "five of a kind", "flush house", "flush five")


def test_x_stops_tracking_a_row(lua, tmp_path):
    rt, mod = lua
    mod.update(1.0)
    answer(lua, tmp_path, targets=("trips", "pair"), queries=("no face", "queen"))
    mod.update(0.1)
    assert find(rt, "calculatro_remove_Any target") is None  # the combined row can't be removed
    assert click(lua, "calculatro_remove_Three of a Kind")
    # Gone at once, with "Any target" too, before the app has answered.
    assert tracked(rt) == (["Pair"], ["no face", "queen"])
    assert [c[1:] for c in columns(rt, rt.eval("BOXES[#BOXES]").definition)] == [
        ["Pair"], ["Face card: none", "Queen: 1+"]]
    assert click(lua, "calculatro_remove_no face")
    assert tracked(rt) == (["Pair"], ["queen"])
    mod.update(0.2)
    assert read_state(tmp_path).tracked_cards == ["queen"]  # sent to the app


def test_plus_inert_once_nothing_more_fits(lua, tmp_path):
    rt, mod = lua
    mod.update(1.0)
    # 12 hand rows (every hand) in 2 columns and 6 queries in a 3rd: another query needs a 4th.
    queries = ("queen", "king", "ace", "jack", "spade", "heart")
    answer(lua, tmp_path, targets=ALL_HANDS, queries=queries)
    mod.update(0.1)
    assert panel_texts(rt)[:2] == ["CALCULATRO@light", "+@muted"]
    assert not click(lua, "calculatro_open")
    assert mod.editor is None
    # Removing one makes room again.
    assert click(lua, "calculatro_remove_heart")
    assert panel_texts(rt)[1] == "+@light"


def open_editor(lua, tmp_path, targets=("pair",), queries=()):
    rt, mod = lua
    mod.update(1.0)
    answer(lua, tmp_path, targets=targets, queries=queries)
    mod.update(0.1)
    assert click(lua, "calculatro_open")
    return rt, mod


def test_editor_replaces_the_odds_until_done(lua, tmp_path):
    rt, mod = open_editor(lua, tmp_path)
    texts = panel_texts(rt)
    assert texts[:4] == ["CALCULATRO@light", "Done@light", "Hands@light", "Cards@light"]
    assert "Hands@muted" not in texts  # no odds
    box = rt.eval("BOXES[#BOXES]")
    assert box.config.can_collide and rt.eval("BOXES[#BOXES].config.major == G.deck")
    # Opens on the Hands tab: the chosen tab is inert and outlined.
    assert find(rt, "calculatro_tab_hands").config.button is None
    assert find(rt, "calculatro_tab_hands").config.outline
    # New odds and snapshots don't rebuild it under the cursor.
    boxes = rt.eval("#BOXES")
    rt.execute("G.hand.cards[1].highlighted = true")
    mod.update(0.2)
    reply(tmp_path)
    mod.update(3.0)
    assert rt.eval("#BOXES") == boxes
    assert click(lua, "calculatro_done")
    assert mod.editor is None and "Hands@muted" in panel_texts(rt)


def test_editor_reopens_on_the_last_tab(lua, tmp_path):
    rt, mod = open_editor(lua, tmp_path)
    assert click(lua, "calculatro_tab_cards")
    assert click(lua, "calculatro_done")
    assert click(lua, "calculatro_open")
    assert find(rt, "calculatro_tab_cards").config.outline
    assert click(lua, "calculatro_tab_hands")
    assert click(lua, "calculatro_done")
    assert click(lua, "calculatro_open")
    assert find(rt, "calculatro_tab_hands").config.outline


def test_hands_tab_toggles_hands(lua, tmp_path):
    rt, mod = open_editor(lua, tmp_path)
    assert find(rt, "calculatro_hand_Pair").config.colour == "blue"
    assert find(rt, "calculatro_hand_Flush").config.colour == "l_black"
    assert panel_texts(rt)[4:] == [f"{n}@light" for n in [
        "Pair", "Two Pair", "Trips", "Straight", "Flush", "Full House",
        "Quads", "Str. Flush", "5 of a Kind", "Flush House", "Flush Five"]]
    assert click(lua, "calculatro_hand_Flush")
    assert click(lua, "calculatro_hand_Three of a Kind")
    assert tracked(rt)[0] == ["Pair", "Three of a Kind", "Flush"]  # in the game's order
    assert click(lua, "calculatro_hand_Pair")
    assert tracked(rt)[0] == ["Three of a Kind", "Flush"]
    assert click(lua, "calculatro_done")
    assert [c[1:] for c in columns(rt, rt.eval("BOXES[#BOXES]").definition)] == [["Any target", "Trips", "Flush"]]


def test_hands_no_longer_offered_are_dropped(lua, tmp_path):
    """Runs saved by older versions may track Royal Flush."""
    rt, mod = lua
    mod.update(1.0)
    rt.execute("G.GAME.calculatro = {hands = {'Pair', 'Royal Flush', 'Flush'}, cards = {}}")
    mod.write_snapshot()
    assert read_state(tmp_path).tracked_hands == ["Pair", "Flush"]
    assert tracked(rt)[0] == ["Pair", "Flush"]


def test_hands_that_would_not_fit_are_greyed(lua, tmp_path):
    # 12 queries take 2 columns, leaving one for 5 hands and "Any target".
    queries = ("queen", "king", "ace", "jack", "ten", "nine", "eight", "seven", "spade", "heart", "club", "diamond")
    rt, mod = open_editor(lua, tmp_path, targets=ALL_HANDS[:4], queries=queries)
    assert find(rt, "calculatro_hand_Flush").config.button  # a 5th hand still fits
    assert click(lua, "calculatro_hand_Flush")
    assert find(rt, "calculatro_hand_Full House").config.button is None  # a 6th doesn't
    assert "Full House@stale" in panel_texts(rt)
    assert click(lua, "calculatro_hand_Pair")  # tracked ones can still be removed
    assert find(rt, "calculatro_hand_Full House").config.button


def cards_tab(lua, tmp_path, **kwargs):
    rt, mod = open_editor(lua, tmp_path, **kwargs)
    assert click(lua, "calculatro_tab_cards")
    return rt, mod


def option(rt, field) -> str:
    return find(rt, f"calculatro_{field}").nodes[2].nodes[1].config.text  # in its padded row


def set_builder(lua, field, text):
    rt, _ = lua
    for _ in range(25):
        if option(rt, field) == text:
            return
        assert click(lua, f"calculatro_{field}_next")
    raise AssertionError(f"{field} has no option {text!r}")


def test_cards_tab_adds_queries(lua, tmp_path):
    rt, mod = cards_tab(lua, tmp_path)
    assert find(rt, "calculatro_add").config.button is None  # Any, Any, Any: always true
    assert "Nothing tracked@muted" in panel_texts(rt)
    set_builder(lua, "count", "2")
    assert click(lua, "calculatro_rank_12")
    assert option(rt, "preset") == "Queen"
    assert "Add  Queen: 2+@light" in panel_texts(rt)
    assert click(lua, "calculatro_add")
    assert tracked(rt) == (["Pair"], ["at least 2 queen"])
    assert find(rt, "calculatro_add").config.button is None  # already tracked
    for suit in ("Hearts", "Clubs", "Diamonds"):
        assert click(lua, f"calculatro_suit_{suit}")
    set_builder(lua, "special", "Lucky")
    texts = panel_texts(rt)
    assert texts[texts.index("Add  Lucky Q@light"):][:3] == ["Add  Lucky Q@light", "[ui_1 3,1]", ": 2+@light"]
    assert click(lua, "calculatro_add")
    assert tracked(rt)[1] == ["at least 2 queen", "at least 2 lucky queen of spades"]
    # The tracked list, each with its pencil and x.
    texts = panel_texts(rt)
    assert texts[texts.index("Queen: 2+@light"):][:8] == [
        "Queen: 2+@light", "[edit]", "[x]", "Lucky Q@light", "[ui_1 3,1]", ": 2+@light", "[edit]", "[x]"]
    assert click(lua, "calculatro_remove_at least 2 queen")
    assert tracked(rt)[1] == ["at least 2 lucky queen of spades"]
    # The app gets them as queries it can parse.
    assert click(lua, "calculatro_done", dt=0.3)
    _, queries = cli.tracked(read_state(tmp_path))
    assert [q.text for q in queries] == ["at least 2 lucky queen of spades"]


def test_face_cards_take_a_suit(lua, tmp_path):
    rt, mod = cards_tab(lua, tmp_path)
    set_builder(lua, "preset", "Face")
    assert "Add  Face card: 1+@light" in panel_texts(rt)
    for suit in ("Spades", "Clubs", "Diamonds"):
        assert click(lua, f"calculatro_suit_{suit}")
    assert mod.builder_query() == ("at least 1 face of hearts", "Face ♥: 1+")


def test_stone_has_no_rank_or_suit(lua, tmp_path):
    # Stone Cards have no rank or suit, so under Stone the chips and the rank
    # cycle show any and are inert, but keep their choices for other specials.
    rt, mod = cards_tab(lua, tmp_path)
    assert click(lua, "calculatro_rank_12") and click(lua, "calculatro_suit_Hearts")
    set_builder(lua, "special", "Stone")
    assert mod.builder_query() == ("at least 1 stone", "Stone card: 1+")
    assert mod.builder_about() == "In hand: at least 1\nSpecial: Stone"
    assert option(rt, "preset") == "Any"
    assert not click(lua, "calculatro_rank_13") and not click(lua, "calculatro_suit_Spades")
    assert not click(lua, "calculatro_preset_next")
    texts = panel_texts(rt)
    assert "Q@stale" in texts and "Q@light" not in texts
    # Negated, they're back.
    assert click(lua, "calculatro_negate")
    assert mod.builder_query()[0] == "at least 1 not stone queen of spades or clubs or diamonds"
    assert option(rt, "preset") == "Queen"
    assert click(lua, "calculatro_negate")
    set_builder(lua, "special", "Wild")
    assert mod.builder_query()[0] == "at least 1 wild queen of spades or clubs or diamonds"


def test_rank_chips_suit_chips_not_and_ranges(lua, tmp_path):
    rt, mod = cards_tab(lua, tmp_path)
    # A preset sets the chips; changing them makes it Custom.
    set_builder(lua, "preset", "Even")
    assert [r for r in range(2, 15) if mod.builder.ranks[r]] == [2, 4, 6, 8, 10]
    assert "Add  Even: 1+@light" in panel_texts(rt)
    assert click(lua, "calculatro_rank_10")
    assert option(rt, "preset") == "Custom"
    assert mod.builder_query() == ("at least 1 2 or 4 or 6 or 8", "2/4/6/8: 1+")
    assert click(lua, "calculatro_preset_next")  # from Custom as if from Any
    assert option(rt, "preset") == "Face"
    for r in (11, 12, 13):
        assert click(lua, f"calculatro_rank_{r}")
    assert option(rt, "preset") == "Any" and find(rt, "calculatro_add").config.button is None
    # "not" negates the special, so it's inert without one.
    assert not click(lua, "calculatro_negate")
    set_builder(lua, "special", "Sealed")
    assert click(lua, "calculatro_negate")
    assert click(lua, "calculatro_rank_12") and click(lua, "calculatro_rank_13")
    assert "Add  No Seal Q/K: 1+@light" in panel_texts(rt)
    # Turning off the last suit turns them all back on: none means any.
    for suit in ("Spades", "Hearts", "Clubs"):
        assert click(lua, f"calculatro_suit_{suit}")
    assert mod.builder_query()[0] == "at least 1 not sealed queen or king of diamonds"
    assert click(lua, "calculatro_suit_Diamonds")
    assert all(mod.builder.suits[s] for s in SUITS)
    # A range shows its top.
    assert find(rt, "calculatro_high") is None
    set_builder(lua, "cmp", "between")
    assert "and@muted" in panel_texts(rt)
    set_builder(lua, "high", "3")
    assert mod.builder_query() == ("between 1 and 3 not sealed queen or king", "No Seal Q/K: 1-3")
    # Named by the ranks left out when that's shorter.
    flips = (13, 2, 3, 4, 6, 7, 8, 10, 11, 14)
    for r in flips:
        assert click(lua, f"calculatro_rank_{r}")
    assert mod.builder_query()[1] == "No Seal not 5/9/K: 1-3"
    for r in flips:  # back to Q/K
        assert click(lua, f"calculatro_rank_{r}")
    assert click(lua, "calculatro_add")
    assert click(lua, "calculatro_done", dt=0.3)
    _, queries = cli.tracked(read_state(tmp_path))
    assert [(q.text, label_for(q)) for q in queries] == [
        ("between 1 and 3 not sealed queen or king", "No Seal Q/K: 1-3")]


def test_builder_explains_its_query(lua, tmp_path):
    rt, mod = cards_tab(lua, tmp_path)
    set_builder(lua, "special", "Sealed")
    assert click(lua, "calculatro_negate")
    assert click(lua, "calculatro_rank_12") and click(lua, "calculatro_rank_13")
    set_builder(lua, "cmp", "between")
    set_builder(lua, "high", "3")
    assert mod.builder_about() == "In hand: between 1 and 3\nRank: Q or K\nSpecial: no seal"
    # Ranks and suits are named by those left out when that's shorter.
    assert click(lua, "calculatro_suit_Spades")
    set_builder(lua, "preset", "Fibonacci")
    set_builder(lua, "cmp", "at least")
    assert mod.builder_about() == "In hand: at least 1\nRank: Fibonacci (2, 3, 5, 8 or A)\nSuit: any but Spades\n" \
                                  "Special: no seal"
    set_builder(lua, "preset", "Non-face")
    set_builder(lua, "special", "Lucky")
    assert mod.builder_about() == "In hand: at least 1\nRank: Non-face (any but J, Q or K)\nSuit: any but Spades\n" \
                                  "Special: not Lucky"
    set_builder(lua, "preset", "Any")
    assert click(lua, "calculatro_rank_12")
    assert click(lua, "calculatro_negate")
    assert mod.builder_about() == "In hand: at least 1\nRank: Queen\nSuit: any but Spades\nSpecial: Lucky"
    # Added queries keep it for their popups.
    assert click(lua, "calculatro_add")
    assert rt.eval("G.GAME.calculatro.cards[1].about") == mod.builder_about()


def test_pencil_edits_a_tracked_query(lua, tmp_path):
    rt, mod = lua
    mod.update(1.0)
    queries = ("at least 2 queen", "QS", "at most 1 lucky king", "between 1 and 3 face of spades or hearts")
    answer(lua, tmp_path, targets=("pair",), queries=queries)
    mod.update(0.1)
    # On card query rows, but not hands; inert for queries the builder didn't make.
    assert find(rt, "calculatro_edit_Pair") is None
    assert find(rt, "calculatro_edit_at least 2 queen").config.colour == "gold"
    assert not click(lua, "calculatro_edit_QS")
    # It opens the editor's Cards tab with the builder set to the query.
    assert click(lua, "calculatro_edit_at most 1 lucky king")
    assert mod.editor.tab == "cards" and find(rt, "calculatro_tab_cards").config.outline
    assert (option(rt, "cmp"), option(rt, "count"), option(rt, "preset"), option(rt, "special")) == (
        "at most", "1", "King", "Lucky")
    assert find(rt, "calculatro_edit_at most 1 lucky king").config.outline  # the one being edited
    # Add becomes Save, inert until something changes.
    assert "Save  Lucky King: 0-1@stale" in panel_texts(rt)
    assert not click(lua, "calculatro_add")
    assert click(lua, "calculatro_negate")
    assert "Save  No Lucky King: 0-1@light" in panel_texts(rt)
    # Nor to one tracked already.
    set_builder(lua, "special", "Any")
    set_builder(lua, "cmp", "at least")
    set_builder(lua, "count", "2")
    assert click(lua, "calculatro_rank_12") and click(lua, "calculatro_rank_13")
    assert not click(lua, "calculatro_add")
    assert click(lua, "calculatro_rank_12") and click(lua, "calculatro_rank_13")  # back to King
    set_builder(lua, "special", "Lucky")  # still negated
    # Saving puts it in the edited one's place, and stops editing.
    assert click(lua, "calculatro_add")
    assert tracked(rt)[1] == ["at least 2 queen", "QS", "at least 2 not lucky king",
                              "between 1 and 3 face of spades or hearts"]
    card = rt.eval("G.GAME.calculatro.cards[3]")
    assert (card.label, card.about) == ("No Lucky King: 2+", mod.builder_about())
    assert mod.editor.editing is None and "Add  No Lucky King: 2+@stale" in panel_texts(rt)
    # The list's pencils edit too; the edited one's stops editing, keeping the builder's choices.
    assert click(lua, "calculatro_edit_between 1 and 3 face of spades or hearts")
    assert mod.builder_query()[0] == "between 1 and 3 face of spades or hearts"
    assert click(lua, "calculatro_edit_between 1 and 3 face of spades or hearts")
    assert mod.editor.editing is None and find(rt, "calculatro_add").config.button is None  # Add: tracked already
    # Removing the one being edited stops editing it.
    assert click(lua, "calculatro_edit_at least 2 queen")
    assert click(lua, "calculatro_remove_at least 2 queen")
    assert mod.editor.editing is None and "Add  Queen: 2+@light" in panel_texts(rt)
    # It opens on the query's page.
    assert click(lua, "calculatro_done")
    track(lua, queries=("queen", "king", "ace", "jack", "spade", "at least 1 hearts"))
    mod.update(0.1)
    assert click(lua, "calculatro_edit_at least 1 hearts")
    assert option(rt, "page") == "Page 2/2"


FONT = ("G.TILESIZE, G.LANG = 20, {font = {FONT = {getWidth = function(_, s) return 60 * #s end}, squish = 1,"
        " FONTSCALE = 0.1}}")


def test_long_card_names_are_cut_to_fit(lua):
    rt, mod = lua
    name = "Plain not 5/9/K ♥♣♦: 1-3"
    assert mod.fit_label(name, 2.0, 0.32) == name  # as it is without the game's font
    rt.execute(FONT)  # 0.096 a letter at 0.32, and 0.28 an icon
    assert mod.fit_label(name, 3.0, 0.32) == name
    # The cut comes out of the name; the suits and count stay.
    assert mod.fit_label(name, 2.0, 0.32) == "Plai.. ♥♣♦: 1-3"
    assert mod.fit_label(name, 2.57, 0.32) == "Plain not.. ♥♣♦: 1-3"  # no space or "/" before the ".."
    assert mod.fit_label("Lucky Queen: 1+", 1.0, 0.32) == "Luck..: 1+"
    assert mod.fit_label("Lucky Queen: 1+", 0.2, 0.32) == "..: 1+"


def test_card_names_show_their_popup_when_hovered(lua, tmp_path):
    rt, mod = lua
    rt.execute(FONT)
    query = ("between 1 and 3 not enhanced 2 or 3 or 4 or 6 or 7 or 8 or 10 or jack or queen or ace"
             " of hearts or clubs or diamonds")
    mod.update(1.0)
    answer(lua, tmp_path, targets=(), queries=(query,))
    name = rt.eval("G.GAME.calculatro.cards[1].label")
    assert name == "Plain not 5/9/K ♥♣♦: 1-3"
    rt.eval("G.GAME.calculatro.cards[1]").about = "In hand: between 1 and 3"
    mod.update(0.1)
    # Cut to fit at full size in the odds panel, and in the editor's list.
    cell = find(rt, f"calculatro_name_{query}")
    assert cell.config.collideable and cell.config.calculatro_popup.name == name
    shown = "".join(t.split("@")[0] for t in rt.execute(TEXTS)(cell).values())
    assert shown == "Plain.. [ui_1 0,1][ui_1 2,1][ui_1 1,1]: 1-3"
    assert click(lua, "calculatro_open") and click(lua, "calculatro_tab_cards")
    assert find(rt, f"calculatro_name_{query}").config.calculatro_popup.about == "In hand: between 1 and 3"
    # Hovering it shows the full name and what it means, above it.
    rt.execute("UIElement = {hover = function(self) HOVERED = self end}")
    hover_with = rt.execute("""return function(mod, about)
        local e = {config = {calculatro_popup = {name = 'Plain not 5/9/K \\226\\153\\165: 1-3', about = about}}}
        mod.hover_popups(e)
        e:hover()
        assert(HOVERED == e)
        return e
    end""")
    e = hover_with(mod, "In hand: between 1 and 3\nSpecial: no enhancement")
    assert e.config.h_popup_config.align == "tm"
    assert list(rt.execute(TEXTS)(e.config.h_popup).values()) == [
        "Plain not 5/9/K @light", "[ui_1 0,1]", ": 1-3@light",
        "In hand: @muted", "between 1 and 3@light", "Special: @muted", "no enhancement@light"]
    # Queries added before there were popups have just their name.
    assert list(rt.execute(TEXTS)(hover_with(mod, None).config.h_popup).values()) == [
        "Plain not 5/9/K @light", "[ui_1 0,1]", ": 1-3@light"]


def test_tracked_cards_page_five_at_a_time(lua, tmp_path):
    rt, mod = cards_tab(lua, tmp_path, queries=("queen", "king", "ace", "jack", "spade"))
    assert find(rt, "calculatro_page") is None  # one page: no page cycle
    assert click(lua, "calculatro_rank_10")
    assert click(lua, "calculatro_add")  # shows the new query's page
    assert option(rt, "page") == "Page 2/2"
    assert find(rt, "calculatro_remove_at least 1 10") and find(rt, "calculatro_remove_queen") is None
    on_page_2 = path(rt, "calculatro_page")
    assert click(lua, "calculatro_page_next")  # wraps around
    assert option(rt, "page") == "Page 1/2"
    # It stays put as pages change, beside Add rather than under the list.
    assert path(rt, "calculatro_page") == on_page_2
    assert path(rt, "calculatro_page").rsplit("/", 3)[0] == path(rt, "calculatro_add").rsplit("/", 1)[0]
    assert click(lua, "calculatro_page_prev")
    assert click(lua, "calculatro_remove_at least 1 10")  # back to one page
    assert find(rt, "calculatro_page") is None and find(rt, "calculatro_remove_queen")


def test_editor_grows_downwards_when_too_tall(lua, tmp_path):
    lift = 1.9 + 0.2 + 0.5 + 0.1
    rt, mod = open_editor(lua, tmp_path)
    assert rt.eval("BOXES[#BOXES]").alignment.offset.y == pytest.approx(-lift)  # fits
    assert click(lua, "calculatro_done")
    rt.execute("G.consumeables.T.h = 5.3")  # leaves 0.3 for the editor's 0.5
    assert click(lua, "calculatro_open")
    box = rt.eval("BOXES[#BOXES]")
    assert box.alignment.offset.y == pytest.approx(-lift + 0.2)  # its top where it would fit


def test_editor_closes_with_the_panel(lua, tmp_path):
    rt, mod = open_editor(lua, tmp_path)
    rt.execute("G.STATE = G.STATES.HAND_PLAYED")
    mod.update(0.1)
    assert panel_texts(rt) is None and mod.editor is None
    rt.execute("G.STATE = G.STATES.SELECTING_HAND")
    mod.update(0.1)
    assert "Hands@muted" in panel_texts(rt)


def test_targets_reset_with_a_new_run(lua, tmp_path):
    rt, mod = open_editor(lua, tmp_path)
    assert click(lua, "calculatro_hand_Flush")
    rt.execute("G.GAME = {current_round = {hands_left = 4, discards_left = 3}, blind = {name = 'Small Blind'}}")
    mod.update(0.3)
    assert read_state(tmp_path).tracked_hands == []


# --- Best discard ------------------------------------------------------------

def reply_search(tmp_path, options=None):
    """The app's odds for the mod's latest state, with its search's result:
    `options` if given, else what the search finds."""
    state = read_state(tmp_path)
    _, payload = cli.calculate(state, *cli.tracked(state))
    key, nonce = state.search
    found = Search(options, Fraction(1)) if options is not None else cli.find_best(state, cli.search_target(state, key))
    payload["search"] = overlay.search(key, nonce, found)
    assert overlay.write(tmp_path / "calculatro_odds.json", payload)
    return found


def selected(rt) -> list[int]:
    return [i for i, c in enumerate(rt.eval("G.hand.cards").values()) if c.highlighted]


def message(rt) -> str | None:
    texts = panel_texts(rt)
    return texts[3].split("@")[0] if texts[3] != "Hands@muted" else None


def search_ready(lua, tmp_path, targets=("trips", "pair")):
    rt, mod = lua
    mod.update(1.0)
    answer(lua, tmp_path, targets=targets, queries=("no face",))
    mod.update(0.1)


def test_search_buttons_on_every_row(lua, tmp_path):
    rt, mod = lua
    search_ready(lua, tmp_path)
    for key in ("Any target", "Three of a Kind", "Pair", "no face"):
        assert find(rt, "calculatro_search_" + key).config.button
    # Without discards left, a hand can be played to throw cards away, but not the last one.
    rt.execute("G.GAME.current_round.discards_left = 0")
    mod.update(0.01)
    assert find(rt, "calculatro_search_Pair").config.button
    rt.execute("G.GAME.current_round.hands_left = 1")
    mod.update(0.01)
    assert not click(lua, "calculatro_search_Pair")
    rt.execute("G.GAME.current_round.hands_left = 4")
    # And outside selecting cards.
    rt.execute("G.STATE = G.STATES.DRAW_TO_HAND")
    mod.update(0.01)
    assert not click(lua, "calculatro_search_Pair")
    rt.execute("G.STATE = G.STATES.SELECTING_HAND")
    mod.update(0.01)
    assert click(lua, "calculatro_search_Pair")


def test_search_selects_the_best_discard(lua, tmp_path):
    rt, mod = lua
    search_ready(lua, tmp_path)
    assert click(lua, "calculatro_search_Three of a Kind")
    assert read_state(tmp_path).search == ("Three of a Kind", 1)  # sent at once
    # Busy until the app answers.
    assert message(rt) == "Searching..."
    assert not click(lua, "calculatro_search_Three of a Kind")
    sounds = rt.eval("SOUNDS")
    found = reply_search(tmp_path)
    mod.update(0.1)
    assert selected(rt) == found.options[0]
    assert rt.eval("SOUNDS") == sounds + (1 if found.options[0] else 0)
    assert message(rt) == (f"1/{len(found.options)}" if len(found.options) > 1 else None)
    assert find(rt, "calculatro_search_Three of a Kind").config.button


def test_card_row_search_selects_its_best_discard(lua, tmp_path):
    rt, mod = lua
    search_ready(lua, tmp_path)
    track(lua, ("trips", "pair"), ("no face", "at most 1 nine"))
    mod.update(0.1)
    assert click(lua, "calculatro_search_at most 1 nine")
    assert read_state(tmp_path).search == ("at most 1 nine", 1)
    assert message(rt) == "Searching..."
    found = reply_search(tmp_path)
    mod.update(0.1)
    # Throwing one nine away is enough: there's none left to draw.
    assert found.probability == 1 and found.options[:2] == [[2], [3]]
    assert selected(rt) == [2]


def test_search_button_cycles_through_options(lua, tmp_path):
    rt, mod = lua
    search_ready(lua, tmp_path)
    click(lua, "calculatro_search_Pair")
    reply_search(tmp_path, [[0], [1], [0, 1]])
    mod.update(0.1)
    assert selected(rt) == [0] and message(rt) == "1/3"
    for expected, shown in (([1], "2/3"), ([0, 1], "3/3"), ([0], "1/3")):
        assert click(lua, "calculatro_search_Pair")
        assert selected(rt) == expected and message(rt) == shown
    # Selecting something else starts again at the best, without asking the app.
    rt.execute("G.hand.cards[4].highlighted = true")
    mod.update(0.1)
    assert message(rt) is None
    assert click(lua, "calculatro_search_Pair")
    assert selected(rt) == [0] and message(rt) == "1/3"
    assert read_state(tmp_path).search is None  # answered, so no longer asked for
    # Another row's button asks for its own.
    assert click(lua, "calculatro_search_Three of a Kind")
    assert read_state(tmp_path).search == ("Three of a Kind", 2) and message(rt) == "Searching..."


def test_search_keeping_the_hand(lua, tmp_path):
    rt, mod = lua
    search_ready(lua, tmp_path)
    click(lua, "calculatro_search_Pair")
    reply_search(tmp_path, [[]])
    mod.update(0.1)
    assert selected(rt) == [] and message(rt) == "Keep hand"


def test_search_dropped_for_another_hand(lua, tmp_path):
    rt, mod = lua
    search_ready(lua, tmp_path)
    click(lua, "calculatro_search_Pair")
    reply_search(tmp_path, [[0]])
    rt.execute("table.remove(G.hand.cards, 5)")  # the hand changed before the reply arrived
    mod.update(0.1)
    assert selected(rt) == [2, 3] and message(rt) is None
    assert find(rt, "calculatro_search_Pair").config.button  # can search again
    # And options stop standing once the hand changes.
    click(lua, "calculatro_search_Pair")
    reply_search(tmp_path, [[0], [1]])
    mod.update(0.1)
    assert message(rt) == "1/2"
    rt.execute("table.remove(G.hand.cards, 4)")
    mod.update(0.1)
    assert message(rt) is None and mod.found is None


def test_search_options_dropped_once_the_app_stops_sending_them(lua, tmp_path):
    """The app sends a search's result with every payload until what it was worked out from changes (a Tarot
    changing a card in place, a joker sold), which the mod can't tell from the hand's card objects."""
    rt, mod = lua
    search_ready(lua, tmp_path)
    click(lua, "calculatro_search_Pair")
    reply_search(tmp_path, [[0], [1]])
    mod.update(0.1)
    assert message(rt) == "1/2" and mod.found is not None
    rt.execute("G.hand.cards[1].base.id = 13")
    mod.update(0.3)
    reply(tmp_path)  # the app's payloads for the changed state come without it
    mod.update(0.1)
    assert mod.found is None and message(rt) is None
    assert click(lua, "calculatro_search_Pair")  # asks the app again rather than cycling the old options
    assert read_state(tmp_path).search == ("Pair", 2) and message(rt) == "Searching..."


def test_search_options_dropped_when_worked_out_again(lua, tmp_path):
    rt, mod = lua
    search_ready(lua, tmp_path)
    click(lua, "calculatro_search_Pair")
    reply_search(tmp_path, [[0], [1]])
    mod.update(0.1)
    state = read_state(tmp_path)
    _, payload = cli.calculate(state, *cli.tracked(state))
    payload["search"] = overlay.search("Pair", 1, Search([[1]], Fraction(1)))
    assert overlay.write(tmp_path / "calculatro_odds.json", payload)
    mod.update(0.1)
    assert mod.found is None


def test_search_request_goes_with_the_search(lua, tmp_path):
    rt, mod = lua
    search_ready(lua, tmp_path)
    click(lua, "calculatro_search_Pair")
    assert read_state(tmp_path).search == ("Pair", 1)
    rt.execute("table.remove(G.hand.cards, 5)")  # the hand it was for is gone
    mod.update(0.3)
    assert mod.searching is None and read_state(tmp_path).search is None


def using_tarot(rt):
    rt.execute("G.STATE = G.STATES.PLAY_TAROT; G.TAROT_INTERRUPT = G.STATES.SELECTING_HAND")


def deselect_all(rt):
    rt.execute("for _, card in ipairs(G.hand.cards) do card.highlighted = false end")


def test_search_reply_during_tarot_waits_for_a_click(lua, tmp_path):
    rt, mod = lua
    search_ready(lua, tmp_path)
    click(lua, "calculatro_search_Pair")
    # A Tarot is used before the reply arrives, its cards chosen by hand.
    using_tarot(rt)
    deselect_all(rt)
    rt.execute("G.hand.cards[5].highlighted = true")
    reply_search(tmp_path, [[0], [1]])
    mod.update(0.1)
    assert selected(rt) == [4] and message(rt) is None
    assert mod.searching is None and not find(rt, "calculatro_search_Pair").config.button
    mod.search("Pair")  # inert while the Tarot is used, even with options to cycle through
    assert selected(rt) == [4]
    # Back to selecting, the next click selects the best, without asking the app again.
    rt.execute("G.STATE = G.STATES.SELECTING_HAND; G.hand.cards[5].highlighted = false")
    mod.update(0.1)
    assert click(lua, "calculatro_search_Pair")
    assert selected(rt) == [0] and message(rt) == "1/2"
    assert read_state(tmp_path).search is None


def test_search_reply_during_tarot_keeping_the_hand_first(lua, tmp_path):
    rt, mod = lua
    search_ready(lua, tmp_path)
    click(lua, "calculatro_search_Pair")
    using_tarot(rt)
    deselect_all(rt)
    reply_search(tmp_path, [[], [1]])
    mod.update(0.1)
    assert selected(rt) == [] and message(rt) is None  # not "1/2": nothing was selected for it
    rt.execute("G.STATE = G.STATES.SELECTING_HAND")
    mod.update(0.1)
    assert click(lua, "calculatro_search_Pair")
    assert selected(rt) == [] and message(rt) == "1/2"


def test_search_options_not_cycled_during_tarot(lua, tmp_path):
    rt, mod = lua
    search_ready(lua, tmp_path)
    click(lua, "calculatro_search_Pair")
    reply_search(tmp_path, [[0], [1]])
    mod.update(0.1)
    assert selected(rt) == [0]
    using_tarot(rt)
    mod.update(0.1)
    mod.search("Pair")
    assert selected(rt) == [0]


def test_search_keeps_the_bells_forced_card(lua, tmp_path):
    rt, mod = lua
    search_ready(lua, tmp_path)
    rt.execute("G.hand.cards[3].ability.forced_selection = true")
    click(lua, "calculatro_search_Pair")
    reply_search(tmp_path, [[0, 2]])
    mod.update(0.1)
    assert selected(rt) == [0, 2]


def test_building_the_panel_registers_each_object_once(lua, tmp_path):
    """The game registers an element again each time it lays it out, twice where text is shrunk to fit a cell, and
    removing it unregisters it once: the copy left behind kept every old panel alive, until the heap passed the
    game's 300 MB ceiling and it collected garbage every frame. A panel build leaves one entry per object."""
    rt, mod = lua
    rt.execute("A, B, C = {'A'}, {'B'}, {'C'}; G.MOVEABLES = {A, B, A, C, B}; G.STAGE_OBJECTS = {{}, {B, B, C}}")
    answer(lua, tmp_path)
    mod.update(1.0)
    assert panel_texts(rt) is not None
    names = rt.eval("function(list) local out = {} for i, v in ipairs(list) do out[i] = v[1] end return out end")
    assert list(names(rt.eval("G.MOVEABLES")).values()) == ["A", "B", "C"]
    assert list(names(rt.eval("G.STAGE_OBJECTS[2]")).values()) == ["B", "C"]
