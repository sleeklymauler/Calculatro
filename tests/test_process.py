import subprocess
import sys
import uuid

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


def test_game_launch_exits_when_already_running(monkeypatch, tmp_path):
    monkeypatch.setattr(process, "claim_instance", lambda: False)
    assert main(["watch", "--file", str(tmp_path / "state.json"), "--game-pid", "1"]) == 0
    assert not (tmp_path / "calculatro.log").exists()
