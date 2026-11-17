"""Exact odds that a hand reaches a target, or meets a card query, after one discard and draw.

In four modules, each importing only those before it:

  * `counting`: the counting search. How many draws from the unseen pool make a target's
    patterns (`probability`) or meet a card query (`count_probability`).
  * `discarding`: what a discard does. What's kept and drawn, and every way the final hand can be
    made under the bosses and jokers that change it (_finals), with its exact odds when every
    face-down card is unknown (_evaluate_discard, _evaluate_cards).
  * `evidence`: what the hand's sorts say about its face-down cards. The exact odds averaged
    over what those cards are (`evaluate_discard`, `evaluate_cards`), and the `Session` that
    keeps what's worked out from that evidence.
  * `sampling`: estimates while the exact odds are slow, and `can_miss`, both from final hands
    drawn at random.

The rest of the app imports the names below from here.
"""

from .. import hands
from . import counting, discarding, evidence, sampling
from .counting import Cancelled, Reuse, count_probability, flipped_by_mark, gc_paused, probability
from .discarding import DiscardOutcome, discard_draws, rules_after
from .evidence import (
    Session,
    evaluate_cards,
    evaluate_discard,
    evidence_checked,
    evidence_unchecked,
    note_evidence,
    posterior,
    usable,
    with_usable_evidence,
)
from .sampling import Estimate, can_miss, estimate_cards, estimate_discard

__all__ = [
    "Cancelled",
    "DiscardOutcome",
    "Estimate",
    "Reuse",
    "Session",
    "can_miss",
    "clear_caches",
    "count_probability",
    "discard_draws",
    "estimate_cards",
    "estimate_discard",
    "evaluate_cards",
    "evaluate_discard",
    "evidence_checked",
    "evidence_unchecked",
    "flipped_by_mark",
    "gc_paused",
    "note_evidence",
    "posterior",
    "probability",
    "rules_after",
    "usable",
    "with_usable_evidence",
]


def clear_caches() -> None:
    """Empty every functools cache in the hands and engine modules: the patterns, and what's worked out from them.

    What they hold is small, but it's built in the middle of a calculation, and keeps the memory that
    calculation's temporary objects used from going back to Windows: about 200 MB after a session of every
    benchmark scenario, which comes out of every later calculation's memory limit. `run_watch` calls this when
    the rule jokers held change. Building them again costs up to about 0.2 s (Four Fingers with Shortcut)."""
    for module in (hands, counting, discarding, evidence, sampling):
        for value in vars(module).values():
            if hasattr(value, "cache_clear"):
                value.cache_clear()
