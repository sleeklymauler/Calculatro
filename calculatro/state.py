"""Game-state model parsed from the Calculatro mod's JSON export."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Any

from .rules import Rules

SCHEMA = 15

RANK_ORDER = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "Jack", "Queen", "King", "Ace"]
RANK_SHORT = {"10": "T", "Jack": "J", "Queen": "Q", "King": "K", "Ace": "A"}
SUITS = ["Spades", "Hearts", "Clubs", "Diamonds"]
SUIT_SHORT = {"Spades": "♠", "Hearts": "♥", "Clubs": "♣", "Diamonds": "♦"}


def _text(d: dict[str, Any], key: str) -> str | None:
    """A field that's text or absent. Numbers aren't checked like this: the mod's
    encoder can write the game's as floats or null, and they'd reject real snapshots."""
    value = d.get(key)
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{key} is a {type(value).__name__}, not a string")
    return value


@dataclass(frozen=True)
class Card:
    rank: str | None = None
    rank_id: int | None = None
    suit: str | None = None
    enhancement: str | None = None
    edition: str | None = None
    seal: str | None = None
    debuff: bool = False
    hidden: bool = False  # face down in hand or discarded face down: identity unknown to the player
    highlighted: bool = False  # selected in hand
    forced: bool = False  # Cerulean Bell: part of every play and discard
    # A face-down hand card in the observations: its id there (`GameState.observed`),
    # which tells it apart from other face-down cards, and stays the same when
    # the hand is dragged. None for every other card.
    latent: int | None = None

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Card:
        return cls(
            rank=_text(d, "rank"),
            rank_id=d.get("rank_id"),
            suit=_text(d, "suit"),
            enhancement=d.get("enhancement"),
            edition=d.get("edition"),
            seal=d.get("seal"),
            debuff=bool(d.get("debuff", False)),
            hidden=bool(d.get("hidden", False)),
            highlighted=bool(d.get("highlighted", False)),
            forced=bool(d.get("forced", False)),
        )

    @property
    def is_stone(self) -> bool:
        """Stone cards have no rank or suit for hand evaluation."""
        return self.enhancement == "m_stone"

    @property
    def is_wild(self) -> bool:
        return self.enhancement == "m_wild"

    def short(self) -> str:
        if self.hidden:
            return "??"
        if self.is_stone:
            return "St"
        rank = RANK_SHORT.get(self.rank or "", self.rank or "?")
        suit = "*" if self.is_wild else SUIT_SHORT.get(self.suit or "", "?")
        return f"{rank}{suit}"


@dataclass(frozen=True)
class Joker:
    key: str
    debuff: bool = False  # a debuffed joker has no effect
    perished: bool = False  # a Perishable joker past its rounds: debuffed for good


def _cards(raw: Any) -> list[Card]:
    # An empty Lua table may arrive as {} instead of [].
    if not raw:
        return []
    return [Card.from_json(c) for c in raw]


def _strings(raw: Any) -> list[str]:
    # An empty Lua table may arrive as {} instead of [].
    if not raw:
        return []
    if not isinstance(raw, list):
        raise TypeError(f"expected a list of strings, not a {type(raw).__name__}")
    return [s for s in raw if isinstance(s, str)]


def _search(raw: Any) -> tuple[str, int] | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("key"), str):
        return None
    nonce = raw.get("nonce")
    # bool is a kind of int, but `true` isn't a number.
    return (raw["key"], nonce) if isinstance(nonce, int) and not isinstance(nonce, bool) else None


def _memory_gb(raw: Any) -> int | None:
    """The "Memory limit" setting, or None (the default) for anything but a number of GB above 0."""
    # bool is a kind of int, but `true` isn't a number.
    return raw if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0 else None


SORT_MODES = ("desc", "suit desc")  # the game's rank and suit sorts


@dataclass(frozen=True)
class ObservedCard:
    """A card in an observation, as the player saw it: exactly one of its printed
    rank and suit, a Stone (which shows neither), face down in hand at `hand`, or
    since discarded face down (one of the discard pile's hidden cards, not saying which)."""

    rank_id: int | None = None
    suit: str | None = None
    stone: bool = False
    hand: int | None = None  # 0-based index into `GameState.hand`, of a hidden card
    discarded: bool = False

    @property
    def latent(self) -> bool:
        """Whether the player hasn't seen what it is."""
        return self.hand is not None or self.discarded


@dataclass(frozen=True)
class Observation:
    """The hand's order when it was known to be the game's sort in `mode`, left
    to right, as ids into `GameState.observed`. See docs/plans/face-down-windows.md."""

    mode: str  # one of SORT_MODES
    cards: tuple[int, ...]


def _int(value: Any, what: str) -> int:
    # bool is a kind of int, but `true` isn't a number.
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{what} is a {type(value).__name__}, not an integer")
    return value


def _observed_card(raw: Any, hand: list[Card]) -> tuple[int, ObservedCard]:
    """An observation's entry: its id and what it says the card is."""
    if not isinstance(raw, dict):
        raise TypeError(f"an observed card is a {type(raw).__name__}, not an object")
    card_id = _int(raw.get("id"), "an observed card's id")
    kinds = [kind for kind in ("printed", "stone", "hand", "discarded")
             if any(key in raw for key in (("rank_id", "suit") if kind == "printed" else (kind,)))]
    if len(kinds) != 1:
        raise ValueError(f"observed card {card_id} is {' and '.join(kinds) or 'nothing'}, not one kind")
    if kinds[0] in ("stone", "discarded"):
        if raw[kinds[0]] is not True:
            raise ValueError(f"observed card {card_id} has {kinds[0]} {raw[kinds[0]]!r}")
        return card_id, ObservedCard(stone=True) if kinds == ["stone"] else ObservedCard(discarded=True)
    if kinds == ["hand"]:
        index = _int(raw["hand"], "an observed card's hand index")
        if not 0 <= index < len(hand) or not hand[index].hidden:
            raise ValueError(f"observed card {card_id} is at hand index {index}, which isn't a face-down card")
        return card_id, ObservedCard(hand=index)
    rank_id = _int(raw.get("rank_id"), "an observed card's rank_id")
    suit = raw.get("suit")
    if not 2 <= rank_id <= 14 or suit not in SUITS:
        raise ValueError(f"observed card {card_id} is rank {rank_id!r} of suit {suit!r}")
    return card_id, ObservedCard(rank_id=rank_id, suit=suit)


def _observations(raw: Any, hand: list[Card],
                  discard: list[Card]) -> tuple[tuple[Observation, ...], tuple[ObservedCard, ...]]:
    """The snapshot's observations, and what each of their cards is by id.

    They're in the order the mod recorded them, and a mode may repeat (every
    sort seen this round is true of the cards in it). Ids are renumbered 0, 1,
    ... by first appearance, so equal evidence parses equal. Only the shape is
    checked here: evidence no deal fits (face-up cards out of order, say) is
    well-shaped, and is for the odds to set aside, not a malformed snapshot.
    """
    # An empty Lua table may arrive as {} instead of [].
    if not raw:
        return (), ()
    if not isinstance(raw, list):
        raise TypeError(f"observations is a {type(raw).__name__}, not a list")
    renumbered: dict[int, int] = {}
    observed: list[ObservedCard] = []
    observations = []
    for observation in raw:
        if not isinstance(observation, dict):
            raise TypeError(f"an observation is a {type(observation).__name__}, not an object")
        mode = observation.get("mode")
        if mode not in SORT_MODES:
            raise ValueError(f"observation of unknown mode {mode!r}")
        entries = observation.get("cards") or []
        if not isinstance(entries, list):
            raise TypeError(f"an observation's cards are a {type(entries).__name__}, not a list")
        cards = [_observed_card(entry, hand) for entry in entries]
        ids = [card_id for card_id, _ in cards]
        if len(set(ids)) != len(ids):
            raise ValueError(f"observation {mode!r} has an id more than once")
        order = []
        for card_id, card in cards:
            if card_id not in renumbered:
                renumbered[card_id] = len(observed)
                observed.append(card)
            elif observed[renumbered[card_id]] != card:
                raise ValueError(f"observed card {card_id} differs between the sorts")
            order.append(renumbered[card_id])
        observations.append(Observation(mode, tuple(order)))
    hands = [card.hand for card in observed if card.hand is not None]
    if len(set(hands)) != len(hands):
        raise ValueError("two observed cards are at the same hand index")
    if sum(card.discarded for card in observed) > sum(card.hidden for card in discard):
        raise ValueError("more observed cards discarded face down than the discard pile has")
    return tuple(observations), tuple(observed)


class SchemaMismatch(ValueError):
    """A snapshot written by a different version of the mod."""

    def __init__(self, schema: Any):
        super().__init__(f"unsupported state schema {schema!r} (expected {SCHEMA})")
        self.schema = schema


class MalformedState(ValueError):
    """A snapshot of the right schema whose fields don't have the shapes it says."""


@dataclass(frozen=True)
class GameState:
    in_run: bool
    id: str | None = None  # snapshot id; the odds file echoes it so the mod knows they're current
    state: str | None = None
    hand_size: int = 0
    hands_left: int = 0
    discards_left: int = 0
    blind: str | None = None
    blind_disabled: bool = False  # by Chicot or Luchador
    hands_played: int = 0  # this round
    discards_used: int = 0  # this round
    hand: list[Card] = field(default_factory=list)
    played: list[Card] = field(default_factory=list)
    # Cards the player hasn't seen: the undrawn deck and the face-down cards in
    # hand or discarded together, since which are which isn't public (see `deck_size`).
    unseen: list[Card] = field(default_factory=list)
    discard: list[Card] = field(default_factory=list)
    jokers: list[Joker] = field(default_factory=list)
    # Copies DNA would make of a lone first hand, with Blueprint and Brainstorm.
    # The mod works it out, since `jokers` hides their order.
    dna_copies: int = 0
    # What the player tracks, set in the mod's panel: hand names ("Three of a
    # Kind") and card queries for `cards.parse_query` ("at least 2 queen").
    tracked_hands: list[str] = field(default_factory=list)
    tracked_cards: list[str] = field(default_factory=list)
    select_limit: int = 5  # most cards that can be selected (and so discarded) at once
    # The latest best-discard search asked for in the panel: the row's key (a
    # hand's name or "Any target") and its number, which goes up with each one.
    search: tuple[str, int] | None = None
    # What the hand's order has shown of its face-down cards: the sorts seen this round, in the order
    # recorded (a mode may repeat), and what each card in them is, by id (the index here). The odds average over
    # what the face-down cards in them are (engine.posterior).
    observations: tuple[Observation, ...] = ()
    observed: tuple[ObservedCard, ...] = ()
    # The player's "Memory limit" setting: the most the app's calculations may use, in GB, as the
    # panel shows it (already no more than half the computer's); None without one (process.memory_limit).
    memory_gb: int | None = None

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> GameState:
        """Parse a snapshot.

        Raises SchemaMismatch for one of another schema, and MalformedState for
        one whose fields aren't what the schema says (a hand-edited file or a
        bug in the mod). Only shapes are checked, not ranges, and of the scalars
        only those that must be text (`_text`). The exception is the observations
        (`_observations`), whose ids, cards and hand indices must agree.
        """
        if not isinstance(d, dict):
            raise MalformedState(f"snapshot is a {type(d).__name__}, not an object")
        schema = d.get("schema")
        if schema != SCHEMA:
            raise SchemaMismatch(schema)
        try:
            return cls._parse(d)
        except (TypeError, AttributeError, KeyError, ValueError) as e:
            raise MalformedState(f"{type(e).__name__}: {e}") from e

    @classmethod
    def _parse(cls, d: dict[str, Any]) -> GameState:
        if not d.get("in_run"):
            return cls(in_run=False, id=_text(d, "id"), memory_gb=_memory_gb(d.get("memory_gb")))
        tracked = d.get("tracked") or {}
        hand = _cards(d.get("hand"))
        discard = _cards(d.get("discard"))
        observations, observed = _observations(d.get("observations"), hand, discard)
        for card_id, card in enumerate(observed):
            if card.hand is not None:
                hand[card.hand] = replace(hand[card.hand], latent=card_id)
        return cls(
            in_run=True,
            id=_text(d, "id"),
            state=_text(d, "state"),
            hand_size=d.get("hand_size") or 0,
            hands_left=d.get("hands_left") or 0,
            discards_left=d.get("discards_left") or 0,
            blind=_text(d, "blind") or None,
            blind_disabled=bool(d.get("blind_disabled")),
            hands_played=d.get("hands_played") or 0,
            discards_used=d.get("discards_used") or 0,
            hand=hand,
            played=_cards(d.get("played")),
            unseen=_cards(d.get("unseen")),
            discard=discard,
            jokers=[Joker(_text(j, "key"), bool(j.get("debuff", False)), bool(j.get("perished", False)))
                    for j in (d.get("jokers") or []) if j.get("key")],
            dna_copies=d.get("dna_copies") or 0,
            tracked_hands=_strings(tracked.get("hands")),
            tracked_cards=_strings(tracked.get("cards")),
            select_limit=d.get("select_limit", 5),  # 0 stays 0
            search=_search(d.get("search")),
            observations=observations,
            observed=observed,
            memory_gb=_memory_gb(d.get("memory_gb")),
        )

    @property
    def serpent(self) -> bool:
        """Whether The Serpent is in play: after a play or discard, it draws 3 cards."""
        return self.blind == "The Serpent" and not self.blind_disabled

    @property
    def mark(self) -> bool:
        """Whether The Mark is in play: face cards are drawn face down, so face-down cards are face cards."""
        return self.blind == "The Mark" and not self.blind_disabled

    @property
    def bell(self) -> bool:
        """Whether Cerulean Bell is in play: one hand card is forced into every play and discard."""
        return self.blind == "Cerulean Bell" and not self.blind_disabled

    @property
    def hook(self) -> bool:
        """Whether The Hook is in play: after a hand is played, it discards 2 random cards held in hand."""
        return self.blind == "The Hook" and not self.blind_disabled

    @property
    def crimson(self) -> bool:
        """Whether Crimson Heart is in play: after a hand is played, it debuffs another joker."""
        return self.blind == "Crimson Heart" and not self.blind_disabled

    @property
    def forced(self) -> int | None:
        """Index of the hand card Cerulean Bell forces, if any."""
        return next((i for i, c in enumerate(self.hand) if c.forced), None)

    @property
    def rules(self) -> Rules:
        """Hand and card rules from the active (non-debuffed) jokers."""
        return Rules.from_jokers(self.jokers)

    @property
    def selected(self) -> list[int]:
        """Indices of hand cards currently highlighted in game."""
        return [i for i, c in enumerate(self.hand) if c.highlighted]

    @property
    def hidden(self) -> int:
        """How many hand cards are face down."""
        return sum(c.hidden for c in self.hand)

    @property
    def discarded_hidden(self) -> int:
        """How many cards were discarded face down: the game doesn't turn them up."""
        return sum(c.hidden for c in self.discard)

    @property
    def deck_size(self) -> int:
        """Cards left to draw: the unseen ones not face down in hand or the discard pile."""
        return len(self.unseen) - self.hidden - self.discarded_hidden

    def rank_counts(self) -> Counter[str]:
        """Unseen cards per rank (stone cards excluded)."""
        return Counter(c.rank for c in self.unseen if c.rank and not c.is_stone)

    def suit_counts(self) -> Counter[str]:
        """Unseen cards per suit (stone and wild cards excluded)."""
        return Counter(c.suit for c in self.unseen if c.suit and not c.is_stone and not c.is_wild)
