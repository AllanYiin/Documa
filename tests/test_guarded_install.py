"""Guarded installer and MCP install-lifecycle tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

from documa import install
from documa.interfaces import mcp_lifecycle
from documa.interfaces.mcp_lifecycle import disconnect_mcp_servers, guarded_install


_WATCHED_CHILD = (
    "import time\n"
    "from documa.interfaces.mcp_lifecycle import start_install_shutdown_watchdog\n"
    "registration = start_install_shutdown_watchdog(poll_seconds=0.05)\n"
    "print('ready' if registration else 'blocked', flush=True)\n"
    "time.sleep(30)\n"
)

_UNRESPONSIVE_CHILD = (
    "import time\n"
    "from documa.interfaces.mcp_lifecycle import _register_process, _runtime_dir\n"
    "registration = _register_process(_runtime_dir())\n"
    "print('ready', flush=True)\n"
    "time.sleep(30)\n"
)


def _spawn_child(script: str, runtime_dir: str) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env["DOCUMA_MCP_RUNTIME_DIR"] = runtime_dir
    return subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


def _close_child(child: subprocess.Popen[str]) -> None:
    if child.poll() is None:
        child.kill()
    child.wait(timeout=10)
    assert child.stdout is not None
    assert child.stderr is not None
    child.stdout.close()
    child.stderr.close()


def test_guarded_installer_requests_clean_mcp_shutdown(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCUMA_MCP_RUNTIME_DIR", str(tmp_path))
    child = _spawn_child(_WATCHED_CHILD, str(tmp_path))
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "ready"
        with guarded_install():
            report = disconnect_mcp_servers(
                grace_seconds=2,
                force_seconds=1,
                include_legacy_windows=False,
            )
        child.wait(timeout=10)
        assert child.returncode == 0
        assert report["detected_registered_pids"] == [child.pid]
        assert report["forced_registered_pids"] == []
        assert report["disconnected"] is True
    finally:
        _close_child(child)


def test_guarded_installer_forces_unresponsive_registered_server(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCUMA_MCP_RUNTIME_DIR", str(tmp_path))
    child = _spawn_child(_UNRESPONSIVE_CHILD, str(tmp_path))
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "ready"
        with guarded_install():
            report = disconnect_mcp_servers(
                grace_seconds=0.1,
                force_seconds=2,
                include_legacy_windows=False,
            )
        child.wait(timeout=10)
        assert report["detected_registered_pids"] == [child.pid]
        assert report["forced_registered_pids"] == [child.pid]
        assert report["disconnected"] is True
    finally:
        _close_child(child)


def test_disconnect_forces_detected_legacy_windows_launcher(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCUMA_MCP_RUNTIME_DIR", str(tmp_path))
    with (
        patch.object(mcp_lifecycle, "_legacy_windows_pids", side_effect=[[321], []]),
        patch.object(mcp_lifecycle, "_force_legacy_windows_servers") as force_legacy,
    ):
        with guarded_install():
            report = disconnect_mcp_servers(grace_seconds=0, force_seconds=0)

    force_legacy.assert_called_once_with([321])
    assert report["detected_legacy_windows_pids"] == [321]
    assert report["remaining_legacy_windows_pids"] == []
    assert report["disconnected"] is True


def test_install_runs_pip_only_after_disconnect(capsys):
    completed = SimpleNamespace(returncode=0)
    shutdown = {
        "detected_registered_pids": [123],
        "detected_legacy_windows_pids": [],
        "forced_registered_pids": [],
        "remaining_registered_pids": [],
        "remaining_legacy_windows_pids": [],
        "disconnected": True,
    }
    with (
        patch.object(install, "guarded_install", return_value=nullcontext()),
        patch.object(install, "disconnect_mcp_servers", return_value=shutdown),
        patch.object(install.subprocess, "run", return_value=completed) as run,
    ):
        result = install.main(["--upgrade", "--force-reinstall", "documa==9.9.9"])

    assert result == 0
    run.assert_called_once_with(
        [sys.executable, "-m", "pip", "install", "--upgrade", "--force-reinstall", "documa==9.9.9"],
        check=False,
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["mcp"] == shutdown


def test_install_aborts_before_pip_if_disconnect_fails(capsys):
    shutdown = {
        "detected_registered_pids": [123],
        "detected_legacy_windows_pids": [],
        "forced_registered_pids": [123],
        "remaining_registered_pids": [123],
        "remaining_legacy_windows_pids": [],
        "disconnected": False,
    }
    with (
        patch.object(install, "guarded_install", return_value=nullcontext()),
        patch.object(install, "disconnect_mcp_servers", return_value=shutdown),
        patch.object(install.subprocess, "run") as run,
    ):
        result = install.main(["documa"])

    assert result == 2
    run.assert_not_called()
    payload = json.loads(capsys.readouterr().err)
    assert payload["stage"] == "mcp_disconnect"
