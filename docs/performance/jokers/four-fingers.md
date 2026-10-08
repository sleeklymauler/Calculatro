# Four Fingers

Four Fingers (`j_four_fingers`) lets Flushes and Straights be made with 4
cards, and the flush and the straight in a Straight Flush may be different
cards as long as they fit in the 5 played cards. The exact game rules are in
[docs/jokers.md](../../jokers.md#rules-that-matter-to-calculatro). This page
covers what that does to the engine's performance; see
[../engine.md](../engine.md) for the general design.

## The problem

With Four Fingers, a suit-s Straight Flush on a 4-rank window needs:

- a suit-s card of 3 of the window's ranks;
- any card of the 4th rank, `m`;
- 4 suit-s cards in all.

Two parts of that link the suits:

- **The `m` card can be any suit.** While the search is still on spades, it
  must already remember which ranks have shown up, because a 7♠ could
  complete a hearts run like 5♥ 6♥ 7♠ 8♥.
- **"4 suited cards in all" spans every rank.** It can't be settled until
  every card of the suit has been considered.

So the search carried, for all four suits at once, the suit's count (0–4) and
which nearby ranks it had seen. The positions multiplied across suits, by
about 5× per extra unknown card. Straight Flush with 8 unknown cards (The
House) took 63 s, and 7 unknown took 12 s. Every other hand was well under
half a second.

## What fixed it

Each step measured with no known cards and 8 unknown cards, during
development (benchmark setup slightly different from
[benchmarks.md](../benchmarks.md)):

| Change | Time |
|---|---|
| Original | 63 s |
| Split the target by suit with inclusion–exclusion | 15 s |
| Restate the pattern without "4 suited cards in all" | 4.6 s |
| Faster search (patterns join late, duplicates merged, cached checks) | 3.2 s |
| Cheaper setup; per-pattern tables | 2.7 s |

### 1. Split by suit

Inclusion–exclusion over suits (`_union_by_suit`) turns one search over
four linked suits into:

- **4 single-suit searches.** These are small: the other suits merge into one
  class per rank, because only their rank matters, as the `m` card.
- **Two-suit terms.** These need 4 suited cards of *both* suits, usually 8 of
  the 8 unknown cards, so they are heavily pruned.
- **Terms with 3 or 4 suits.** These are impossible without unseen Wild
  Cards and skipped outright.

### 2. Restate the pattern

Given the 3 suited run cards, "4 suited cards in all" means a 4th suited card
exists. It is one of:

1. the `m` card itself: the whole run is suited, which is one pattern per
   window;
2. a second suited card of one of the 3 run ranks (only possible with
   duplicate cards or Wild Cards);
3. a suited card outside the window.

This is the same condition, but (3) is a single "seen one?" requirement. The
search remembers one bit per suit instead of a count, and the bound on
cards needed got tighter (5 cards, not 4, when `m` is off-suit). Royal Flush
works the same way, with only ranks 10+ counting as "outside".

Since the Shortcut work, runs that share their 3 suited ranks are one
pattern, with "a card of any rank that completes the run" and "a suited card
of another rank". (1) is then the case where that suited card has the
completing rank, so it's no longer a pattern of its own. See
[shortcut.md](shortcut.md).

A side effect: the implication check that `combine()` uses to prune patterns
had to learn that several single-rank requirements add up. Otherwise
"Flush + Straight Flush" stopped collapsing to Flush and got slow. See
"Pruning targets" in [../engine.md](../engine.md).

## Still slow: unseen Wild Cards

Splitting by suit is skipped when an undrawn or face-down card is a Wild
Card. It counts as every suit, so two-suit terms barely prune and the
four-suit term is as hard as the original problem. These cases use one
search over all suits. After the Smeared Joker work, in the benchmark
deck (4 unseen Wild Cards), a Straight Flush takes about 0.14 s at 6 unknown
cards, 0.38 s at 7 and 2.2 s at 8 (The House); 3.3 s with Full House as well.
See [benchmarks.md](../benchmarks.md) for current numbers.

An idea considered here: condition on which Wild Cards are drawn. For each
combination, move them into the kept cards and split the rest by suit. The
number of combinations grows quickly with the number of Wild Cards, and a
rough estimate wasn't clearly better, so it was left out at first. It was
later tried and dropped: with Wild Cards kept, terms with 3 and 4 suits
become possible (see [shortcut.md](shortcut.md#tried-and-dropped)).
