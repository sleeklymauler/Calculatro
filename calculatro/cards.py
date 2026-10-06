"""Card-count queries over the cards drawn after a discard.

Examples: "queen", "2+ queens", "at least 2 queens", "no face cards",
"exactly 1 spade", "<=1 heart", "2 of clubs", "2c", "QS".

A bare count means "at least" ("2 queens" = at least 2); "no"/"zero"/"0"
means none. Suits follow Balatro's flush rules: Wild Cards (unless
debuffed) count as every suit, and Stone Cards have no rank or suit.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from .state import RANK_SHORT, SUIT_SHORT, SUITS, Card

_RANK_WORDS = {
    **{str(r): r for r in range(2, 11)},
    "t": 10, "j": 11, "q": 12, "k": 13, "a": 14,
    "jack": 11, "queen": 12, "king": 13, "ace": 14,
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "twos": 2, "threes": 3, "fours": 4, "fives": 5, "sixes": 6,
    "sevens": 7, "eights": 8, "nines": 9, "tens": 10,
}
_RANK_NAMES = {11: "Jack", 12: "Queen", 13: "King", 14: "Ace"}
_SUIT_WORDS = {
    **{s.lower(): s for s in SUITS},
    **{s.lower()[:-1]: s for s in SUITS},
    **{sym: s for s, sym in SUIT_SHORT.items()},
    "s": "Spades", "h": "Hearts", "c": "Clubs", "d": "Diamonds",
}


def _has_rank(card: Card, rank: int) -> bool:
    return not card.is_stone and card.rank_id == rank


def _has_suit(card: Card, suit: str) -> bool:
    if card.is_stone:
        return False
    if card.is_wild and not card.debuff:
        return True
    return card.suit == suit


def _rank_name(r: int) -> str:
    return _RANK_NAMES.get(r, str(r))


@dataclass(frozen=True)
class CardFilter:
    name: str
    test: Callable[[Card], bool]


@dataclass(frozen=True)
class CardQuery:
    filter: CardFilter
    op: str  # ">=", "<=" or "=="
    count: int

    @property
    def label(self) -> str:
        if self.op == "==" and self.count == 0:
            desc = "none"
        else:
            desc = {">=": "at least", "<=": "at most", "==": "exactly"}[self.op] + f" {self.count}"
        return f"{self.filter.name}: {desc}"

    def accepts(self, k: int) -> bool:
        return {">=": k >= self.count, "<=": k <= self.count, "==": k == self.count}[self.op]


_PLURALS = {"jack", "queen", "king", "ace", "spade", "heart", "club", "diamond", "face"}


def _singular(word: str) -> str:
    # Only word forms pluralize: "QS"/"2s"/"10s" are poker notation for Q♠/2♠/10♠.
    return word[:-1] if word.endswith("s") and word[:-1] in _PLURALS else word


def parse_filter(text: str) -> CardFilter:
    t = text.strip().lower()
    t = re.sub(r"\s+cards?$", "", t)  # "face cards", "queen card"
    t = _singular(t)
    if t == "face":
        return CardFilter("Face card", lambda c: not c.is_stone and c.rank_id in (11, 12, 13))
    if t in _RANK_WORDS:
        r = _RANK_WORDS[t]
        return CardFilter(_rank_name(r), lambda c, r=r: _has_rank(c, r))
    if t in _SUIT_WORDS:
        s = _SUIT_WORDS[t]
        return CardFilter(s[:-1], lambda c, s=s: _has_suit(c, s))
    # Specific card: "2c", "QS", "2♣" or "2 of clubs" (but not "2 clubs", a count).
    for word in sorted(_RANK_WORDS, key=len, reverse=True):
        if not t.startswith(word):
            continue
        m = re.fullmatch(r"(?:\s+of\s+)?(\S+)", t[len(word):])
        suit = m and _singular(m[1])
        if suit in _SUIT_WORDS:
            r, s = _RANK_WORDS[word], _SUIT_WORDS[suit]
            name = RANK_SHORT.get(_rank_name(r), _rank_name(r)) + SUIT_SHORT[s]
            return CardFilter(name, lambda c, r=r, s=s: _has_rank(c, r) and _has_suit(c, s))
    raise ValueError(
        f"unknown card filter {text!r}; use a rank (queen, Q, 10), suit (spade), "
        "specific card (2 of clubs, 2C) or 'face'"
    )


_COUNT_PATTERNS = [
    (r"(?:at\s+least|>=)\s*(\d+)\s+", ">=", 0),
    (r">\s*(\d+)\s+", ">=", 1),
    (r"(?:at\s+most|<=)\s*(\d+)\s+", "<=", 0),
    (r"<\s*(\d+)\s+", "<=", -1),
    (r"(?:exactly|==?)\s*(\d+)\s+", "==", 0),
    (r"(\d+)\+\s*", ">=", 0),
]


def parse_query(text: str) -> CardQuery:
    t = text.strip().lower()
    try:  # a bare filter, e.g. "queen" or "2 of clubs" (whose "2" isn't a count)
        return CardQuery(parse_filter(t), ">=", 1)
    except ValueError:
        pass
    for pattern, op, adjust in _COUNT_PATTERNS:
        m = re.match(pattern, t)
        if m:
            count = int(m[1]) + adjust
            if count < 0:
                raise ValueError(f"impossible count in {text!r}")
            return CardQuery(parse_filter(t[m.end():]), op, count)
    m = re.match(r"(?:no|zero|none)\s+", t)
    if m:
        return CardQuery(parse_filter(t[m.end():]), "==", 0)
    m = re.match(r"(\d+)\s+", t)
    if m:
        count = int(m[1])
        return CardQuery(parse_filter(t[m.end():]), "==" if count == 0 else ">=", count)
    m = re.match(r"(?:an?|any|one)\s+", t)
    return CardQuery(parse_filter(t[m.end():] if m else t), ">=", 1)
