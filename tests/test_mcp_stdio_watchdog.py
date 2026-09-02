"""Orphan guard: documa-mcp must exit when the stdio host disappears."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

_CHILD_SCRIPT = (
    "import sys, time\n"
    "from documa.interfaces.mcp_server import install_stdio_exit_watchdog\n"
    "installed = install_stdio_exit_watchdog(poll_seconds=0.2)\n"
    "print('installed' if installed else 'skipped', flush=True)\n"
    "time.sleep(30)\n"
)

_MCP2_STDIN_DIVERSION_SCRIPT = (
    "import os, time\n"
    "from documa.interfaces.mcp_server import install_stdio_exit_watchdog\n"
    "installed = install_stdio_exit_watchdog(poll_seconds=0.2)\n"
    "print('installed' if installed else 'skipped', flush=True)\n"
    "diversion_fd = os.open(os.devnull, os.O_RDONLY)\n"
    "os.dup2(diversion_fd, 0)\n"
    "os.close(diversion_fd)\n"
    "time.sleep(0.7)\n"
    "print('alive', flush=True)\n"
)


def _source_environment() -> dict[str, str]:
    env = dict(os.environ)
    source_root = str(Path(__file__).resolve().parents[1] / "src")
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = source_root if not existing else f"{source_root}{os.pathsep}{existing}"
    return env


def test_watchdog_exits_when_host_closes_stdin():
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD_SCRIPT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env=_source_environment(),
    )
    try:
        first_line = child.stdout.readline().strip()
        assert first_line == "installed", "watchdog must engage when stdin is a pipe"
        # Host goes away: close our end of the transport.
        child.stdin.close()
        deadline = time.monotonic() + 10
        while child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        assert child.poll() is not None, "orphaned server did not exit after stdin closed"
        assert child.returncode == 0
    finally:
        if child.poll() is None:
            child.kill()
        child.stdout.close()


def test_watchdog_declines_without_pipe_stdin():
    # With stdin redirected from a real file (not a pipe), the Windows branch
    # must decline instead of instantly exiting a manual run. On POSIX the
    # poll-based branch installs regardless, so the check is Windows-specific.
    if sys.platform != "win32":
        pytest.skip("pipe-type guard is Windows-specific")
    import tempfile

    with tempfile.TemporaryFile() as handle:
        child = subprocess.run(
            [sys.executable, "-c", _CHILD_SCRIPT.replace("time.sleep(30)", "")],
            stdin=handle,
            stdout=subprocess.PIPE,
            text=True,
            timeout=30,
            env=_source_environment(),
        )
    assert child.stdout.strip() == "skipped"


def test_watchdog_survives_mcp2_stdin_fd_diversion():
    if sys.platform != "win32":
        pytest.skip("MCP 2.x fd-0 diversion regression is Windows-specific")

    child = subprocess.Popen(
        [sys.executable, "-c", _MCP2_STDIN_DIVERSION_SCRIPT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env=_source_environment(),
    )
    try:
        assert child.stdout.readline().strip() == "installed"
        assert child.stdout.readline().strip() == "alive"
        assert child.wait(timeout=5) == 0
    finally:
        if child.poll() is None:
            child.kill()
        child.stdin.close()
        child.stdout.close()
