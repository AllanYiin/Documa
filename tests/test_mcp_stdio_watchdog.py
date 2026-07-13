"""Orphan guard: documa-mcp must exit when the stdio host disappears."""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

_CHILD_SCRIPT = (
    "import sys, time\n"
    "from documa.interfaces.mcp_server import install_stdio_exit_watchdog\n"
    "installed = install_stdio_exit_watchdog(poll_seconds=0.2)\n"
    "print('installed' if installed else 'skipped', flush=True)\n"
    "time.sleep(30)\n"
)


def test_watchdog_exits_when_host_closes_stdin():
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD_SCRIPT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
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
        )
    assert child.stdout.strip() == "skipped"
