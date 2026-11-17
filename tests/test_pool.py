"""The runners (calculatro.pool): tasks run in the job's thread, or in worker processes that are stopped,
replaced and ended as docs/architecture.md#worker-processes says. The worker processes are Windows' (the
app's memory readings and job object are)."""

import os
import pickle
import subprocess
import sys
import time
from dataclasses import replace
from fractions import Fraction

import pytest

import calculatro.__main__ as cli
from calculatro import engine, pool, process, spread, tasks
from calculatro.cards import parse_query
from calculatro.engine import Cancelled
from calculatro.hands import combine, get_target
from calculatro.pool import InProcessRunner, ProcessRunner, TaskFailed, WorkerDied
from calculatro.report import build_report
from calculatro.state import Card, GameState
from calculatro.worker import MEMORY, TIME, Control, Job, Limits, TooSlow, Watchdog
from fakes import FakeSource
from pool_tasks import Do
from slow_cases import SLOW
from slow_cases import task as slow_task

windows = pytest.mark.skipif(sys.platform != "win32", reason="worker processes are Windows'")
MB = 2**20


def wait_for(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.005)


class Pool:
    """A ProcessRunner with `cores` workers ready, and what it logged."""

    def __init__(self, cores: int = 2, **kwargs):
        self.logged: list[str] = []
        self.runner = ProcessRunner(log=self.logged.append, **kwargs)
        self.runner.resize(cores)
        assert self.runner.wait_ready()

    def run(self, task, control: Control | None = None, priority: int = 0):
        return self.runner.submit(task, control or Control(), priority).result()

    def pids(self) -> set[int]:
        return {worker.process.pid for worker in self.runner._workers}


@pytest.fixture
def workers():
    made: list[Pool] = []

    def make(cores: int = 2, **kwargs) -> Pool:
        made.append(Pool(cores, **kwargs))
        return made[-1]

    yield make
    for each in made:
        each.runner.close()


def card(rank_id: int, suit: str = "Spades") -> Card:
    return Card(rank=str(rank_id), rank_id=rank_id, suit=suit, enhancement="c_base")


HAND = [card(9), card(10, "Hearts"), card(12, "Clubs"), card(12, "Diamonds"), card(14)]
UNSEEN = [card(r, s) for s in ("Spades", "Hearts", "Clubs", "Diamonds") for r in (11, 13)]
REAL = GameState(in_run=True, state="SELECTING_HAND", hand_size=5, hands_left=2, discards_left=2,
                 hand=[replace(c, highlighted=i == 0) for i, c in enumerate(HAND)], unseen=UNSEEN,
                 tracked_hands=["Pair", "Flush"], tracked_cards=["at least 2 queen"])


# --- In the job's thread ---------------------------------------------------------------------------------

def test_in_process_runs_the_task_before_submit_returns():
    runner = InProcessRunner()
    done = runner.submit(Do("pid"))
    assert done.done() and done.result() == os.getpid()
    assert runner.submit(Do("wait", 0.05), Control(Watchdog(period=0.005))).result() == TooSlow(TIME)
    failed = runner.submit(Do("fail", 1))
    with pytest.raises(ValueError, match="no good: 1"):
        failed.result()
    control = Control()
    control.cancel()
    with pytest.raises(Cancelled):
        runner.submit(Do("wait", 5), control).result()


def test_tasks_and_their_results_pickle():
    """As they cross to a worker process and back: a card query goes by its text."""
    query = parse_query("at least 2 queen")
    rows = [get_target("Pair"), combine([get_target("Pair"), get_target("Flush")]), query]
    for row in rows:
        for task in (tasks.Odds(REAL, row, [0], 5.0), tasks.Approximate(REAL, row, [], 1.0),
                     tasks.BestDiscard(REAL, row, Limits())):
            copy = pickle.loads(pickle.dumps(task))
            assert tasks.row_key(copy.row) == tasks.row_key(row) and replace(copy, row=row) == task
            found = task.run(None, engine.Session())
            assert pickle.loads(pickle.dumps(found)) == found
    assert pickle.loads(pickle.dumps(TooSlow(MEMORY))) == TooSlow(MEMORY)
    with pytest.raises(TypeError, match="without its text"):
        pickle.dumps(replace(query, text=""))


# --- In worker processes ---------------------------------------------------------------------------------

@windows
def test_a_task_runs_in_a_worker_and_answers(workers):
    made = workers()
    found = made.runner.submit(Do("pid"), Control())
    assert found.result() in made.pids() and found.result() != os.getpid()
    slow = made.runner.submit(Do("wait", 0.1), Control())
    assert slow.result() == TooSlow(TIME)  # by the worker's own watchdog
    assert 0.1 <= slow.seconds < 2
    assert made.logged == []


@windows
def test_real_tasks_answer_as_they_do_in_process(workers):
    made = workers()
    query = parse_query("at least 2 queen")
    for task in (tasks.Odds(REAL, get_target("Pair"), [0], 30.0), tasks.Odds(REAL, query, [], 30.0),
                 tasks.BestDiscard(REAL, get_target("Flush"), Limits()),
                 tasks.BestDiscard(REAL, query, Limits())):
        assert made.run(task) == task.run(Control(), engine.Session())


@windows
def test_without_workers_or_a_control_tasks_run_in_process(workers):
    made = workers()
    assert made.runner.submit(Do("pid")).result() == os.getpid()  # no job's Control: nothing could cancel it
    made.runner.resize(1)
    assert made.run(Do("pid")) == os.getpid()
    wait_for(lambda: not made.pids())
    made.runner.resize(3)
    assert made.runner.wait_ready() and len(made.pids()) == 3
    assert made.run(Do("pid")) in made.pids()
    made.runner.resize(2)
    assert len(made.pids()) == 2 and made.run(Do("pid")) in made.pids()
    assert made.logged == [] and made.runner._deaths == 0  # ended by the app: not counted against them


@windows
def test_a_failed_task_is_logged_once_with_the_workers_traceback(workers, capsys):
    made, failures = workers(), cli.Failures()
    for n in range(2):
        with pytest.raises(TaskFailed) as raised:
            made.run(Do("fail", n))
        failures("Odds for snapshot s:1", raised.value)
    err = capsys.readouterr().err
    assert err.count("Odds for snapshot s:1 failed") == 1 and "ValueError: no good: 0" in err
    assert "pool_tasks.py" in err and "in _fail" in err  # where it was raised, in the worker
    with pytest.raises(TaskFailed) as raised:  # a result that can't come back
        made.run(Do("unpicklable"))
    assert "pickle" in raised.value.text
    assert made.run(Do("pid")) in made.pids()  # the workers are no worse for it


@windows
def test_what_a_worker_prints_is_logged_once(workers):
    made = workers()
    for row in ("a", "b", "a", "b"):  # both workers say it
        made.run(Do("say", "The hand's sorts fit no deal", row=row, lane=row))
    made.run(Do("say", "something else"))
    wait_for(lambda: len(made.logged) == 2)
    assert made.logged == ["The hand's sorts fit no deal\n", "something else\n"]


@windows
def test_cancelling_the_job_cancels_its_task_at_once(workers):
    made = workers()
    job = Job(lambda control: made.run(Do("wait", 60), control))
    wait_for(lambda: any(w.entry for w in made.runner._workers))
    start = time.monotonic()
    job.cancel()
    assert job.wait(1) and time.monotonic() - start < 0.5
    with pytest.raises(Cancelled):
        job.result()
    wait_for(lambda: not any(w.entry for w in made.runner._workers), 2)  # the worker stopped it
    # The cancelled task's own answer, when it comes, isn't taken for the next one's.
    assert [made.run(Do("pid")) in made.pids() for _ in range(4)] == [True] * 4
    control = Control()
    control.cancel()
    with pytest.raises(Cancelled):  # cancelled already: never started
        made.run(Do("wait", 60), control)
    assert made.runner._deaths == 0 and len(made.pids()) == 2


@windows
@pytest.mark.parametrize("name", SLOW)
def test_a_slow_task_answers_a_cancel_well_within_the_grace(workers, name):
    """Each of test_cancel's slow cases, as a task in a worker: cancelled partway through, the worker is free
    again long before CANCEL_GRACE, after which the app would end it. By the clock, on a machine busy with the
    other tests, so the bound is far above the 0.1 s of CPU time the engine goes between checks."""
    made = workers()
    before, took = made.pids(), None

    def busy() -> bool:
        return any(worker.entry for worker in made.runner._workers)

    for running in (2.0, 0.5):  # the first fills the worker's caches, whose builds can't be cancelled
        job = Job(lambda control: made.run(slow_task(name), control))
        wait_for(lambda job=job: busy() or job.wait(0))
        if job.wait(running):
            job.result()  # finished (or failed) before it could be cancelled
            took = None
            continue
        cancelled = time.monotonic()
        job.cancel()
        assert job.wait(1)  # for the job, it's over at once
        wait_for(lambda: not busy(), 20)
        took = time.monotonic() - cancelled
    assert took is None or took < pool.CANCEL_GRACE / 2, f"the worker took {took:.2f} s to stop"
    assert made.pids() == before and made.runner._deaths == 0  # none was ended for it


@windows
def test_a_task_waits_for_a_free_worker_in_order_of_priority(workers):
    made = workers()
    busy = [Job(lambda control, n=n: made.run(Do("wait", 60, row=f"busy{n}"), control)) for n in range(2)]
    wait_for(lambda: all(w.entry for w in made.runner._workers))
    order: list[str] = []
    waiting = [Job(lambda control, name=name, priority=priority: order.append(
        made.run(Do("say", name, row=name), control, priority) or name))
        for name, priority in (("exact", tasks.EXACT), ("search", tasks.SEARCH), ("quick", tasks.QUICK))]
    wait_for(lambda: len(made.runner._waiting) == 3)
    assert order == []
    busy[0].cancel()
    assert all(job.wait(5) for job in waiting)
    assert order == ["quick", "search", "exact"]
    busy[1].cancel()


@windows
def test_a_rows_tasks_go_to_the_worker_that_had_it(workers):
    made = workers(3)
    first = {row: made.run(Do("pid", row=row, lane=lane)) for row, lane in (("a", "odds"), ("b", "search"))}
    assert first["a"] != first["b"]  # another lane's task takes a worker no lane has used
    assert made.run(Do("pid", row="c", lane="search")) == first["b"]  # a new row: its lane's worker
    for _ in range(3):  # whatever the lane, and whichever worker was free longest
        assert {row: made.run(Do("pid", row=row, lane="other")) for row in first} == first


@windows
def test_a_worker_that_doesnt_answer_a_cancel_is_replaced(workers, monkeypatch):
    monkeypatch.setattr(pool, "CANCEL_GRACE", 0.2)
    made = workers()
    before = made.pids()
    job = Job(lambda control: made.run(Do("hang"), control))
    wait_for(lambda: any(w.entry for w in made.runner._workers))
    job.cancel()
    assert job.wait(1)
    wait_for(lambda: len(made.pids()) == 2 and made.pids() != before and made.runner.wait_ready())
    assert made.run(Do("pid")) in made.pids()
    assert made.runner._deaths == 0 and made.logged == []  # the app ended it


@windows
def test_a_worker_that_dies_fails_its_task_and_is_replaced_until_the_app_does_without(workers):
    made = workers()
    for died in range(1, pool.DEATHS_MOST + 1):
        with pytest.raises(WorkerDied):
            made.run(Do("die"))
        assert made.runner._deaths == died
        if died < pool.DEATHS_MOST:
            assert made.runner.wait_ready() and len(made.pids()) == 2
            assert made.run(Do("pid")) in made.pids()
    assert not made.pids()
    assert made.run(Do("pid")) == os.getpid()  # in the app itself from here on
    made.runner.resize(4)
    assert not made.pids() and not made.runner.wait_ready(0.05)
    assert [line.startswith("A worker process ended unexpectedly (exit code 3)") for line in made.logged] == [
        True, True, True, False]
    assert "3 worker processes have ended unexpectedly" in made.logged[-1]


@windows
def test_over_the_memory_limit_only_the_largest_workers_task_is_stopped(workers):
    made = workers()
    watchdog = Watchdog(made.runner.memory_used, memory_limit=300 * MB, period=0.01, grace=30)
    small = Job(lambda control: made.run(Do("hog", 20 * MB, row="small"), control), watchdog)
    wait_for(lambda: any(w.entry for w in made.runner._workers))
    large = Job(lambda control: made.run(Do("hog", 400 * MB, row="large"), control), watchdog)
    assert large.wait(10) and large.result() == TooSlow(MEMORY)
    assert not small.wait(0.3)
    wait_for(lambda: made.runner.memory_used() < 300 * MB)  # given back
    small.cancel()
    assert made.runner._deaths == 0 and len(made.pids()) == 2


@windows
def test_a_worker_that_keeps_the_memory_is_ended(workers):
    made = workers()
    before = made.pids()
    watchdog = Watchdog(made.runner.memory_used, memory_limit=300 * MB, period=0.01, grace=0.2)
    other = Job(lambda control: made.run(Do("wait", 60, row="other"), control), watchdog)
    wait_for(lambda: any(w.entry for w in made.runner._workers))
    deaf = Job(lambda control: made.run(Do("deaf_hog", 400 * MB), control), watchdog)
    assert deaf.wait(10) and deaf.result() == TooSlow(MEMORY)
    assert not other.wait(0.3)  # under the limit again: nothing else is stopped
    wait_for(lambda: len(made.pids()) == 2 and made.runner.wait_ready())
    assert len(made.pids() & before) == 1 and made.runner._deaths == 0
    other.cancel()


@windows
def test_a_worker_left_with_too_much_memory_is_replaced_between_tasks(workers):
    made = workers()
    made.runner.resize(2, memory_limit=40 * MB)  # a quarter is less than a worker starts with
    first = made.run(Do("pid"))
    wait_for(lambda: first not in made.pids() and len(made.pids()) == 2)
    assert made.runner._deaths == 0 and made.logged == []


@windows
def test_the_sorts_are_checked_once_between_the_workers(workers):
    from scenarios import THE_HOUSE_BOTH, Scenario
    state = Scenario(THE_HOUSE_BOTH, ("Pair",), "No jokers").state()[0]
    made = workers()
    assert engine.evidence_checked(state, made.runner.session) is None
    assert made.run(Do("sorts_checked", row="a", lane="a", state=state)) is None
    assert made.run(Do("check_sorts", row="a", lane="a", state=state)) is True
    assert engine.evidence_checked(state, made.runner.session) is True  # the app knows, for `evidence_unchecked`
    assert not engine.evidence_unchecked(state, [], made.runner.session)
    assert made.run(Do("sorts_checked", row="b", lane="b", state=state)) is True  # and has told the other worker


@windows
def test_clearing_the_caches_clears_every_workers(workers):
    made = workers()
    assert all(made.run(Do("build_patterns", row=row, lane=row)) > 0 for row in "ab")
    made.runner.clear_caches()
    assert [made.run(Do("patterns_cached", row=row, lane=row)) for row in "ab"] == [0, 0]


@windows
def test_quick_work_takes_a_worker_from_a_search_spread_over_them_all(workers):
    """The search's task that started last is stopped and waits its turn again; the quick task doesn't wait
    for it to finish."""
    made = workers()
    searching = [Job(lambda control, n=n: made.run(Do("wait", 60, row="s", lane="search", search=1, arg2=n),
                                                   control, tasks.SEARCH)) for n in range(2)]
    wait_for(lambda: all(w.entry for w in made.runner._workers))
    last = max(made.runner._workers, key=lambda w: w.entry.future.started).entry
    start = time.monotonic()
    assert made.run(Do("pid", row="q"), priority=tasks.QUICK) in made.pids()
    assert time.monotonic() - start < 2
    wait_for(lambda: all(w.entry for w in made.runner._workers))  # the search's task is running again
    assert last in [w.entry for w in made.runner._workers] and not last.future.done()
    assert not any(job.wait(0) for job in searching)
    # An exact try waits, and so does anything while a worker is on something else.
    exact = Job(lambda control: made.run(Do("pid", row="e"), control, tasks.EXACT))
    assert not exact.wait(4 * pool.PATIENCE)
    for job in searching:
        job.cancel()
    assert exact.wait(5) and made.runner._deaths == 0 and len(made.pids()) == 2


@windows
def test_a_search_spread_over_workers_matches_the_apps_own_and_frees_what_they_kept(workers):
    made = workers(3)
    query = parse_query("at least 2 queen")
    for row in (get_target("Flush"), combine([get_target("Pair"), get_target("Flush")]), query):
        assert spread.best_discards(made.runner, REAL, row, Control()) == tasks.find_best(REAL, row)
    for row in "abc":  # each worker has let go of the last search
        assert made.run(Do("search_kept", row=row, lane=row)) is None
    assert made.logged == []


ORPHANS = """
import sys, time
from calculatro.pool import ProcessRunner
runner = ProcessRunner(job={job}, orphan_exit={orphan_exit})
runner.resize(2)
assert runner.wait_ready()
print(*(worker.process.pid for worker in runner._workers), flush=True)
time.sleep(60)
"""


@windows
@pytest.mark.parametrize(("job", "orphan_exit"), [(False, True), (True, False)], ids=["pipe", "job"])
def test_workers_end_when_the_app_is_killed(job, orphan_exit):
    """By each of the two ways alone: the closed pipe, and the job object."""
    app = subprocess.Popen([sys.executable, "-c", ORPHANS.format(job=job, orphan_exit=orphan_exit)],
                           stdout=subprocess.PIPE, text=True)
    try:
        ended = [process.exited(int(pid)) for pid in app.stdout.readline().split()]
        assert len(ended) == 2 and not any(done() for done in ended)
    finally:
        app.kill()
        app.wait()
    wait_for(lambda: all(done() for done in ended), 5)


# --- The app with worker processes -----------------------------------------------------------------------

@windows
def test_a_report_and_a_search_from_workers_match_the_apps_own(workers):
    made = workers()
    targets, queries = cli.tracked(REAL)
    want = build_report(REAL, targets, queries, Control())
    assert build_report(REAL, targets, queries, Control(), runner=made.runner) == want
    assert want.targets[1].keep == Fraction(1)  # Pair, held


@windows
def test_the_app_answers_from_workers_sized_by_the_setting(workers):
    """The loop as `main` runs it: each state's "Cores used" resizes the pool, changing it keeps the finished
    rows, and stopping ends the workers."""
    made = workers(1)
    calls: list[int] = []
    asked = replace(REAL, id="s:1", search=("Flush", 1), cores=2)
    steps = [asked, replace(asked, id="s:2", cores=3), replace(asked, id="s:3", cores=1)]
    source, answers, sizes = FakeSource(steps[0]), [], []

    def panel(payload):
        done = not payload.get("pending") and not payload.get("partial") and payload.get("search")
        if done and payload["id"] not in [a["id"] for a in answers]:
            answers.append(payload)
            sizes.append(len(made.pids()))
            if len(answers) < len(steps):
                source.queue.append(steps[len(answers)])

    end = time.monotonic() + 30
    cli.run_watch(source, panel, poll_interval=0.005, runner=made.runner,
                  cores=lambda setting, limit: calls.append(setting) or setting,
                  stop=lambda: len(answers) == len(steps) or time.monotonic() > end)
    assert [a["id"] for a in answers] == ["s:1", "s:2", "s:3"] and calls == [2, 3, 1]
    assert sizes[:2] == [2, 3]
    assert answers[0]["targets"] == answers[1]["targets"] == answers[2]["targets"]
    assert answers[0]["search"]["options"] and answers[0]["search"] == answers[2]["search"]
    assert not made.pids() and made.logged == []  # closed with the loop


def test_cores_is_part_of_neither_basis():
    """More cores change no odds: the finished rows and the search are kept."""
    assert cli.odds_basis(replace(REAL, cores=4)) == cli.odds_basis(REAL)
    assert cli.search_basis(replace(REAL, cores=4)) == cli.search_basis(REAL)
