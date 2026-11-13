"""Card-count queries over the final hand: the cards kept and the cards drawn.

Examples: "queen", "2+ queens", "at least 2 queens", "no face cards", "face of spades",
"exactly 1 spade", "<=1 heart", "2 of clubs", "2c", "QS", "lucky", "2+ mult cards",
"red seal", "foil hearts", "lucky red seal queen", "between 1 and 3 queen or king",
"1-3 even of hearts or diamonds", "non-face red cards", "not sealed", "not hearts".

Modifiers (enhancement, edition, seal) go before an optional rank/suit/card
filter and are ANDed with it: "foil queen" is a Queen with the Foil edition.
"not"/"non-" negates a modifier ("not lucky", "non-foil"); "unsealed" and
"unenhanced" are the same as "not sealed" and "not enhanced".

Ranks and suits are ORed within themselves: "queen or king", "2, 3 or 5",
"hearts or diamonds", "red suits"/"black cards", "not hearts" (the other three),
and the groups "even", "odd", "fibonacci" (Even Steven's, Odd Todd's and
Fibonacci's ranks), "face" and "non-face" (not a face card, as Card:is_face
sees it). Ranks then suits: "queen or king of hearts or diamonds".

A bare count means "at least" ("2 queens" = at least 2), and counts may be
spelled out ("two queens"); "no"/"zero"/"0" means none. Ranges are
"between 1 and 3", "1-3" or "1 to 3". Suits follow
Balatro's flush rules: Wild Cards (unless debuffed) count as every suit,
Stone Cards have no rank or suit, and with Smeared Joker a card counts as
both suits of its colour.
Face cards follow `Card:is_face`: a debuffed card is never a face card, and
with Pareidolia every other card is one, Stone Cards included.
"""

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
    op: str  # ">=", "<=", "==" or "between" (count to high, both included)
    count: int
    high: int | None = None  # the top of a "between" range
    text: str = field(default="", compare=False)  # as written, e.g. "at least 2 queen"

    @property
    def label(self) -> str:
        if self.op == "==" and self.count == 0:
            desc = "none"
        elif self.op == "between":
            desc = f"between {self.count} and {self.high}"
        else:
            desc = {">=": "at least", "<=": "at most", "==": "exactly"}[self.op] + f" {self.count}"
        return f"{self.filter.name}: {desc}"

    def accepts(self, k: int) -> bool:
        if self.op == "between":
            return self.count <= k <= self.high
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
    "foil": ("foil", "Foil"), "holographic": ("holo", "Holo"), "holo": ("holo", "Holo"),
    "polychrome": ("polychrome", "Poly"), "poly": ("polychrome", "Poly"),
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
    by_phrase = dict(mods)
    mods += [("un" + w, _negate(by_phrase[w])) for w in ("sealed", "enhanced")]
    return sorted(mods, key=lambda m: len(m[0]), reverse=True)


# What "not <modifier>" is called; the rest are "No <modifier>".
_NEGATED_NAMES = {"Sealed": "No Seal", "Enhanced": "Plain"}


def _negate(f: CardFilter) -> CardFilter:
    return CardFilter(_NEGATED_NAMES.get(f.name, "No " + f.name), lambda c, rules: not f.test(c, rules))


_MODIFIERS = _modifiers()


def _match_modifier(t: str) -> tuple[CardFilter, str] | None:
    neg = re.match(r"(?:not\s+|non-\s*|non\s+)", t)
    if neg and (m := _match_modifier(t[neg.end():])):
        return _negate(m[0]), m[1]
    for phrase, f in _MODIFIERS:
        m = re.match(rf"{phrase}s?(?:\s+|$)", t)
        if m:
            return f, t[m.end():]
    return None


def parse_filter(text: str) -> CardFilter:
    t = text.strip().lower()
    t = re.sub(r"(?<!red)(?<!black)\s+cards?$", "", t)  # "face cards", "queen card", but "red cards"
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


# The rank groups jokers care about: Even Steven, Odd Todd, Fibonacci.
_GROUPS = {
    "even": ("Even", (2, 4, 6, 8, 10)),
    "odd": ("Odd", (3, 5, 7, 9, 14)),
    "fibonacci": ("Fib", (2, 3, 5, 8, 14)),
    "fib": ("Fib", (2, 3, 5, 8, 14)),
}
_OR = re.compile(r"\s*,\s*(?:or\s+)?|\s+or\s+")  # "2, 3 or 5", "queen or king"


def _short(r: int) -> str:
    return RANK_SHORT.get(_rank_name(r), _rank_name(r))


def _ranks_label(ranks: list[int]) -> str:
    """Several ranks, lowest first: runs of 3 or more as "2-5", the rest joined by "/" ("4/T")."""
    parts, i = [], 0
    while i < len(ranks):
        j = i
        while j + 1 < len(ranks) and ranks[j + 1] == ranks[j] + 1:
            j += 1
        if j - i >= 2:
            parts.append(f"{_short(ranks[i])}-{_short(ranks[j])}")
            i = j + 1
        else:
            parts.append(_short(ranks[i]))
            i += 1
    return "/".join(parts)


@dataclass(frozen=True)
class _Ranks:
    name: str    # on its own: "Queen", "Q/K", "Even", "Face card", "Non-face"
    prefix: str  # before suit symbols: "Q" ("Q♠"), "Q/K ", "Face "
    test: Callable[[Card, Rules], bool]


def _rank_set(ranks: tuple[int, ...]) -> Callable[[Card, Rules], bool]:
    return lambda c, rules: not c.is_stone and c.rank_id in ranks


def _parse_ranks(part: str) -> _Ranks | None:
    part = re.sub(r"\s+cards?$", "", part.strip())
    if re.fullmatch(r"faces?", part):
        return _Ranks("Face card", "Face ", _is_face)
    if re.fullmatch(r"non[\s-]?faces?", part):
        return _Ranks("Non-face", "Non-face ", lambda c, rules: not _is_face(c, rules))
    if part in _GROUPS:
        name, ranks = _GROUPS[part]
        return _Ranks(name, name + " ", _rank_set(ranks))
    words = [_singular(w) for w in _OR.split(part)]
    if not all(w in _RANK_WORDS for w in words):
        return None
    ranks = sorted({_RANK_WORDS[w] for w in words})
    if len(ranks) == 1:
        return _Ranks(_rank_name(ranks[0]), _short(ranks[0]), _rank_set(tuple(ranks)))
    # Named by the ranks left out when that's shorter: "not 5/9/K".
    name = _ranks_label(ranks)
    left_out = [r for r in range(2, 15) if r not in ranks]
    if left_out and len(excluded := "not " + _ranks_label(left_out)) < len(name):
        name = excluded
    return _Ranks(name, name + " ", _rank_set(tuple(ranks)))


def _parse_suits(part: str) -> tuple[str, ...] | None:
    """Suits in SUITS order: "spades", "hearts or diamonds", "red suits", "not hearts"."""
    part = part.strip()
    m = re.fullmatch(r"(red|black)\s+(?:suit|card)s?", part)
    if m:
        return ("Hearts", "Diamonds") if m[1] == "red" else ("Spades", "Clubs")
    m = re.fullmatch(r"(?:not\s+|non-\s*|non\s+)(\S+)", part)
    if m:
        s = _SUIT_WORDS.get(_singular(m[1]))
        return tuple(x for x in SUITS if x != s) if s else None
    words = [_singular(w) for w in _OR.split(part)]
    if not all(w in _SUIT_WORDS for w in words):
        return None
    suits = {_SUIT_WORDS[w] for w in words}
    return tuple(s for s in SUITS if s in suits)


def _suited(ranks: _Ranks | None, suits: tuple[str, ...]) -> CardFilter:
    def has_suit(c: Card, rules: Rules) -> bool:
        return any(_has_suit(c, s, rules) for s in suits)
    symbols = "".join(SUIT_SHORT[s] for s in suits)
    if ranks is None:
        return CardFilter(suits[0][:-1] if len(suits) == 1 else symbols, has_suit)
    return CardFilter(ranks.prefix + symbols, lambda c, rules: ranks.test(c, rules) and has_suit(c, rules))


def _parse_base(t: str, text: str) -> CardFilter:
    if ranks := _parse_ranks(t):
        return CardFilter(ranks.name, ranks.test)
    if suits := _parse_suits(t):
        return _suited(None, suits)
    # Ranks of suits: "2 of clubs", "queen or king of hearts or diamonds", "face cards of spades".
    for m in re.finditer(r"\s+of\s+", t):
        ranks, suits = _parse_ranks(t[:m.start()]), _parse_suits(t[m.end():])
        if ranks and suits:
            return _suited(ranks, suits)
    # A named group then suits: "face spade", "even hearts or diamonds" (but not "2 clubs", a count).
    m = re.fullmatch(r"((?:non[\s-]?)?faces?(?:\s+cards?)?|even|odd|fib(?:onacci)?)\s+(.+)", t)
    if m and (suits := _parse_suits(m[2])):
        return _suited(_parse_ranks(m[1]), suits)
    # Specific card: "2c", "QS", "2♣", "10s".
    for word in sorted(_RANK_WORDS, key=len, reverse=True):
        suit = _singular(t[len(word):]) if t.startswith(word) else None
        if suit in _SUIT_WORDS:
            return _suited(_parse_ranks(word), (_SUIT_WORDS[suit],))
    raise ValueError(
        f"unknown card filter {text!r}; use a rank (queen, Q, 10, queen or king, even, odd, fibonacci, face, "
        "non-face), suits (spade, hearts or diamonds, red suits, not hearts), ranks of suits (2 of clubs, 2C, "
        "face of spades), or a modifier (lucky, mult, foil, polychrome, red seal, enhanced, not sealed, ...) "
        "optionally followed by one of those"
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
    m = (re.match(rf"between\s+{_NUMBER}\s+and\s+{_NUMBER}\s+", t)
         or re.match(rf"{_NUMBER}\s*(?:-|to\s)\s*{_NUMBER}\s+", t))
    if m:
        low, high = _number(m[1]), _number(m[2])
        if low > high:
            raise ValueError(f"impossible count in {text!r}")
        f = parse_filter(t[m.end():])
        return CardQuery(f, "==", low) if low == high else CardQuery(f, "between", low, high)
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
