"""The slow calculations a cancel must reach soon, by name: each a benchmark scenario (or a card query over The
House's first hand sorted both ways) and how it's run. `test_cancel` runs each in a thread, timing the stretches
between its checks of `cancel`; `test_pool` runs each as a task in a worker process (`task`)."""

from calculatro import tasks
from calculatro.cards import parse_query
from calculatro.worker import Limits
from scenarios import SCENARIOS

HOUSE_BOTH = "standard | 8 unknown: The House (all face-down), sorted both ways | Pair | No jokers"

# How each is run, and what on: a scenario's id, or for the card queries, the query's text.
SLOW: dict[str, tuple[str, str]] = {
    # Odds rows: The House's union by suit, Cerulean Bell's forced card, The Hook taking from face-down cards,
    # The Mark's face cards in a sort, and a posterior of every deal fitting two sorts.
    "odds, The House, any": ("odds", (
        "wild | 8 unknown: The House (all face-down) | Full House + Straight Flush (any) | Four Fingers + Shortcut")),
    "odds, Cerulean Bell": ("odds", (
        "standard | Keep 6, draw 2, Cerulean Bell | Full House + Straight Flush (any) | Four Fingers + Shortcut")),
    "odds, The Hook, sorted": ("odds", (
        "standard | 5 unknown: 5 face-down kept, sorted by rank, The Hook, 1 played | Pair | No jokers")),
    "odds, The Mark, sorted": ("odds", (
        "standard | 6 unknown: The Mark, 3 face-down, draw 3, sorted by rank | Full House + Straight Flush (any) "
        "| Four Fingers + Shortcut")),
    "odds, sorted both ways": ("odds", (
        "standard | 8 unknown: The House (all face-down), sorted both ways | Straight Flush | No jokers")),
    "estimate, sorted both ways": ("estimate", (
        "standard | 8 unknown: The House (all face-down), sorted both ways | Straight Flush | No jokers")),
    # Best-discard searches.
    "search, 10 cards": ("search", "standard | Best discard: 10 cards | Straight Flush | Four Fingers + Shortcut"),
    "search, 11 cards, any": ("search", (
        "standard | Best discard: 11 cards, holding no target | Flush + Straight + Full House (any) | No jokers")),
    "search, Crimson Heart": ("search", (
        "standard | Best discard: 8 cards, Crimson Heart, played, Joker debuffed | Straight Flush "
        "| Four Fingers + Shortcut")),
    "search, sorted": ("search", (
        "standard | Best discard: 8 cards, 2 face-down, sorted by rank | Flush + Straight + Full House (any) "
        "| Four Fingers + Shortcut")),
    "search given up, sorted": ("search given up", (
        "standard | Best discard: 8 cards, 2 face-down, sorted by rank | Straight Flush | Four Fingers + Shortcut")),
    # Card queries, over The House's first hand sorted both ways.
    "card odds, sorted both ways": ("card odds", "2+ face cards"),
    "card search, sorted both ways": ("card search", "2+ face cards"),
    "card estimate, sorted both ways": ("card estimate", "2+ face cards"),
}


def scenario(sid: str):
    return next(s for s in SCENARIOS if s.id == sid)


def task(name: str, seconds: float = 600.0) -> tasks.Task:
    """The case as the app would give it to a runner, with limits of `seconds` (so only a cancel ends it): a
    search given up on gives up after a second, and answers from estimates for the rest."""
    kind, what = SLOW[name]
    if kind.startswith("card"):
        (state, discard), row = scenario(HOUSE_BOTH).state(), parse_query(what)
    else:
        found, row = scenario(what).state(), scenario(what).target()
        state, discard = found if isinstance(found, tuple) else (found, [])
    if kind.endswith("odds"):
        return tasks.Odds(state, row, discard, seconds)
    if kind.endswith("estimate"):
        return tasks.Approximate(state, row, discard, seconds)
    return tasks.BestDiscard(state, row, Limits(search=1.0 if kind == "search given up" else seconds,
                                                search_estimate=seconds))
