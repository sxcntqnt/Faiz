"""
core/tmux.py
Low-level tmux primitives. All tmux interaction goes through here.
"""

from __future__ import annotations

import subprocess
import shlex
from dataclasses import dataclass


def _run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


# ── Session management ────────────────────────────────────────────────────────

def session_exists(name: str) -> bool:
    result = _run(["tmux", "has-session", "-t", name])
    return result.returncode == 0


def kill_session(name: str) -> None:
    _run(["tmux", "kill-session", "-t", name])


def new_session(name: str, command: str, working_dir: str = "/") -> bool:
    """
    Create a detached tmux session running command in working_dir.
    Returns True on success.
    """
    result = _run([
        "tmux", "new-session",
        "-d",
        "-s", name,
        "-c", working_dir,
        "bash", "-c", command,
    ])
    return result.returncode == 0


def capture_pane(session: str, lines: int = 30) -> str:
    """
    Capture the last N lines of output from a session's active pane.
    Returns empty string if session is gone or capture fails.
    Call immediately after noticing a session has died — tmux clears pane
    history once the session is fully removed.
    """
    result = _run([
        "tmux", "capture-pane",
        "-p",           # print to stdout
        "-t", session,
        "-S", f"-{lines}",  # last N lines
    ])
    return result.stdout.strip() if result.returncode == 0 else ""


def list_sessions() -> list[str]:
    result = _run(["tmux", "list-sessions", "-F", "#S"])
    if result.returncode != 0:
        return []
    return [s.strip() for s in result.stdout.splitlines() if s.strip()]


# ── Window management ─────────────────────────────────────────────────────────

def list_windows() -> list[dict]:
    """
    Returns list of {session, index, name} across all sessions.
    """
    result = _run(["tmux", "list-windows", "-a", "-F", "#S|#I|#W"])
    if result.returncode != 0:
        return []
    windows = []
    for line in result.stdout.splitlines():
        parts = line.split("|")
        if len(parts) == 3:
            windows.append({"session": parts[0], "index": parts[1], "name": parts[2]})
    return windows


def new_window(session: str, name: str) -> None:
    _run(["tmux", "new-window", "-t", session, "-n", name])

def send_keys(target: str, keys: str) -> None:
    _run([
        "tmux",
        "send-keys",
        "-t",
        target,
        keys,
        "Enter",
    ])

# ── Navigation ────────────────────────────────────────────────────────────────

def switch_to(session: str, window_index: str | None = None) -> bool:
    """
    Switch to a session (and optionally a window within it).
    Works inside and outside tmux.
    """
    import os
    in_tmux = bool(os.environ.get("TMUX"))
    target = f"{session}:{window_index}" if window_index else session

    if in_tmux:
        result = _run(["tmux", "switch-client", "-t", target])
    else:
        result = _run(["tmux", "attach-session", "-t", session])

    return result.returncode == 0


def open_alert_pane(session: str, pane_name: str, command: str) -> None:
    """
    Open a new named window in a session and run command in it.
    Used by watchdog for panic / alert panes.
    """
    new_window(session, pane_name)
    windows = list_windows()
    for w in windows:
        if w["session"] == session and w["name"] == pane_name:
            send_keys(f"{session}:{w['index']}", command)
            break


# ── Pane introspection ────────────────────────────────────────────────────────

@dataclass
class PaneSnapshot:
    session: str
    window_index: str
    window_name: str
    pane_index: str
    current_command: str
    current_path: str


def snapshot_panes() -> list[PaneSnapshot]:
    """
    Full snapshot of all panes across all sessions.
    Used by resurrection.
    """
    fmt = "#{session_name}|#{window_index}|#{window_name}|#{pane_index}|#{pane_current_command}|#{pane_current_path}"
    result = _run(["tmux", "list-panes", "-a", "-F", fmt])
    if result.returncode != 0:
        return []

    panes = []
    for line in result.stdout.splitlines():
        parts = line.split("|")
        if len(parts) == 6:
            panes.append(PaneSnapshot(*parts))
    return panes
