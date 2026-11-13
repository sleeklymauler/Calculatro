import subprocess
import sys
import uuid
from pathlib import Path

import calculatro.__main__ as cli
from calculatro import process
from calculatro.__main__ import main


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


def run_main(monkeypatch, tmp_path, first: bool):
    """`watch` started by hand: the panel callback it would run with, and its console output."""
    monkeypatch.setattr(process, "claim_instance", lambda name: first)
    calls = []
    monkeypatch.setattr(cli, "run_watch", lambda source, write, panel, stop, **_: calls.append(panel))
    assert main(["watch", "--file", str(tmp_path / "state.json")]) == 0
    return calls[0]


def test_manual_start_writes_the_panel(monkeypatch, tmp_path):
    panel = run_main(monkeypatch, tmp_path, first=True)
    assert panel({"schema": 1, "id": "s:1", "pending": True})
    assert (tmp_path / "calculatro_odds.json").exists()


def test_manual_start_beside_another_leaves_the_panel_alone(monkeypatch, tmp_path, capsys):
    """Two instances writing the odds file would clash, so a second one only prints."""
    panel = run_main(monkeypatch, tmp_path, first=False)
    panel({"schema": 1, "id": "s:1", "pending": True})
    assert not (tmp_path / "calculatro_odds.json").exists()
    assert cli.ALREADY_RUNNING in capsys.readouterr().out


def test_game_launch_exits_when_already_running(monkeypatch, tmp_path):
    monkeypatch.setattr(process, "claim_instance", lambda name: False)
    assert main(["watch", "--file", str(tmp_path / "state.json"), "--game-pid", "1"]) == 0
    assert not (tmp_path / "calculatro.log").exists()
