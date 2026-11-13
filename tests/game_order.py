"""How the game sorts the hand, for checking the face-down hint and the odds
against dealing every way. Card:get_nominal (card.lua:950):

    mult = 1, or 1000 sorting by suit, or -1000 for a Stone Card
    nominal + suit_nominal*mult + suit_nominal_original*0.0001*mult + face_nominal + 0.000001*unique_val

and the hand is sorted highest first ('desc', 'suit desc').
"""

from __future__ import annotations

from fractions import Fraction

NOMINAL = {**{r: r for r in range(2, 11)}, 11: 10, 12: 10, 13: 10, 14: 11}
FACE_NOMINAL = {11: Fraction(1, 10), 12: Fraction(2, 10), 13: Fraction(3, 10), 14: Fraction(4, 10)}
SUIT_NOMINAL = {"Diamonds": Fraction(1, 100), "Clubs": Fraction(2, 100), "Hearts": Fraction(3, 100),
                "Spades": Fraction(4, 100)}


def nominal(card, mode: str, tiebreak: int = 0) -> Fraction:
    """Card:get_nominal for `card`, (rank_id, suit) or "Stone", sorted in `mode`.

    `tiebreak` (0 to 99) stands for what the player can't see and only orders
    cards alike in print: the original suit, the creation order (unique_val), and
    a Stone's own rank and suit. A Stone's key is below every other card's.
    """
    small = Fraction(tiebreak, 10**6)
    if card == "Stone":
        return -1000 + small
    rank, suit = card
    mult = 1000 if mode == "suit desc" else 1
    return NOMINAL[rank] + SUIT_NOMINAL[suit] * mult + FACE_NOMINAL.get(rank, 0) + small


def game_order(cards, mode: str) -> list:
    """Names of (name, card) pairs, as the game sorts them; no two cards alike."""
    return [name for name, value in sorted(cards, key=lambda c: nominal(c[1], mode), reverse=True)]
