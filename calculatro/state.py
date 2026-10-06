"""Game-state model parsed from the Calculatro mod's JSON export."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

SCHEMA = 2

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
    hidden: bool = False  # face-down in hand: identity unknown to the player
    highlighted: bool = False  # selected in hand

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


def _cards(raw: Any) -> list[Card]:
    # An empty Lua table may arrive as {} instead of [].
    if not raw:
        return []
    return [Card.from_json(c) for c in raw]


@dataclass(frozen=True)
class GameState:
    in_run: bool
    state: str | None = None
    hand_size: int = 0
    hands_left: int = 0
    discards_left: int = 0
    blind: str | None = None
    hand: list[Card] = field(default_factory=list)
    played: list[Card] = field(default_factory=list)
    deck: list[Card] = field(default_factory=list)
    discard: list[Card] = field(default_factory=list)
    hidden_pool: list[Card] = field(default_factory=list)  # identities of face-down hand cards
    jokers: list[str] = field(default_factory=list)

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> GameState:
        schema = d.get("schema")
        if schema != SCHEMA:
            raise ValueError(f"unsupported state schema {schema!r} (expected {SCHEMA})")
        if not d.get("in_run"):
            return cls(in_run=False)
        return cls(
            in_run=True,
            state=d.get("state"),
            hand_size=d.get("hand_size") or 0,
            hands_left=d.get("hands_left") or 0,
            discards_left=d.get("discards_left") or 0,
            blind=d.get("blind") or None,
            hand=_cards(d.get("hand")),
            played=_cards(d.get("played")),
            deck=_cards(d.get("deck")),
            discard=_cards(d.get("discard")),
            hidden_pool=_cards(d.get("hidden_pool")),
            jokers=[j.get("key") for j in (d.get("jokers") or []) if j.get("key")],
        )

    @property
    def selected(self) -> list[int]:
        """Indices of hand cards currently highlighted in game."""
        return [i for i, c in enumerate(self.hand) if c.highlighted]

    def rank_counts(self) -> Counter[str]:
        """Remaining deck cards per rank (stone cards excluded)."""
        return Counter(c.rank for c in self.deck if c.rank and not c.is_stone)

    def suit_counts(self) -> Counter[str]:
        """Remaining deck cards per suit (stone and wild cards excluded)."""
        return Counter(c.suit for c in self.deck if c.suit and not c.is_stone and not c.is_wild)
