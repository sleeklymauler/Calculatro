import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

import calculatro.__main__ as cli
from calculatro import process
from calculatro.__main__ import main
from calculatro.paths import log_path, odds_path


def test_exited_follows_the_process():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        done = process.exited(child.pid)
        assert not done()
    finally:
        child.kill()
        child.wait()
    assert done()


def test_exited_for_a_missing_process():
    assert process.exited(0xFFFFFFF0)()


def test_memory_limit_is_the_setting_up_to_half_the_computers():
    gb = process.GB
    assert process.memory_limit(None, 32 * gb) == 2 * gb  # the default
    assert process.memory_limit(8, 32 * gb) == 8 * gb
    assert process.memory_limit(8, 16 * gb) == 8 * gb
    assert process.memory_limit(8, 8 * gb) == 4 * gb
    assert process.memory_limit(None, 3 * gb) == 3 * gb // 2
    assert process.memory_limit(4, None) == 4 * gb  # the computer's memory unknown


def test_installed_memory_is_read():
    installed = process.installed_memory()
    assert installed is not None and installed >= process.GB
    assert process.memory_limit(1024) == installed // 2  # read when not given


def test_one_instance():
    name = f"Local\\CalculatroTest-{uuid.uuid4()}"
    assert process.claim_instance(name)
    assert not process.claim_instance(name)


def test_instance_claimed_again_once_the_first_exits():
    """One that wasn't first doesn't keep the name held after the first exits."""
    name = f"Local\\CalculatroTest-{uuid.uuid4()}"
    assert process.claim_instance(name)
    assert not process.claim_instance(name)
    process._kernel32().CloseHandle(process._mutexes.pop())  # as if the first exited
    assert process.claim_instance(name)


def test_instance_name_is_per_state_file(tmp_path, monkeypatch):
    """Another file's instance can't stop the game's app; the same file's can, however it's spelled."""
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "state.json"
    name = process.instance_name(path)
    assert name != process.instance_name(tmp_path / "other.json")
    assert name == process.instance_name(Path("state.json"))
    assert name == process.instance_name(Path(str(path).upper()))


def test_instances_of_different_state_files_both_run(tmp_path):
    """Names made from paths are ones Windows accepts: `claim_instance` returns
    True for one it rejects, so only the refusal shows the name really works."""
    tag = uuid.uuid4().hex
    path, other = tmp_path / f"{tag}.json", tmp_path / f"{tag}-other.json"
    assert process.claim_instance(process.instance_name(path))
    assert process.claim_instance(process.instance_name(other))
    assert not process.claim_instance(process.instance_name(path))


def test_manual_start_writes_the_panel(monkeypatch, tmp_path):
    """`watch` started by hand: the panel callback it runs with writes the odds file."""
    monkeypatch.setattr(process, "claim_instance", lambda name: True)
    calls = []
    monkeypatch.setattr(cli, "run_watch", lambda source, panel, stop, **_: calls.append(panel))
    assert main(["watch", "--file", str(tmp_path / "state.json")]) == 0
    assert calls[0]({"schema": 1, "id": "s:1", "pending": True})
    assert odds_path(tmp_path / "state.json").exists()


def test_manual_start_beside_another_says_so_and_exits(monkeypatch, tmp_path, capsys):
    """Two instances writing the odds file would clash, so a second one started by hand doesn't run."""
    monkeypatch.setattr(process, "claim_instance", lambda name: False)
    monkeypatch.setattr(cli, "run_watch", lambda *args, **kwargs: pytest.fail("watched beside another"))
    assert main(["watch", "--file", str(tmp_path / "state.json")]) == 1
    assert cli.ALREADY_RUNNING in capsys.readouterr().err
    assert not odds_path(tmp_path / "state.json").exists()


def test_game_launch_exits_when_already_running(monkeypatch, tmp_path):
    monkeypatch.setattr(process, "claim_instance", lambda name: False)
    assert main(["watch", "--file", str(tmp_path / "state.json"), "--game-pid", "1"]) == 0
    assert not log_path(tmp_path / "state.json").exists()


def test_low_priority_is_set_and_handed_to_child_processes():
    """In a child, so the tests' own process keeps its priority. The workers the app starts rely on Windows
    handing the class on (docs/plans/parallelism.md)."""
    read = "from calculatro import process; print(process.priority_class())"
    code = ("import subprocess, sys; from calculatro import process; "
            "print(process.set_low_priority(), process.priority_class(), flush=True); "
            f"subprocess.run([sys.executable, '-c', {read!r}])")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.split()
    assert out == ["True", str(process.BELOW_NORMAL_PRIORITY_CLASS), str(process.BELOW_NORMAL_PRIORITY_CLASS)]


def test_only_a_game_launch_lowers_the_priority(monkeypatch, tmp_path):
    """One started by hand keeps the priority it was given."""
    monkeypatch.setattr(process, "claim_instance", lambda name: True)
    monkeypatch.setattr(cli, "run_watch", lambda *args, **kwargs: None)
    lowered = []
    monkeypatch.setattr(process, "set_low_priority", lambda: lowered.append(True) or True)
    monkeypatch.setattr(sys, "stdout", sys.stdout)  # main replaces both for the game
    monkeypatch.setattr(sys, "stderr", sys.stderr)
    assert main(["watch", "--file", str(tmp_path / "state.json")]) == 0
    assert not lowered
    assert main(["watch", "--file", str(tmp_path / "state.json"), "--game-pid", "1"]) == 0
    sys.stdout.close()
    sys.stderr.close()  # the log, so tmp_path can be removed
    assert lowered == [True]


@pytest.mark.parametrize(("mask", "cores"), [(1, 1), (3, 2), (0b10110, 3)])
def test_usable_cores_are_those_in_the_affinity_mask(mask, cores):
    """As under `start /affinity`: a child held to some cores counts only those, where `os.cpu_count()` (and
    `os.process_cpu_count()`, on Windows) still counts the computer's."""
    if mask.bit_length() > (os.cpu_count() or 1):
        pytest.skip("not that many cores here")
    code = ("import ctypes, os; from calculatro import process; k = process._kernel32(); "
            "k.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]; "
            f"assert k.SetProcessAffinityMask(k.GetCurrentProcess(), {mask}); "
            "print(process.usable_cores(), os.cpu_count())")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.split()
    assert out == [str(cores), str(os.cpu_count())]
    assert process.usable_cores() >= cores  # this process, not held to fewer


@pytest.mark.parametrize(("usable", "by_cores"), [(1, 1), (2, 1), (4, 1), (7, 1), (8, 2), (11, 2), (12, 3), (16, 4),
                                                  (64, 4)])
def test_auto_uses_a_quarter_of_the_cores_up_to_four_and_what_the_memory_limit_holds(usable, by_cores):
    gb = process.GB
    assert process.auto_cores(usable, 8 * gb) == by_cores
    assert process.auto_cores(usable, 2 * gb) == by_cores  # the default limit holds 4
    assert process.auto_cores(usable, gb) == min(by_cores, 2)
    assert process.auto_cores(usable, gb // 2) == 1  # 1 GB on a computer with 1: half of it
    assert process.cores_used(0, 2 * gb, usable) == by_cores


@pytest.mark.parametrize(("setting", "usable", "used"), [
    (1, 16, 1), (2, 16, 2), (8, 16, 8), (9, 16, 8), (8, 4, 3), (4, 2, 1), (2, 1, 1)])
def test_the_cores_setting_leaves_a_core_free(setting, usable, used):
    """Whatever the memory limit: the player chose. The mod offers no more than the computer's cores less one
    (cores.lua), but the game, and so the app, may be held to fewer."""
    assert process.cores_used(setting, process.GB, usable) == used


def test_cores_used_reads_the_usable_cores_when_not_given():
    assert process.cores_used(64, process.GB) == max(1, min(process.CORES_MOST, process.usable_cores() - 1))


# The interpreter itself: in a venv `sys.executable` is a launcher, whose process only waits for the real one.
PYTHON = getattr(sys, "_base_executable", None) or sys.executable


def test_memory_used_reads_another_process_by_its_handle():
    child = subprocess.Popen([PYTHON, "-c", "import time; held = bytearray(50 * 2**20); time.sleep(60)"])
    try:
        end = time.monotonic() + 10
        while process.memory_used(int(child._handle)) < 50 * 2**20:
            assert time.monotonic() < end
            time.sleep(0.01)
        assert process.memory_used() > 0
    finally:
        child.kill()
        child.wait()
    assert process.memory_used(0xFFFFFFF0) == 0  # no such handle


def test_a_job_ends_its_processes_when_its_last_handle_closes():
    """As Windows closes the app's handles when it exits, however it does."""
    job = process.kill_on_close_job()
    assert job
    child = subprocess.Popen([PYTHON, "-c", "import time; time.sleep(60)"])
    try:
        assert process.add_to_job(job, int(child._handle))
        assert child.poll() is None
        process._kernel32().CloseHandle(job)
        assert child.wait(5) is not None
    finally:
        child.kill()
        child.wait()
    assert not process.add_to_job(0xFFFFFFF0, 0xFFFFFFF0)
