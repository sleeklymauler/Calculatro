"""The log kept when the game starts the app."""

import io
import json
import re
import sys
import tomllib
from pathlib import Path

import pytest

import calculatro
import calculatro.__main__ as cli
from calculatro import log, process

ROOT = Path(__file__).parent.parent


def add(path: Path, text: str) -> None:
    """A session writing `text` to the log."""
    with log.open_log(path) as f:
        f.write(text)


def test_sessions_add_to_the_log(tmp_path):
    path = tmp_path / "calculatro.log"
    add(path, "first\n")
    add(path, "second\n")
    assert path.read_text(encoding="utf-8") == "first\nsecond\n"
    assert not (tmp_path / "calculatro.log.1").exists()


def test_a_full_log_moves_aside(tmp_path):
    path, old = tmp_path / "calculatro.log", tmp_path / "calculatro.log.1"
    old.write_text("older", encoding="utf-8")
    full = "x" * (log.MAX_BYTES + 1)
    path.write_text(full, encoding="utf-8")
    add(path, "new\n")
    assert path.read_text(encoding="utf-8") == "new\n"
    assert old.read_text(encoding="utf-8") == full


def test_a_log_at_the_limit_stays(tmp_path):
    path = tmp_path / "calculatro.log"
    path.write_text("x" * log.MAX_BYTES, encoding="utf-8")
    add(path, "new\n")
    assert path.read_text(encoding="utf-8").endswith("xnew\n")
    assert not (tmp_path / "calculatro.log.1").exists()


def test_a_full_log_that_wont_move_keeps_growing(tmp_path):
    """Open elsewhere without sharing deletes, as in some viewers: Windows refuses the rename."""
    path = tmp_path / "calculatro.log"
    path.write_text("x" * (log.MAX_BYTES + 1), encoding="utf-8")
    with open(path, encoding="utf-8"):
        add(path, "new\n")
    assert path.read_text(encoding="utf-8").endswith("xnew\n")
    assert not (tmp_path / "calculatro.log.1").exists()


@pytest.mark.parametrize("written, shown", [
    (r"C:\Users\Some One\AppData\x.py", r"~\AppData\x.py"),
    (r"c:\users\some one\AppData\x.py", r"~\AppData\x.py"),
    ("C:/Users/Some One/AppData/x.py", "~/AppData/x.py"),
    (r"[Errno 13] 'C:\\Users\\Some One\\x.json'", r"[Errno 13] '~\\x.json'"),
    (r"in C:\Users\Some One: no", "in ~: no"),
    (r"C:\Users\Some One", "~"),
    (r"C:\Users\Some Oneself\x.py", r"C:\Users\Some Oneself\x.py"),
    (r"D:\Users\Some One\x.py", r"D:\Users\Some One\x.py"),
])
def test_the_home_folder_is_written_as_a_tilde(monkeypatch, tmp_path, written, shown):
    monkeypatch.setenv("USERPROFILE", r"C:\Users\Some One")
    add(tmp_path / "calculatro.log", written)
    assert (tmp_path / "calculatro.log").read_text(encoding="utf-8") == shown


def test_a_home_at_a_drives_root_isnt_hidden(monkeypatch, tmp_path):
    monkeypatch.setenv("USERPROFILE", "C:\\")
    add(tmp_path / "calculatro.log", r"C:\x.py")
    assert (tmp_path / "calculatro.log").read_text(encoding="utf-8") == r"C:\x.py"


def test_the_header_hides_the_home_folder(monkeypatch, tmp_path):
    home = tmp_path / "Some One"
    monkeypatch.setenv("USERPROFILE", str(home))
    text = start_from_game(monkeypatch, tmp_path, lambda source, write, panel, stop, **_: None, home / "state.json")
    assert str(home) not in text and text.rstrip().endswith(r"watching ~\state.json")


def start_from_game(monkeypatch, tmp_path, run_watch, state: Path | None = None,
                    exited=lambda pid: lambda: False) -> str:
    """`main` as the game starts it, with the game running unless `exited` says not; what's in the log after."""
    state = state or tmp_path / "state.json"
    state.parent.mkdir(exist_ok=True)
    with monkeypatch.context() as patch:
        patch.setattr(sys, "stdout", sys.stdout)  # main replaces them: restored when this ends
        patch.setattr(sys, "stderr", sys.stderr)
        patch.setattr(process, "claim_instance", lambda name: True)
        patch.setattr(process, "exited", exited)
        patch.setattr(cli, "run_watch", run_watch)
        assert cli.main(["watch", "--file", str(state), "--game-pid", "1"]) == 0
        sys.stdout.close()
        sys.stderr.close()
    return (state.parent / "calculatro.log").read_text(encoding="utf-8")


def test_game_start_says_when_the_game_is_missing(monkeypatch, tmp_path):
    text = start_from_game(monkeypatch, tmp_path, lambda source, write, panel, stop, **_: None,
                           exited=lambda pid: lambda: True)
    assert text.splitlines()[-1] == "Game process 1 isn't running or can't be opened: exiting."


def test_game_start_says_nothing_of_a_running_game(monkeypatch, tmp_path):
    text = start_from_game(monkeypatch, tmp_path, lambda source, write, panel, stop, **_: None)
    assert "exiting" not in text


@pytest.mark.parametrize("stderr", [None, "kept"])
def test_game_start_runs_without_a_log(monkeypatch, tmp_path, stderr):
    """The log is only there to diagnose problems: not being able to open it mustn't stop the odds."""
    (tmp_path / "calculatro.log").mkdir()  # can't be opened as a file
    ran = []
    with monkeypatch.context() as patch:
        patch.setattr(sys, "stdout", sys.stdout)
        kept = None if stderr is None else io.StringIO()  # None as under pythonw
        patch.setattr(sys, "stderr", kept)
        patch.setattr(process, "claim_instance", lambda name: True)
        patch.setattr(process, "exited", lambda pid: lambda: True)  # and something to report
        patch.setattr(cli, "run_watch", lambda *args, **kwargs: ran.append(True))
        assert cli.main(["watch", "--file", str(tmp_path / "state.json"), "--game-pid", "1"]) == 0
        assert sys.stderr is kept
        sys.stdout.close()
    assert ran
    assert kept is None or "Game process 1" in kept.getvalue()


def test_game_start_keeps_the_last_sessions_log(monkeypatch, tmp_path):
    monkeypatch.setenv("USERPROFILE", r"C:\Users\Some One")  # tmp_path is usually in the real one

    def fails(source, write, panel, stop, **_):
        print("the traceback", file=sys.stderr)

    start_from_game(monkeypatch, tmp_path, fails)
    lines = start_from_game(monkeypatch, tmp_path, lambda source, write, panel, stop, **_: None).splitlines()
    assert len(lines) == 3 and lines[1] == "the traceback"
    for line in lines[0], lines[2]:
        assert line.startswith(f"=== Calculatro {calculatro.__version__} started ")
        assert "game pid 1," in line and str(tmp_path / "state.json") in line


def test_versions_match():
    """Releases have no package metadata for the log's header to read, so the app states its version too."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    lovely = (ROOT / "mod" / "Calculatro" / "lovely.toml").read_text(encoding="utf-8")
    assert calculatro.__version__ == project == re.search(r'^version\s*=\s*"([^"]+)"', lovely, re.M)[1]
    steamodded = json.loads((ROOT / "mod" / "Calculatro" / "Calculatro.json").read_text(encoding="utf-8"))
    assert steamodded["version"] == project
