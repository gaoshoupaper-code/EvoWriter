"""Real child-process boundaries; these checks run on the Linux deployment host."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="生产候选隔离使用Linux Landlock")


def test_candidate_cannot_read_other_tasks_write_source_or_inherit_secrets(tmp_path):
    own = tmp_path / "own"
    own.mkdir()
    other = tmp_path / "heldout.md"
    other.write_text("heldout evidence", encoding="utf-8")
    source = tmp_path / "framework.py"
    source.write_text("original", encoding="utf-8")
    script = r'''
import json, os, pathlib, socket, sys
from app.platform.isolation.sandbox import restrict_process, runtime_read_paths
own, other, source, inherited = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]), int(sys.argv[4])
restrict_process(read_paths=[*runtime_read_paths(), source], write_paths=[own])
denied = {}
for name, action in {
 "heldout": lambda: other.read_text(),
 "source_write": lambda: source.write_text("changed"),
 "fd": lambda: os.read(inherited, 100),
 "udp": lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM),
 "unix": lambda: socket.socket(socket.AF_UNIX, socket.SOCK_STREAM),
 "internal_connect": lambda: socket.create_connection(("127.0.0.1", 7789), timeout=1),
}.items():
 try:
  action()
  denied[name] = False
 except OSError:
  denied[name] = True
(own / "output.md").write_text("own output")
denied["env"] = "SANDBOX_TEST_SECRET" not in os.environ
denied["source_read"] = source.read_text() == "original"
print(json.dumps(denied))
'''
    env = dict(os.environ, SANDBOX_TEST_SECRET="test-only-secret")
    with other.open("rb") as inherited:
        result = subprocess.run(
            [sys.executable, "-c", script, str(own), str(other), str(source), str(inherited.fileno())],
            capture_output=True, text=True, env=env, pass_fds=(inherited.fileno(),), timeout=30,
        )
    assert result.returncode == 0, result.stderr
    assert all(json.loads(result.stdout.strip()).values())
    assert (own / "output.md").read_text() == "own output"
    assert source.read_text() == "original"


def test_sandbox_fails_closed_when_landlock_unavailable(monkeypatch, tmp_path):
    from app.platform.isolation import sandbox
    monkeypatch.setattr(sandbox, "landlock_abi", lambda: -1)
    with pytest.raises(RuntimeError, match="Landlock"):
        sandbox.restrict_process(read_paths=[tmp_path], write_paths=[])
