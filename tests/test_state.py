"""Runs the real mod Lua against a mocked Balatro `G` and parses the output."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from calculatro.__main__ import calculate, summarize
from calculatro.cards import parse_query
from calculatro.hands import get_target
from calculatro.rules import Rules
from calculatro.source import FileStateSource
from calculatro.state import SCHEMA, GameState, Joker, MalformedState, SchemaMismatch
from mocked_game import MOD_DIR, directory_items, load_mod, mod_sources, snapshot_json


def test_snapshot_parses(lua):
    state = GameState.from_json(snapshot_json(lua))
    assert state.in_run and state.state == "SELECTING_HAND"
    assert (state.hand_size, state.hands_left, state.discards_left) == (8, 4, 3)
    assert state.blind == "Big Blind"
    assert [c.short() for c in state.hand] == ["Q♥", "Q♠", "9♣", "9♦", "??"]
    assert state.hand[2].seal == "Red" and state.hand[3].edition == "foil"
    assert len(state.unseen) == 5 and state.deck_size == 4 and len(state.discard) == 1
    assert state.jokers == [Joker("j_four_fingers"), Joker("j_smeared", debuff=True)]
    assert state.rules == Rules(four_fingers=True)  # a debuffed joker has no effect
    assert "Jokers: j_four_fingers, j_smeared (debuffed)" in summarize(state)
    assert state.selected == [2, 3]
    assert state.hidden == 1 and "2♥" in [c.short() for c in state.unseen]


def test_snapshot_sends_what_is_tracked(lua):
    rt, _ = lua
    assert GameState.from_json(snapshot_json(lua)).tracked_hands == []  # a new run tracks nothing
    rt.execute("""G.GAME.calculatro = {hands = {'Pair', 'Flush'},
                                       cards = {{query = 'at least 2 queen', label = 'Queen: 2+'}}}""")
    state = GameState.from_json(snapshot_json(lua))
    assert state.tracked_hands == ["Pair", "Flush"] and state.tracked_cards == ["at least 2 queen"]



def test_snapshot_sends_the_serpents_state(lua):
    rt, _ = lua
    state = GameState.from_json(snapshot_json(lua))
    assert (state.blind_disabled, state.hands_played, state.discards_used) == (False, 0, 0)
    assert not state.serpent
    rt.execute("""G.GAME.blind = {name = 'The Serpent'}
                  G.GAME.current_round.hands_played = 1
                  G.GAME.current_round.discards_used = 2""")
    state = GameState.from_json(snapshot_json(lua))
    assert (state.blind, state.hands_played, state.discards_used) == ("The Serpent", 1, 2)
    assert state.serpent
    rt.execute("G.GAME.blind.disabled = true")  # by Chicot or Luchador
    state = GameState.from_json(snapshot_json(lua))
    assert state.blind_disabled and not state.serpent


def test_snapshot_sends_the_bells_forced_card(lua):
    rt, _ = lua
    state = GameState.from_json(snapshot_json(lua))
    assert state.forced is None and not state.bell
    rt.execute("""G.GAME.blind = {name = 'Cerulean Bell'}
                  G.hand.cards[3].ability.forced_selection = true""")
    raw = snapshot_json(lua)
    state = GameState.from_json(raw)
    assert state.bell and state.forced == 2
    assert [c.forced for c in state.hand] == [False, False, True, False, False]
    # Only hand cards say whether they're forced.
    rt.execute("G.deck.cards[1].ability.forced_selection = true")
    raw = snapshot_json(lua)
    assert not any("forced" in c for c in raw["unseen"])
    rt.execute("G.GAME.blind.disabled = true")
    assert not GameState.from_json(snapshot_json(lua)).bell


def test_snapshot_hides_joker_order(lua):
    # Amber Acorn shuffles the jokers face down: their order mustn't leak.
    rt, _ = lua
    before = snapshot_json(lua)["jokers"]
    rt.execute("""local c = G.jokers.cards
                  table.insert(c, 1, {config = {center_key = 'j_shortcut'}})
                  c[1], c[3] = c[3], c[1]""")
    shuffled = snapshot_json(lua)["jokers"]
    rt.execute("local c = G.jokers.cards; c[1], c[2], c[3] = c[3], c[1], c[2]")
    assert snapshot_json(lua)["jokers"] == shuffled == sorted(shuffled, key=lambda j: json.dumps(j, sort_keys=True))
    assert len(shuffled) == 3 and all(j in shuffled for j in before)


def test_snapshot_marks_perished_jokers(lua):
    # Crimson Heart can't lift a run-out Perishable joker's debuff, so the app needs to know.
    rt, _ = lua
    rt.execute("""table.insert(G.jokers.cards, {config = {center_key = 'j_shortcut'}, debuff = true,
                                                ability = {perishable = true, perish_tally = 0}})
                  table.insert(G.jokers.cards, {config = {center_key = 'j_joker'},
                                                ability = {perishable = true, perish_tally = 2}})""")
    state = GameState.from_json(snapshot_json(lua))
    assert state.jokers == [Joker("j_four_fingers"), Joker("j_joker"), Joker("j_shortcut", debuff=True, perished=True),
                            Joker("j_smeared", debuff=True)]


def test_rules_from_jokers():
    assert Rules.from_jokers([]) == Rules()
    # Copies change nothing; Splash and other jokers don't change the odds.
    jokers = [Joker("j_shortcut"), Joker("j_shortcut"), Joker("j_splash"), Joker("j_joker")]
    assert Rules.from_jokers(jokers) == Rules(shortcut=True)
    # One debuffed copy doesn't cancel another that isn't.
    jokers = [Joker("j_pareidolia", debuff=True), Joker("j_pareidolia"), Joker("j_smeared", debuff=True)]
    assert Rules.from_jokers(jokers) == Rules(pareidolia=True)


def odds(state, targets, queries=()) -> str:
    """The odds as the console shows them."""
    return calculate(state, targets, list(queries))[0]


def test_odds_for_selection(lua):
    state = GameState.from_json(snapshot_json(lua))
    # Hand holds 5 of 8, but keeping it draws nothing (the game only refills it
    # after a play or discard): trips needs the face-down card to be Q♣, 1 in the
    # 5 unseen (Q♣ A* St T♦ 2♥). Discarding 99 draws the whole deck: Q♣ guaranteed.
    # Card queries count the final hand too: a third queen is the same Q♣, and
    # the nines are only in hand if kept.
    out = odds(state, [get_target("trips"), get_target("pair")], [parse_query("3+ queens"),
                                                                  parse_query("at most 1 nine")])
    assert out.splitlines() == [
        "Odds: keep hand  ->  discard 9♣ 9♦ and draw 4",
        "  Any target         100.00%  ->  100.00%",
        "  Three of a Kind     20.00%  ->  100.00%",
        "  Pair               100.00%  ->  100.00%",
        "  In hand:",
        "  Queen: at least 3   20.00%  ->  100.00%",
        "  9: at most 1         0.00%  ->  100.00%",
    ]


def test_odds_without_selection(lua):
    rt, _ = lua
    rt.execute("for _, c in ipairs(G.hand.cards) do c.highlighted = nil end")
    state = GameState.from_json(snapshot_json(lua))
    assert odds(state, [get_target("trips")], [parse_query("3+ queens")]).splitlines() == [
        "Odds (current hand):",
        "  Three of a Kind     20.00%",
        "  In hand:",
        "  Queen: at least 3   20.00%",
        "Select cards in game to see the odds after discarding them.",
    ]
    assert odds(state, [], [parse_query("queen")]).splitlines() == [
        "Odds (current hand):",
        "  Queen: at least 1  100.00%",
        "Select cards in game to see the odds after discarding them.",
    ]


def test_odds_on_the_last_hand(lua):
    # With no discards and no hand after this one, nothing is drawn after the
    # selection: it's the hand to be played, so there are no discard odds.
    rt, _ = lua
    rt.execute("G.GAME.current_round.hands_left = 1; G.GAME.current_round.discards_left = 0")
    state = GameState.from_json(snapshot_json(lua))
    assert state.selected
    assert odds(state, [get_target("trips")]).splitlines() == [
        "Odds (current hand):",
        "  Three of a Kind   20.00%",
        "Last hand, with no discards left: nothing is drawn after it.",
    ]


def test_odds_cards_only(lua):
    out = odds(GameState.from_json(snapshot_json(lua)), [], [parse_query("3+ queens")])
    assert out.splitlines() == [
        "Odds: keep hand  ->  discard 9♣ 9♦ and draw 4",
        "  Queen: at least 3   20.00%  ->  100.00%",
    ]


def test_counts_exclude_stone_and_wild(lua):
    state = GameState.from_json(snapshot_json(lua))
    assert state.rank_counts() == {"Queen": 1, "Ace": 1, "10": 1, "2": 1}
    assert state.suit_counts() == {"Clubs": 1, "Diamonds": 1, "Hearts": 1}


def test_stone_base_card_not_leaked(lua):
    # A Stone card hides its rank and suit in game, so they aren't exported.
    rt, _ = lua
    rt.execute("G.hand.cards[1].config.center_key = 'm_stone'")
    data = snapshot_json(lua)
    stones = [c for c in data["hand"] + data["unseen"] if c.get("enhancement") == "m_stone"]
    assert len(stones) == 2
    assert not any(k in c for c in stones for k in ("rank", "rank_id", "suit"))


def test_empty_areas_encode_as_arrays(lua):
    rt, mod = lua
    rt.execute("G.hand.cards = {}")
    data = snapshot_json(lua)
    assert data["hand"] == [] and data["played"] == []


def test_deck_order_not_leaked(lua):
    rt, mod = lua
    before = snapshot_json(lua)["unseen"]
    rt.execute("local c = G.deck.cards; c[1], c[4] = c[4], c[1]")
    assert snapshot_json(lua)["unseen"] == before


def test_face_down_cards_not_told_from_the_deck(lua):
    # Which unseen card is face down in hand isn't public, so swapping it with
    # a deck card (selected or not) must give the same snapshot.
    rt, _ = lua
    before = snapshot_json(lua)
    rt.execute("""local hand, deck = G.hand.cards, G.deck.cards
                  hand[5], deck[1] = deck[1], hand[5]
                  hand[5].facing, deck[1].facing = 'back', 'front'""")
    assert snapshot_json(lua) == before
    rt.execute("G.hand.cards[5].highlighted = true")
    after = snapshot_json(lua)
    assert after["unseen"] == before["unseen"] and after["hand"][4] == {"hidden": True, "highlighted": True}
    assert "deck" not in after and "hidden_pool" not in after


def test_face_down_discards_not_told_from_the_deck(lua):
    # The game doesn't reveal a face-down card when it's discarded (View Deck
    # still counts it as unplayed), so which unseen card it was isn't public:
    # swapping it with a deck card must give the same snapshot.
    rt, _ = lua
    rt.execute("""local c = table.remove(G.deck.cards, 1)
                  c.facing, c.ability.wheel_flipped = 'back', true
                  G.discard.cards[2] = c""")
    before = snapshot_json(lua)
    assert before["discard"][1] == {"hidden": True, "highlighted": False}
    rt.execute("""local deck, discard = G.deck.cards, G.discard.cards
                  discard[2], deck[1] = deck[1], discard[2]
                  discard[2].facing, deck[1].facing = 'back', 'front'
                  discard[2].ability.wheel_flipped, deck[1].ability.wheel_flipped = true, nil""")
    assert snapshot_json(lua) == before
    state = GameState.from_json(before)
    assert len(state.unseen) == 5 and "Q♣" in [c.short() for c in state.unseen]
    assert (state.deck_size, state.hidden, state.discarded_hidden) == (3, 1, 1)
    assert "(3 cards in deck, 1 face down in hand, 1 discarded face down)" in summarize(state)


def test_cards_discarded_face_up_have_been_seen(lua):
    # The game turns every card in the discard pile over, so facing doesn't tell
    # these from cards discarded face down. They mustn't count as still to come.
    state = GameState.from_json(snapshot_json(lua))
    assert "3♣" not in [c.short() for c in state.unseen]
    assert [c.short() for c in state.discard] == ["3♣"] and state.discarded_hidden == 0
    assert (len(state.unseen), state.deck_size) == (5, 4)


# Every card where the game has it, with the areas it sets on them. Face-down
# discards and hand cards are marked, as the game marks them when they're drawn.
PLAYING_CARDS = """
G.playing_cards = {}
for _, area in ipairs({G.deck, G.hand, G.discard}) do
  for _, c in ipairs(area.cards) do
    c.area = area
    G.playing_cards[#G.playing_cards + 1] = c
    if area == G.hand and c.facing == 'back' then c.ability.wheel_flipped = true end
  end
end
"""


def pool_problem(lua, tmp_path):
    """What the check logged, after a new snapshot: None if nothing."""
    rt, mod = lua
    mod.seq = (mod.seq or 0) + 1
    mod.check_unseen()
    log = tmp_path / "calculatro.log"
    return log.read_text(encoding="utf-8") if log.exists() else None


def test_unseen_pool_check_agrees_with_the_game(lua, tmp_path):
    rt, _ = lua
    rt.execute(PLAYING_CARDS)
    rt.execute("""local c = table.remove(G.deck.cards, 1)
                  c.facing, c.area, c.ability.wheel_flipped = 'back', G.discard, true
                  G.discard.cards[2] = c""")  # discarded face down
    assert pool_problem(lua, tmp_path) is None


def test_unseen_pool_check_allows_a_tarot_leaving_a_card_face_down(lua, tmp_path):
    rt, _ = lua
    rt.execute(PLAYING_CARDS)
    rt.execute("G.hand.cards[5].ability.wheel_flipped = nil")  # flipping it up cleared the mark
    assert pool_problem(lua, tmp_path) is None


@pytest.mark.parametrize("change, counts", [
    # The game calls a card played that the mod has in its pool.
    ("table.remove(G.playing_cards, 1)", "mod 5, game 4 (1 extra, 0 missing)"),
    # The game has one unseen that the mod doesn't find in the areas it looks at.
    ("G.discard.cards[1].area = G.deck", "mod 5, game 6 (0 extra, 1 missing)"),
    # The right total with different cards.
    ("G.playing_cards[1] = {area = G.deck, base = {id = 3, suit = 'Clubs'}, config = {}, ability = {}}",
     "mod 5, game 5 (1 extra, 1 missing)"),
])
def test_unseen_pool_check_reports_a_mismatch_once(lua, tmp_path, change, counts):
    rt, _ = lua
    rt.execute(PLAYING_CARDS)
    rt.execute(change)
    log = pool_problem(lua, tmp_path)
    assert f"the unseen pool doesn't match the game's: {counts} in Big Blind" in log
    # Only how many: not which cards, which could give away the face-down ones.
    assert not any(word in log for word in ("Hearts", "Spades", "Clubs", "Diamonds", "Queen"))
    assert pool_problem(lua, tmp_path) == log


def test_unseen_pool_check_waits_for_a_settled_hand(lua, tmp_path):
    rt, mod = lua
    rt.execute(PLAYING_CARDS)
    rt.execute("table.remove(G.playing_cards, 1); G.STATE = G.STATES.DRAW_TO_HAND")
    assert pool_problem(lua, tmp_path) is None
    # And it only looks again at a new snapshot.
    rt.execute("G.STATE = G.STATES.SELECTING_HAND")
    mod.check_unseen()
    assert not (tmp_path / "calculatro.log").exists()
    assert pool_problem(lua, tmp_path) is not None


def test_unseen_pool_check_runs_with_the_snapshots(lua, tmp_path):
    rt, mod = lua
    rt.execute(PLAYING_CARDS)
    rt.execute("table.remove(G.playing_cards, 1)")
    mod.update(1)
    assert "mod 5, game 4" in (tmp_path / "calculatro.log").read_text(encoding="utf-8")


def test_not_in_run(lua):
    rt, _ = lua
    rt.execute("G.STAGE = G.STAGES.MAIN_MENU")
    state = GameState.from_json(snapshot_json(lua))
    assert not state.in_run
    assert summarize(state) == "Not in a run."


def test_update_writes_file_only_on_change(lua, tmp_path):
    rt, mod = lua
    path = tmp_path / "calculatro_state.json"
    mod.update(1.0)
    state = FileStateSource(path).read()
    assert state is not None and len(state.hand) == 5
    assert "Unseen (4 cards in deck, 1 face down in hand)" in summarize(state)

    path.write_text("sentinel")
    mod.update(1.0)  # unchanged state: no rewrite
    assert path.read_text() == "sentinel"

    rt.execute("G.GAME.current_round.discards_left = 2")
    mod.update(1.0)
    assert FileStateSource(path).read().discards_left == 2


def test_missing_file_returns_none(tmp_path):
    assert FileStateSource(tmp_path / "calculatro_state.json").read() is None


@pytest.mark.parametrize("content", [
    b'{"schema": 1, "in_ru',  # truncated
    b'\xef\xbb\xbf{"schema": 1}',  # a byte order mark
    b'{"blind": "caf\xe9"}',  # not UTF-8
    b"[" * 200_000,  # nested too deep to parse
], ids=["truncated", "bom", "invalid-utf8", "deep-nesting"])
def test_undecodable_file_reported_once_until_it_changes(tmp_path, capsys, monkeypatch, content):
    path = tmp_path / "calculatro_state.json"
    path.write_bytes(content)
    source = FileStateSource(path)
    with pytest.raises(MalformedState):
        source.read()

    reads = []
    read_text = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: reads.append(a) or read_text(*a, **k))
    assert source.poll() is None
    assert source.poll() is None  # unchanged: not read again
    assert len(reads) == 1
    assert capsys.readouterr().err.count("isn't a valid snapshot") == 1

    path.write_text(json.dumps({"schema": SCHEMA, "id": "x:1", "in_run": False}))
    state = source.poll()
    assert state is not None and state.id == "x:1"


def test_old_schema_waits_for_a_current_snapshot(lua, tmp_path, capsys):
    """A file from another version of the mod (the last session's, or a mod not
    reinstalled after an update) is skipped with one message, not fatal."""
    _, mod = lua
    path = tmp_path / "calculatro_state.json"
    source = FileStateSource(path)
    path.write_text(json.dumps({"schema": SCHEMA - 1, "id": "old:1", "in_run": False}))
    with pytest.raises(SchemaMismatch):
        source.read()
    assert source.poll() is None
    assert source.poll() is None  # unchanged: not read or reported again
    err = capsys.readouterr().err
    assert err.count("Ignoring") == 1 and "install_mod.ps1" in err

    path.write_text(json.dumps({"schema": SCHEMA - 1, "id": "old:2", "in_run": False}))
    assert source.poll() is None
    assert capsys.readouterr().err == ""  # the same schema is reported once

    mod.update(1.0)  # the mod's first snapshot
    state = source.poll()
    assert state is not None and state.in_run and len(state.hand) == 5


def test_newer_schema_suggests_updating_the_app(tmp_path, capsys):
    path = tmp_path / "calculatro_state.json"
    path.write_text(json.dumps({"schema": SCHEMA + 1, "in_run": False}))
    assert FileStateSource(path).poll() is None
    assert "update the app" in capsys.readouterr().err


@pytest.mark.parametrize("raw", [
    [],
    "snapshot",
    {"schema": SCHEMA, "in_run": True, "hand": [None]},
    {"schema": SCHEMA, "in_run": True, "tracked": ["Pair"]},
    {"schema": SCHEMA, "in_run": True, "tracked": {"hands": "Pair"}},
    {"schema": SCHEMA, "in_run": True, "tracked": {"cards": {"at least 2 queen": True}}},
    {"schema": SCHEMA, "in_run": True, "jokers": ["j_joker"]},
    {"schema": SCHEMA, "in_run": False, "id": 5},
    {"schema": SCHEMA, "in_run": True, "id": 5},
    {"schema": SCHEMA, "in_run": True, "state": ["SELECTING_HAND"]},
    {"schema": SCHEMA, "in_run": True, "blind": 1},
    {"schema": SCHEMA, "in_run": True, "jokers": [{"key": 5}]},
    {"schema": SCHEMA, "in_run": True, "hand": [{"rank": ["Ace"]}]},
    {"schema": SCHEMA, "in_run": True, "unseen": [{"suit": ["Spades"]}]},
])
def test_malformed_snapshot_raises(raw):
    with pytest.raises(MalformedState):
        GameState.from_json(raw)


def test_empty_lua_table_is_an_empty_list():
    state = GameState.from_json({"schema": SCHEMA, "in_run": True, "tracked": {"hands": {}, "cards": {}}})
    assert state.tracked_hands == [] and state.tracked_cards == []


@pytest.mark.parametrize(("search", "parsed"), [
    ({"key": "Pair", "nonce": 3}, ("Pair", 3)),
    ({"key": "Pair", "nonce": True}, None),
    ({"key": "Pair", "nonce": 3.5}, None),
    ({"key": 5, "nonce": 3}, None),
    ("Pair", None),
])
def test_search_request(search, parsed):
    assert GameState.from_json({"schema": SCHEMA, "in_run": True, "search": search}).search == parsed


@pytest.mark.parametrize(("raw", "parsed"), [(4, 4), (1, 1), (None, None), ("4", None), (True, None), (0, None),
                                             (-2, None), (2.5, None)])
def test_memory_setting(raw, parsed):
    snapshot = {"schema": SCHEMA, "memory_gb": raw}
    assert GameState.from_json({**snapshot, "in_run": True}).memory_gb == parsed
    assert GameState.from_json({**snapshot, "in_run": False}).memory_gb == parsed


def test_snapshot_sends_the_memory_setting_as_it_applies(lua):
    """In a run or not, as the panel shows it: no more than half the computer's memory."""
    rt, mod = lua
    mod.installed_memory = rt.eval("function() return 8 * 2^30 end")
    assert GameState.from_json(snapshot_json(lua)).memory_gb == 2  # the default
    mod.settings.memory_gb = 8
    assert GameState.from_json(snapshot_json(lua)).memory_gb == 4
    rt.execute("G.STAGE = G.STAGES.MAIN_MENU")
    state = GameState.from_json(snapshot_json(lua))
    assert not state.in_run and state.memory_gb == 4


def test_malformed_snapshot_skipped_until_a_valid_one(lua, tmp_path, capsys):
    """Valid JSON of the wrong shape is skipped with one message, not fatal."""
    _, mod = lua
    path = tmp_path / "calculatro_state.json"
    source = FileStateSource(path)
    path.write_text(json.dumps({"schema": SCHEMA, "id": "x:1", "in_run": True, "hand": [None]}))
    assert source.poll() is None
    assert source.poll() is None  # unchanged: not read again
    path.write_text(json.dumps({"schema": SCHEMA, "id": "x:2", "in_run": True, "hand": [None]}))
    assert source.poll() is None  # the same error is reported once
    assert capsys.readouterr().err.count("isn't a valid snapshot") == 1

    mod.update(1.0)
    state = source.poll()
    assert state is not None and state.in_run and len(state.hand) == 5


def test_select_limit_zero_kept():
    raw = {"schema": SCHEMA, "in_run": True}
    assert GameState.from_json(raw).select_limit == 5  # missing: the game's default
    assert GameState.from_json({**raw, "select_limit": 0}).select_limit == 0


def test_poll_tolerates_a_locked_file(tmp_path, monkeypatch):
    path = tmp_path / "calculatro_state.json"
    path.write_text(json.dumps({"schema": SCHEMA, "in_run": False}))
    source = FileStateSource(path)

    def locked(*args, **kwargs):
        raise PermissionError

    monkeypatch.setattr(type(path), "stat", locked)
    assert source.poll() is None
    monkeypatch.undo()
    assert source.poll() is not None


def install(save_dir, folder: str = "Calculatro"):
    mod_dir = save_dir / "Mods" / folder
    mod_dir.mkdir(parents=True)
    shutil.copy(MOD_DIR / "lovely.toml", mod_dir)
    return mod_dir


@pytest.mark.parametrize("folder", ["Calculatro", "Calculatro-0.1.0 - Copy"])
def test_launch_command(lua, tmp_path, folder):
    _, mod = lua
    assert mod.launch_command(1234) is None  # no mod folder: the app isn't started
    mod_dir = install(tmp_path, folder)
    assert mod.launch_command(1234) is None  # no launch.txt or python folder
    (mod_dir / "python").mkdir()
    (mod_dir / "python" / "pythonw.exe").write_bytes(b"")
    # A release: its own Python, with a Windows path.
    assert tuple(mod.launch_command(1234)) == (
        str(mod_dir / "python" / "pythonw.exe"), "-m calculatro watch --game-pid 1234")
    # launch.txt (install_mod.ps1) takes priority.
    (mod_dir / "launch.txt").write_text(r"C:\venv\Scripts\pythonw.exe" "\r\n", encoding="utf-8")
    assert tuple(mod.launch_command(1234)) == (
        r"C:\venv\Scripts\pythonw.exe", "-m calculatro watch --game-pid 1234")


def test_mod_folder_is_the_one_holding_the_mods_lovely_toml(lua, tmp_path):
    _, mod = lua
    (tmp_path / "Mods" / "lovely").mkdir(parents=True)  # Lovely's own
    (tmp_path / "Mods" / "notes.txt").write_text("not a folder", encoding="utf-8")
    other = tmp_path / "Mods" / "Another Mod"
    other.mkdir()
    (other / "lovely.toml").write_text('[manifest]\nversion = "1.0.0"\n', encoding="utf-8")
    assert mod.mod_folder() is None
    install(tmp_path, "Renamed")
    assert mod.mod_folder() == "Mods/Renamed"
    assert not (tmp_path / "calculatro.log").exists()


def test_mod_folder_skips_one_lovely_ignores(lua, tmp_path):
    _, mod = lua
    (install(tmp_path) / ".lovelyignore").write_bytes(b"")
    assert mod.mod_folder() is None
    install(tmp_path, "Renamed")
    assert mod.mod_folder() == "Mods/Renamed"


def test_mod_folder_with_several_copies_is_reported_once(lua, tmp_path):
    _, mod = lua
    install(tmp_path, "A copy")
    install(tmp_path, "Z copy")
    assert mod.mod_folder() == "Mods/A copy"
    install(tmp_path)
    assert mod.mod_folder() == "Mods/Calculatro"
    log = (tmp_path / "calculatro.log").read_text(encoding="utf-8")
    assert log.count("more than one copy of the mod") == 1
    assert "A copy, Z copy" in log


# What the game's love.filesystem can do and Lua's own functions can't: a UTF-8 path.
def unicode_filesystem(rt, save_dir):
    def read(name):
        path = save_dir / name
        return path.read_bytes().decode("utf-8") if path.is_file() else None

    def write(name, text):
        (save_dir / name).write_bytes(text.encode("utf-8"))
        return True

    def append(name, text):
        with (save_dir / name).open("ab") as f:
            f.write(text.encode("utf-8"))
        return True

    fs = rt.eval("love.filesystem")
    fs.getSaveDirectory = rt.eval("function(dir) return function() return dir end end")(save_dir.as_posix())
    fs.read, fs.write, fs.append = read, write, append
    fs.getInfo = lambda name: True if (save_dir / name).exists() else None
    fs.getDirectoryItems = directory_items(rt, save_dir)


@pytest.mark.parametrize("name", ["Luké", "Łukasz", "光"])
def test_non_ascii_save_directory(luajit, tmp_path, name):
    rt, mod = luajit
    save_dir = tmp_path / name
    mod_dir = install(save_dir, f"Calculatro {name}")  # the folder's name can be non-ASCII too
    unicode_filesystem(rt, save_dir)
    state_file = save_dir / "calculatro_state.json"

    mod.write_snapshot()  # the first creates the file, the next replaces it
    first = mod.state_id
    assert json.loads(state_file.read_text(encoding="utf-8"))["id"] == first
    rt.execute("G.GAME.current_round.hands_left = 1")
    mod.write_snapshot()
    assert mod.state_id != first
    assert json.loads(state_file.read_text(encoding="utf-8"))["id"] == mod.state_id
    assert not state_file.with_suffix(".json.tmp").exists()

    (save_dir / "calculatro_odds.json").write_text(
        json.dumps({"schema": mod.odds_schema, "id": mod.state_id, "pending": True}), encoding="utf-8")
    mod.read_odds()
    assert mod.ack_id == mod.state_id

    exe = rf"C:\{name}\pythonw.exe"
    (mod_dir / "launch.txt").write_text(exe + "\r\n", encoding="utf-8")
    command, _ = mod.launch_command(1234)
    assert command == exe

    mod.log_error("something broke")
    assert "[mod] something broke" in (save_dir / "calculatro.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("call", ["io.open", "io.lines", "os.rename", "os.remove", "loadfile", "dofile"])
def test_mod_never_uses_a_function_taking_an_ansi_path(call):
    # They can't find the save directory if the Windows username isn't plain ASCII.
    used = [name for name, source in mod_sources().items() if re.search(rf"\b{re.escape(call)}\b", source)]
    assert not used


def test_snapshot_that_cannot_be_written_is_tried_again(lua, tmp_path):
    rt, mod = lua
    mod.write_snapshot()
    written = mod.state_id
    (tmp_path / "calculatro_state.json.tmp").mkdir()  # in the new one's way
    rt.execute("G.GAME.current_round.hands_left = 1")
    mod.write_snapshot()
    assert mod.state_id == written
    (tmp_path / "calculatro_state.json.tmp").rmdir()
    mod.write_snapshot()
    assert mod.state_id != written
    assert json.loads((tmp_path / "calculatro_state.json").read_text(encoding="utf-8"))["id"] == mod.state_id


def test_snapshots_that_keep_failing_are_reported_once(lua, tmp_path):
    rt, mod = lua
    (tmp_path / "calculatro_state.json.tmp").mkdir()
    for _ in range(9):  # now and then is normal, while the app reads the file
        mod.update(mod.interval)
    assert not (tmp_path / "calculatro.log").exists()
    for _ in range(10):
        mod.update(mod.interval)
    log = (tmp_path / "calculatro.log").read_text(encoding="utf-8")
    assert log.count("could not write the state file") == 1


def test_thread_writes_the_state_file_away_from_the_game(threaded, tmp_path):
    rt, mod = threaded
    path = tmp_path / "calculatro_state.json"
    mod.write_snapshot()
    assert not path.exists() and mod.state_id is None  # only handed over

    rt.eval("RUN_THREAD")()
    mod.check_worker()
    assert json.loads(path.read_text(encoding="utf-8"))["id"] == mod.state_id is not None
    assert not path.with_suffix(".json.tmp").exists()


def test_thread_skips_to_the_newest_snapshot(threaded, tmp_path):
    rt, mod = threaded
    mod.write_snapshot()
    rt.execute("G.GAME.current_round.hands_left = 1")
    mod.write_snapshot()
    rt.eval("RUN_THREAD")()
    mod.check_worker()
    written = json.loads((tmp_path / "calculatro_state.json").read_text(encoding="utf-8"))
    assert written["hands_left"] == 1 and written["id"] == mod.state_id


def test_snapshot_is_sent_to_the_thread_once(threaded):
    rt, mod = threaded
    mod.write_snapshot()
    mod.write_snapshot()  # unchanged, though not written yet
    assert rt.eval("#love.thread.getChannel('calculatro_state_requests').items") == 1


def test_failed_write_on_the_thread_is_sent_again_and_reported(threaded, tmp_path):
    rt, mod = threaded
    real = rt.eval("love.filesystem").write
    rt.eval("love.filesystem").write = rt.eval("function() return false, 'locked' end")
    for _ in range(10):
        mod.write_snapshot()
        rt.eval("RUN_THREAD")()
        rt.eval("require('calculatro.log').guard")("state file", mod.check_worker)
    assert mod.state_id is None
    assert "could not write the state file: locked" in (tmp_path / "calculatro.log").read_text(encoding="utf-8")

    rt.eval("love.filesystem").write = real
    mod.write_snapshot()
    rt.eval("RUN_THREAD")()
    mod.check_worker()
    assert mod.state_id is not None and mod.write_failures == 0


def test_mod_writes_the_file_itself_once_the_thread_stops(threaded, tmp_path):
    rt, mod = threaded
    path = tmp_path / "calculatro_state.json"
    mod.write_snapshot()  # handed to the thread, which never gets to it
    rt.execute("THREAD_RUNNING = false")
    mod.check_worker()
    mod.write_snapshot()
    assert json.loads(path.read_text(encoding="utf-8"))["id"] == mod.state_id is not None


def test_thread_reads_the_odds_file_away_from_the_game(threaded, tmp_path):
    rt, mod = threaded
    mod.write_snapshot()
    rt.eval("RUN_THREAD")()
    mod.check_worker()
    (tmp_path / "calculatro_odds.json").write_text(
        json.dumps({"schema": mod.odds_schema, "id": mod.state_id, "pending": True}), encoding="utf-8")

    filesystem = rt.eval("love.filesystem")
    real = filesystem.read
    filesystem.read = rt.eval("function() error('read on the game thread') end")
    mod.read_odds()  # would wait for the lock a stalled write holds, freezing the game
    assert mod.ack_id is None
    filesystem.read = real

    rt.eval("RUN_THREAD")()
    mod.check_worker()
    assert mod.ack_id == mod.state_id


def test_mod_reads_the_odds_file_itself_once_the_thread_stops(threaded, tmp_path):
    rt, mod = threaded
    mod.write_snapshot()
    rt.eval("RUN_THREAD")()
    mod.check_worker()
    rt.execute("THREAD_RUNNING = false")
    mod.check_worker()
    (tmp_path / "calculatro_odds.json").write_text(
        json.dumps({"schema": mod.odds_schema, "id": mod.state_id, "pending": True}), encoding="utf-8")
    mod.read_odds()
    assert mod.ack_id == mod.state_id


def test_thread_that_cannot_start_is_reported_without_stopping(threaded, tmp_path):
    rt, mod = threaded
    rt.execute("package.preload['love.filesystem'] = function() error('no filesystem') end\n"
               "package.loaded['love.filesystem'] = nil")
    mod.write_snapshot()
    rt.eval("RUN_THREAD")()  # raises nothing: an error escaping a thread would stop the game
    mod.check_worker()
    assert "no filesystem" in (tmp_path / "calculatro.log").read_text(encoding="utf-8")


def test_launch_with_nothing_to_start(lua, request):
    rt, _ = lua
    real = load_mod(rt).launch  # the fixture replaces the mod's
    if "luajit" in request.node.name:
        ok, message = rt.eval("pcall")(real)
        assert not ok and "nothing to start" in message
    else:
        assert real() is False  # no FFI outside LuaJIT


@pytest.mark.parametrize("jokers, copies", [
    ([], 0),
    (["j_joker"], 0),
    (["j_dna"], 1),
    (["j_dna", "j_dna"], 2),
    (["j_blueprint", "j_dna"], 2),  # Blueprint copies the joker to its right
    (["j_dna", "j_blueprint"], 1),
    (["j_blueprint", "j_joker", "j_dna"], 1),
    (["j_blueprint", "j_blueprint", "j_dna"], 3),  # along a chain
    (["j_dna", "j_brainstorm"], 2),  # Brainstorm copies the leftmost
    (["j_joker", "j_brainstorm", "j_dna"], 1),
    (["j_brainstorm", "j_dna"], 1),  # the leftmost Brainstorm has nothing to copy
    (["j_blueprint", "j_brainstorm", "j_dna"], 1),  # Blueprint -> Brainstorm -> Blueprint: a loop
    (["j_brainstorm", "j_blueprint", "j_dna"], 2),  # Brainstorm has nothing to copy, but Blueprint reaches DNA
    (["j_dna", "j_blueprint", "j_blueprint", "j_brainstorm"], 4),  # a chain, by way of Brainstorm, back to DNA
])
def test_snapshot_counts_dna_copies(lua, jokers, copies):
    rt, _ = lua
    items = ", ".join(f"{{config = {{center_key = '{key}'}}}}" for key in jokers)
    rt.execute(f"G.jokers.cards = {{{items}}}")
    snapshot = snapshot_json(lua)
    assert snapshot["dna_copies"] == copies
    assert GameState.from_json(snapshot).dna_copies == copies


@pytest.mark.parametrize("debuffed, copies", [
    (0, 1),  # the Blueprint: it does nothing, and DNA still acts alone
    (1, 0),  # DNA: nothing can copy it either
])
def test_snapshot_dna_copies_with_debuffs(lua, debuffed, copies):
    rt, _ = lua
    rt.execute(f"""G.jokers.cards = {{{{config = {{center_key = 'j_blueprint'}}}},
                                      {{config = {{center_key = 'j_dna'}}}}}}
                   G.jokers.cards[{debuffed + 1}].debuff = true""")
    assert snapshot_json(lua)["dna_copies"] == copies


def test_snapshot_dna_copies_debuffed_link_ends_the_chain(lua):
    rt, _ = lua
    rt.execute("""G.jokers.cards = {{config = {center_key = 'j_blueprint'}},
                                    {config = {center_key = 'j_blueprint'}, debuff = true},
                                    {config = {center_key = 'j_dna'}}}""")
    assert snapshot_json(lua)["dna_copies"] == 1


@pytest.mark.parametrize("jokers", [["j_blueprint", "j_dna"], ["j_dna", "j_blueprint"]])
def test_snapshot_dna_copies_hide_the_order_of_face_down_jokers(lua, jokers):
    # Amber Acorn: the player can't see what Blueprint copies, so it mustn't count.
    rt, _ = lua
    items = ", ".join(f"{{config = {{center_key = '{key}'}}, facing = 'back'}}" for key in jokers)
    rt.execute(f"G.jokers.cards = {{{items}}}")
    assert snapshot_json(lua)["dna_copies"] == 1
