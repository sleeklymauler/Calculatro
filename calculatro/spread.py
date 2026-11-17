"""A best-discard search spread over the worker processes (pool.ProcessRunner), a choice's odds to each.

The search is search.py's, in its steps: one task lays it out (`search.plan`), then each choice's odds are a
task of their own (`search.choice_odds`), as many side by side as there are workers, and the app picks the best
as they come in (`search.settle`). The answer is the same as from one process: the odds are exact, the best
choices are all those whose odds equal the best, and a choice is only left out once its upper bound is below
odds already found, which doesn't depend on the order they're found in.
"""

import itertools
import queue
import time
from fractions import Fraction

from . import search, tasks
from .cards import CardQuery
from .hands import Target, get_target
from .pool import Future, Runner
from .state import GameState
from .worker import LIMITS, MEMORY, TIME, Control, Limits, TooSlow

_numbers = itertools.count(1)


class _GivenUp(Exception):
    """The search has reached a limit (`limit`: worker.TIME or MEMORY)."""

    def __init__(self, limit: str):
        super().__init__(limit)
        self.limit = limit


def best_discards(runner: Runner, state: GameState, row: Target | CardQuery, control: Control,
                  limits: Limits = LIMITS) -> search.Search | TooSlow:
    """The best discards for `row`'s target hand or card query, given up as `tasks.search_within` is: after
    `limits.search`, counted from when a worker starts its first task, or at the memory limit, and then
    answered from estimates if it can be. With a runner that has no worker processes (its `width` 1), it's
    one task, `tasks.BestDiscard`. Raises engine.Cancelled once `control` is cancelled.

    A task stopped at the memory limit is tried again, with one fewer of the search's tasks side by side
    from then on, since each worker keeps what the engine shares between the choices (engine.Reuse) for
    itself. With one left, the search is given up for memory.
    """
    if runner.width < 2:
        return runner.submit(tasks.BestDiscard(state, row, limits), control, tasks.SEARCH).result()
    return _Spread(runner, state, row, control, limits).run()


class _Spread:
    def __init__(self, runner: Runner, state: GameState, row: Target | CardQuery, control: Control,
                 limits: Limits):
        self.runner, self.state, self.row, self.control, self.limits = runner, state, row, control, limits
        self.number = next(_numbers)  # the search's, for what its workers keep between its tasks
        self.most = runner.width  # tasks side by side
        self.done: queue.SimpleQueue[Future] = queue.SimpleQueue()
        self.flying: dict[Future, object] = {}  # each task running or waiting, and what it's for
        self.first: Future | None = None
        self.deadline: float | None = None
        self.laid: search.Plan | None = None
        self.lows: list[Fraction] = []
        self.highs: list[Fraction] = []
        self.exact: dict[int, Fraction] = {}
        self.out: set[int] = set()

    def run(self) -> search.Search | TooSlow:
        try:
            try:
                return self.find()
            except _GivenUp as stopped:
                self.drop(list(self.flying))
                return self.estimate(TooSlow(stopped.limit))
        finally:
            self.drop(list(self.flying))
            self.runner.release(self.number)

    # --- Tasks ---------------------------------------------------------------------------------------

    def start(self, task: tasks.Task, what: object) -> None:
        future = self.runner.submit(task, self.control, tasks.SEARCH)
        if self.first is None:
            self.first = future
        self.flying[future] = what
        future.then(self.done.put)

    def drop(self, futures: list[Future]) -> None:
        for future in futures:
            del self.flying[future]
            future.cancel()

    def next(self) -> tuple[object, object]:
        """The next task to finish: what it was for, and its result, or MEMORY if it was stopped for that with
        others beside it (it's to be started again). Raises _GivenUp at a limit, and what the task raised."""
        while True:
            if self.deadline is None and (self.first.started is not None or self.first.done()):
                self.deadline = (self.first.started or time.monotonic()) + self.limits.search
            wait = 0.05 if self.deadline is None else self.deadline - time.monotonic()
            if wait <= 0:
                raise _GivenUp(TIME)
            try:
                future = self.done.get(timeout=wait)
            except queue.Empty:
                continue
            if future not in self.flying:
                continue  # dropped
            found = future.result()
            what = self.flying.pop(future)
            if not isinstance(found, TooSlow):
                return what, found
            if found.limit != MEMORY or self.most <= 1:
                raise _GivenUp(found.limit)
            self.most -= 1
            self.runner.release(self.number)  # what the workers kept for it is what filled the memory
            return what, MEMORY

    def each(self, wanted: dict[object, tasks.Task]) -> dict[object, object]:
        """The results of all of `wanted`'s tasks, `most` side by side."""
        waiting, found = list(wanted), {}
        while waiting or self.flying:
            while waiting and len(self.flying) < self.most:
                what = waiting.pop(0)
                self.start(wanted[what], what)
            what, result = self.next()
            if result is MEMORY:
                waiting.insert(0, what)
            else:
                found[what] = result
        return found

    def choice(self, goal: Target | CardQuery, discard: list[int], miss: bool = False) -> tasks.Choice:
        return tasks.Choice(self.state, goal, discard, self.limits.search, self.number, miss)

    # --- The search ----------------------------------------------------------------------------------

    def find(self) -> search.Search:
        state, row, seconds = self.state, self.row, self.limits.search
        laid = self.each({None: tasks.SearchPlan(state, row, seconds, self.number)})[None]
        self.laid = laid
        count = len(laid.choices)
        self.lows, self.highs = [Fraction(0)] * count, [Fraction(1)] * count
        if isinstance(row, CardQuery):  # every choice is worked out
            self.exact.update(self.each({j: self.choice(row, laid.discards[j]) for j in range(count)}))
            best = max(self.exact.values())
            return self.settle([j for j in range(count) if self.exact[j] == best], best)
        keys = laid.keys.get(row.name)
        if keys is None:
            # Several hands: each one's odds bound those of any of them (search.best_discards). The choices'
            # keys for all together are worked out beside them.
            wanted: dict[object, tasks.Task] = {None: tasks.RelabelKeys(state, row, laid.discards, seconds,
                                                                      self.number)}
            for hand in row.hands:
                for j, key in enumerate(laid.keys[hand]):
                    wanted.setdefault((hand, key), self.choice(get_target(hand), laid.discards[j]))
            found = self.each(wanted)
            keys = found[None]
            for j in range(count):
                each = [found[hand, laid.keys[hand][j]] for hand in row.hands]
                self.lows[j], self.highs[j] = max(each), min(Fraction(1), sum(each))
        return self.best(keys)

    def best(self, keys: list[object]) -> search.Search:
        """The exact odds of the choices that could be best, by their upper bounds, highest first."""
        laid, lows, highs, exact = self.laid, self.lows, self.highs, self.exact
        floor = max(lows)
        best: Fraction | None = None
        known: dict[object, Fraction | None] = {}  # by key; None for one below 1, with 1 found
        sharing: dict[object, list[int]] = {}  # the choices waiting for a key's task
        waiting = sorted(range(len(laid.choices)), key=lambda j: -highs[j])

        def settled(j: int, p: Fraction | None) -> None:
            nonlocal best
            if p is None:
                self.out.add(j)
                return
            exact[j] = p
            best = p if best is None else max(best, p)

        while waiting or self.flying:
            bar = max(floor, best or 0)
            while waiting and len(self.flying) < self.most:
                j = waiting[0]
                if highs[j] < bar:
                    waiting.clear()  # and so are the rest's
                    break
                waiting.pop(0)
                if lows[j] == highs[j]:
                    settled(j, lows[j])
                elif keys[j] in known:
                    settled(j, known[keys[j]])
                elif keys[j] in sharing:
                    sharing[keys[j]].append(j)
                else:
                    sharing[keys[j]] = [j]
                    self.start(self.choice(self.row, laid.discards[j], miss=bar == 1), keys[j])
                bar = max(floor, best or 0)
            if not self.flying:
                continue
            key, p = self.next()
            if p is MEMORY:
                waiting[:0] = sharing.pop(key)
                continue
            known[key] = p
            for j in sharing.pop(key):
                settled(j, p)
            # Those whose upper bound is now below the best found can't be best.
            bar = max(floor, best or 0)
            late = [future for future, key in self.flying.items() if all(highs[j] < bar for j in sharing[key])]
            for future in late:
                del sharing[self.flying[future]]
            self.drop(late)
        return self.settle([j for j, p in exact.items() if p == best], best)

    def settle(self, winners: list[int], best: Fraction) -> search.Search:
        return search.settle(self.state, self.laid, winners, best, self.runner.session)

    def estimate(self, stopped: TooSlow) -> search.Search | TooSlow:
        """What the search given up on answers: from estimates of the choices that could be best, if it
        had got as far as its choices."""
        if self.laid is None:
            return stopped
        seconds = self.limits.search_estimate
        estimated = self.runner.submit(tasks.EstimatedBest(
            self.state, self.row, self.laid, self.lows, self.highs, self.exact, self.out, seconds, self.number),
            self.control, tasks.SEARCH).result()
        return tasks.given_up(stopped, estimated)
