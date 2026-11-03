"""Game-state model parsed from the Calculatro mod's JSON export."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .rules import Rules

SCHEMA = 11

RANK_ORDER = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "Jack", "Queen", "King", "Ace"]
RANK_SHORT = {"10": "T", "Jack": "J", "Queen": "Q", "King": "K", "Ace": "A"}
SUITS = ["Spades", "Hearts", "Clubs", "Diamonds"]
SUIT_SHORT = {"Spades": "♠", "Hearts": "♥", "Clubs": "♣", "Diamonds": "♦"}


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

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Card:
        return cls(
            rank=d.get("rank"),
            rank_id=d.get("rank_id"),
            suit=d.get("suit"),
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
    return [s for s in raw or [] if isinstance(s, str)]


def _search(raw: Any) -> tuple[str, int] | None:
    if isinstance(raw, dict) and isinstance(raw.get("key"), str) and isinstance(raw.get("nonce"), int):
        return raw["key"], raw["nonce"]
    return None


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

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> GameState:
        """Parse a snapshot.

        Raises SchemaMismatch for one of another schema, and MalformedState for
        one whose fields aren't what the schema says (a hand-edited file or a
        bug in the mod). Only shapes are checked, not ranges.
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
            return cls(in_run=False, id=d.get("id"))
        tracked = d.get("tracked") or {}
        return cls(
            in_run=True,
            id=d.get("id"),
            state=d.get("state"),
            hand_size=d.get("hand_size") or 0,
            hands_left=d.get("hands_left") or 0,
            discards_left=d.get("discards_left") or 0,
            blind=d.get("blind") or None,
            blind_disabled=bool(d.get("blind_disabled")),
            hands_played=d.get("hands_played") or 0,
            discards_used=d.get("discards_used") or 0,
            hand=_cards(d.get("hand")),
            played=_cards(d.get("played")),
            unseen=_cards(d.get("unseen")),
            discard=_cards(d.get("discard")),
            jokers=[Joker(j["key"], bool(j.get("debuff", False)), bool(j.get("perished", False)))
                    for j in (d.get("jokers") or []) if j.get("key")],
            dna_copies=d.get("dna_copies") or 0,
            tracked_hands=_strings(tracked.get("hands")),
            tracked_cards=_strings(tracked.get("cards")),
            select_limit=d.get("select_limit", 5),  # 0 stays 0
            search=_search(d.get("search")),
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
