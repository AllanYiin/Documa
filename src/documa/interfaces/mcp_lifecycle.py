"""Coordinate MCP server lifetime with in-place Documa upgrades."""

from __future__ import annotations

import csv
import json
import os
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from filelock import FileLock, Timeout


_RUNTIME_DIR_ENV = "DOCUMA_MCP_RUNTIME_DIR"
_INSTALL_LOCK = "install.lock"
_SHUTDOWN_REQUEST = "shutdown-request.json"


def _runtime_dir() -> Path:
    configured = os.environ.get(_RUNTIME_DIR_ENV)
    root = Path(configured) if configured else Path(tempfile.gettempdir()) / "documa-mcp"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _read_shutdown_token(root: Path) -> str | None:
    try:
        payload = json.loads((root / _SHUTDOWN_REQUEST).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return None
    token = payload.get("token")
    return token if isinstance(token, str) else None


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


@dataclass
class MCPProcessRegistration:
    pid: int
    marker_path: Path
    lock: FileLock

    def close(self) -> None:
        try:
            self.marker_path.unlink(missing_ok=True)
        finally:
            if self.lock.is_locked:
                self.lock.release()
            Path(self.lock.lock_file).unlink(missing_ok=True)


def _register_process(root: Path) -> MCPProcessRegistration:
    pid = os.getpid()
    marker = root / f"server-{pid}-{uuid.uuid4().hex}.json"
    lock = FileLock(f"{marker}.lock")
    lock.acquire()
    _write_json_atomic(
        marker,
        {
            "pid": pid,
            "server_id": marker.stem,
            "started_at_unix": time.time(),
        },
    )
    return MCPProcessRegistration(pid=pid, marker_path=marker, lock=lock)


def start_install_shutdown_watchdog(poll_seconds: float = 0.25) -> MCPProcessRegistration | None:
    """Register this server and stop it when a guarded install requests shutdown.

    Registration and watchdog startup happen while briefly holding the install
    gate, closing the race where an installer could request shutdown between the
    server's preflight check and its watchdog becoming active. None means an
    install already owns the gate and this server must not start.
    """

    root = _runtime_dir()
    install_lock = FileLock(str(root / _INSTALL_LOCK))
    try:
        install_lock.acquire(timeout=0)
    except Timeout:
        return None

    try:
        initial_token = _read_shutdown_token(root)
        registration = _register_process(root)

        def watch() -> None:
            while _read_shutdown_token(root) == initial_token:
                time.sleep(poll_seconds)
            os._exit(0)

        threading.Thread(target=watch, daemon=True, name="documa-mcp-install-watchdog").start()
        return registration
    finally:
        install_lock.release()


@contextmanager
def guarded_install() -> Iterator[None]:
    """Prevent MCP servers from starting for the duration of an install."""

    lock = FileLock(str(_runtime_dir() / _INSTALL_LOCK))
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


def _active_registrations(root: Path) -> list[tuple[int, Path]]:
    active: list[tuple[int, Path]] = []
    for marker in root.glob("server-*.json"):
        marker_lock = FileLock(f"{marker}.lock")
        try:
            marker_lock.acquire(timeout=0)
        except Timeout:
            try:
                payload = json.loads(marker.read_text(encoding="utf-8"))
                pid = int(payload["pid"])
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                continue
            active.append((pid, marker))
            continue

        marker_lock.release()
        marker.unlink(missing_ok=True)
        Path(marker_lock.lock_file).unlink(missing_ok=True)
    return active


def _request_shutdown(root: Path) -> None:
    _write_json_atomic(
        root / _SHUTDOWN_REQUEST,
        {"token": uuid.uuid4().hex, "requested_at_unix": time.time()},
    )


def _wait_for_registered_exit(root: Path, timeout_seconds: float) -> list[tuple[int, Path]]:
    deadline = time.monotonic() + timeout_seconds
    active = _active_registrations(root)
    while active and time.monotonic() < deadline:
        time.sleep(0.05)
        active = _active_registrations(root)
    return active


def _force_registered_processes(active: list[tuple[int, Path]]) -> list[int]:
    forced: list[int] = []
    for pid, _ in active:
        if pid == os.getpid():
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            continue
        forced.append(pid)
    return forced


def _legacy_windows_pids() -> list[int]:
    if os.name != "nt":
        return []
    completed = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq documa-mcp.exe", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return []

    pids: list[int] = []
    for row in csv.reader(completed.stdout.splitlines()):
        if len(row) < 2 or row[0].casefold() != "documa-mcp.exe":
            continue
        try:
            pids.append(int(row[1]))
        except ValueError:
            continue
    return pids


def _force_legacy_windows_servers(pids: list[int]) -> None:
    for pid in pids:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
            text=True,
            check=False,
        )


def disconnect_mcp_servers(
    *,
    grace_seconds: float = 2.0,
    force_seconds: float = 2.0,
    include_legacy_windows: bool = True,
) -> dict[str, object]:
    """Disconnect all detected MCP servers, forcing stragglers after grace."""

    root = _runtime_dir()
    registered_before = _active_registrations(root)
    legacy_before = _legacy_windows_pids() if include_legacy_windows else []
    _request_shutdown(root)

    remaining = _wait_for_registered_exit(root, grace_seconds)
    forced_registered = _force_registered_processes(remaining)
    if legacy_before:
        _force_legacy_windows_servers(legacy_before)

    remaining = _wait_for_registered_exit(root, force_seconds)
    legacy_remaining = _legacy_windows_pids() if include_legacy_windows else []
    return {
        "detected_registered_pids": [pid for pid, _ in registered_before],
        "detected_legacy_windows_pids": legacy_before,
        "forced_registered_pids": forced_registered,
        "remaining_registered_pids": [pid for pid, _ in remaining],
        "remaining_legacy_windows_pids": legacy_remaining,
        "disconnected": not remaining and not legacy_remaining,
    }
