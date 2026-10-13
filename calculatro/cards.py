"""Card-count queries over the cards drawn after a discard.

Examples: "queen", "2+ queens", "at least 2 queens", "no face cards", "face of spades",
"exactly 1 spade", "<=1 heart", "2 of clubs", "2c", "QS", "lucky", "2+ mult cards",
"red seal", "foil hearts", "lucky red seal queen".

Modifiers (enhancement, edition, seal) go before an optional rank/suit/card
filter and are ANDed with it: "foil queen" is a Queen with the Foil edition.

A bare count means "at least" ("2 queens" = at least 2), and counts may be
spelled out ("two queens"); "no"/"zero"/"0" means none. Suits follow
Balatro's flush rules: Wild Cards (unless debuffed) count as every suit,
Stone Cards have no rank or suit, and with Smeared Joker a card counts as
both suits of its colour.
Face cards follow `Card:is_face`: a debuffed card is never a face card, and
with Pareidolia every other card is one, Stone Cards included.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from .rules import Rules
from .state import RANK_SHORT, SUIT_SHORT, SUITS, Card

_RANK_WORDS = {
    **{str(r): r for r in range(2, 11)},
    "t": 10, "j": 11, "q": 12, "k": 13, "a": 14,
    "jack": 11, "queen": 12, "king": 13, "ace": 14,
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "twos": 2, "threes": 3, "fours": 4, "fives": 5, "sixes": 6,
    "sevens": 7, "eights": 8, "nines": 9, "tens": 10,
    "jacks": 11, "queens": 12, "kings": 13, "aces": 14,
}
_RANK_NAMES = {11: "Jack", 12: "Queen", 13: "King", 14: "Ace"}
_SUIT_WORDS = {
    **{s.lower(): s for s in SUITS},
    **{s.lower()[:-1]: s for s in SUITS},
    **{sym: s for s, sym in SUIT_SHORT.items()},
    "s": "Spades", "h": "Hearts", "c": "Clubs", "d": "Diamonds",
}


def _has_rank(card: Card, rank: int, rules: Rules) -> bool:
    return not card.is_stone and card.rank_id == rank


_COLOUR = {"Spades": "Black", "Clubs": "Black", "Hearts": "Red", "Diamonds": "Red"}


def _has_suit(card: Card, suit: str, rules: Rules) -> bool:
    if card.is_stone:
        return False
    if card.is_wild and not card.debuff:
        return True
    if rules.smeared:
        return card.suit in _COLOUR and _COLOUR[card.suit] == _COLOUR.get(suit)
    return card.suit == suit


def _is_face(card: Card, rules: Rules) -> bool:
    if card.debuff:
        return False
    return rules.pareidolia or (not card.is_stone and card.rank_id in (11, 12, 13))


def _rank_name(r: int) -> str:
    return _RANK_NAMES.get(r, str(r))


@dataclass(frozen=True)
class CardFilter:
    name: str
    test: Callable[[Card, Rules], bool]


@dataclass(frozen=True)
class CardQuery:
    filter: CardFilter
    op: str  # ">=", "<=" or "=="
    count: int
    text: str = field(default="", compare=False)  # as written, e.g. "at least 2 queen"

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


_ENHANCEMENTS = {
    "bonus": "m_bonus", "mult": "m_mult", "wild": "m_wild", "glass": "m_glass",
    "steel": "m_steel", "stone": "m_stone", "gold": "m_gold", "lucky": "m_lucky",
}
_EDITIONS = {
    "foil": ("foil", "Foil"), "holographic": ("holo", "Holographic"), "holo": ("holo", "Holographic"),
    "polychrome": ("polychrome", "Polychrome"), "poly": ("polychrome", "Polychrome"),
    "negative": ("negative", "Negative"),
}
_SEALS = ["Red", "Blue", "Gold", "Purple"]


def _modifiers() -> list[tuple[str, CardFilter]]:
    """(phrase, filter) pairs, longest phrase first so "gold seal" beats "gold"."""
    mods = [(f"{s.lower()} seal", CardFilter(f"{s} Seal", lambda c, rules, s=s: c.seal == s)) for s in _SEALS]
    mods += [(w, CardFilter("Sealed", lambda c, rules: c.seal is not None))
             for w in ("any seal", "sealed", "seal")]
    mods += [(w, CardFilter(name, lambda c, rules, e=e: c.edition == e))
             for w, (e, name) in _EDITIONS.items()]
    mods += [(w, CardFilter("Edition", lambda c, rules: c.edition is not None))
             for w in ("editioned", "edition")]
    mods += [(w, CardFilter(w.capitalize(), lambda c, rules, k=k: c.enhancement == k))
             for w, k in _ENHANCEMENTS.items()]
    mods.append(("enhanced", CardFilter("Enhanced", lambda c, rules: c.enhancement not in (None, "c_base"))))
    return sorted(mods, key=lambda m: len(m[0]), reverse=True)


_MODIFIERS = _modifiers()


def _match_modifier(t: str) -> tuple[CardFilter, str] | None:
    for phrase, f in _MODIFIERS:
        m = re.match(rf"{phrase}s?(?:\s+|$)", t)
        if m:
            return f, t[m.end():]
    return None


def parse_filter(text: str) -> CardFilter:
    t = text.strip().lower()
    t = re.sub(r"\s+cards?$", "", t)  # "face cards", "queen card"
    mods: list[CardFilter] = []
    while (m := _match_modifier(t)):
        mods.append(m[0])
        t = m[1]
    if not mods:
        return _parse_base(t, text)
    base = _parse_base(t, text) if t else None
    filters = mods + ([base] if base else [])
    name = " ".join(f.name for f in mods) + " " + (base.name if base else "card")
    return CardFilter(name, lambda c, rules: all(f.test(c, rules) for f in filters))


def _parse_base(t: str, text: str) -> CardFilter:
    t = _singular(t)
    if t == "face":
        return CardFilter("Face card", _is_face)
    # A suited face card: "face of spades", "face spade" or "face cards of spades".
    m = re.fullmatch(r"faces?(?:\s+cards?)?(?:\s+of)?\s+(\S+)", t)
    if m and _singular(m[1]) in _SUIT_WORDS:
        s = _SUIT_WORDS[_singular(m[1])]
        return CardFilter("Face " + SUIT_SHORT[s], lambda c, rules, s=s:
                          _is_face(c, rules) and _has_suit(c, s, rules))
    if t in _RANK_WORDS:
        r = _RANK_WORDS[t]
        return CardFilter(_rank_name(r), lambda c, rules, r=r: _has_rank(c, r, rules))
    if t in _SUIT_WORDS:
        s = _SUIT_WORDS[t]
        return CardFilter(s[:-1], lambda c, rules, s=s: _has_suit(c, s, rules))
    # Specific card: "2c", "QS", "2♣" or "2 of clubs" (but not "2 clubs", a count).
    for word in sorted(_RANK_WORDS, key=len, reverse=True):
        if not t.startswith(word):
            continue
        m = re.fullmatch(r"(?:\s+of\s+)?(\S+)", t[len(word):])
        suit = m and _singular(m[1])
        if suit in _SUIT_WORDS:
            r, s = _RANK_WORDS[word], _SUIT_WORDS[suit]
            name = RANK_SHORT.get(_rank_name(r), _rank_name(r)) + SUIT_SHORT[s]
            return CardFilter(name, lambda c, rules, r=r, s=s:
                              _has_rank(c, r, rules) and _has_suit(c, s, rules))
    raise ValueError(
        f"unknown card filter {text!r}; use a rank (queen, Q, 10), suit (spade), "
        "specific card (2 of clubs, 2C), 'face' (face of spades), or a modifier (lucky, mult, foil, "
        "polychrome, red seal, enhanced, ...) optionally followed by one of those"
    )


_NUMBER_WORDS = {w: n for n, w in enumerate([
    "zero", "one", "two", "three", "four", "five", "six",
    "seven", "eight", "nine", "ten", "eleven", "twelve",
])}
_NUMBER = r"(\d+|" + "|".join(_NUMBER_WORDS) + ")"


def _number(word: str) -> int:
    return _NUMBER_WORDS[word] if word in _NUMBER_WORDS else int(word)


_COUNT_PATTERNS = [
    (rf"(?:at\s+least|>=)\s*{_NUMBER}\s+", ">=", 0),
    (rf">\s*{_NUMBER}\s+", ">=", 1),
    (rf"(?:at\s+most|<=)\s*{_NUMBER}\s+", "<=", 0),
    (rf"<\s*{_NUMBER}\s+", "<=", -1),
    (rf"(?:exactly|==?)\s*{_NUMBER}\s+", "==", 0),
    (r"(\d+)\+\s*", ">=", 0),
]


def parse_query(text: str) -> CardQuery:
    return replace(_parse_query(text), text=text.strip())


def _parse_query(text: str) -> CardQuery:
    t = text.strip().lower()
    try:  # a bare filter, e.g. "queen" or "2 of clubs" (whose "2" isn't a count)
        return CardQuery(parse_filter(t), ">=", 1)
    except ValueError:
        pass
    for pattern, op, adjust in _COUNT_PATTERNS:
        m = re.match(pattern, t)
        if m:
            count = _number(m[1]) + adjust
            if count < 0:
                raise ValueError(f"impossible count in {text!r}")
            return CardQuery(parse_filter(t[m.end():]), op, count)
    m = re.match(r"(?:no|zero|none)\s+", t)
    if m:
        return CardQuery(parse_filter(t[m.end():]), "==", 0)
    m = re.match(rf"{_NUMBER}\s+", t)
    if m:
        count = _number(m[1])
        return CardQuery(parse_filter(t[m.end():]), "==" if count == 0 else ">=", count)
    m = re.match(r"(?:an?|any)\s+", t)
    return CardQuery(parse_filter(t[m.end():] if m else t), ">=", 1)
