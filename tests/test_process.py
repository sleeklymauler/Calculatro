import subprocess
import sys
import uuid

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


def test_one_instance():
    name = f"Local\\CalculatroTest-{uuid.uuid4()}"
    assert process.claim_instance(name)
    assert not process.claim_instance(name)


def test_instance_claimed_again_once_the_first_exits():
    """One that wasn't first doesn't keep the name held after the first exits."""
    name = f"Local\\CalculatroTest-{uuid.uuid4()}"
    assert process.claim_instance(name)
    assert not process.claim_instance(name)
    process._kernel32.CloseHandle(process._mutexes.pop())  # as if the first exited
    assert process.claim_instance(name)


def run_main(monkeypatch, tmp_path, first: bool):
    """`watch` started by hand: the panel callback it would run with, and its console output."""
    monkeypatch.setattr(process, "claim_instance", lambda: first)
    calls = []
    monkeypatch.setattr(cli, "run_watch", lambda source, write, panel, stop: calls.append(panel))
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
    monkeypatch.setattr(process, "claim_instance", lambda: False)
    assert main(["watch", "--file", str(tmp_path / "state.json"), "--game-pid", "1"]) == 0
    assert not (tmp_path / "calculatro.log").exists()
