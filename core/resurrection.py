"""
core/resurrection.py
Save the entire tmux workspace to JSON and restore it on demand.
"""

from __future__ import annotations

import json
import time
import logging
from datetime import datetime
from pathlib import Path

from .registry import Registry
from . import tmux

log = logging.getLogger("flam.resurrection")


class Resurrection:
    def __init__(self, registry: Registry):
        self.registry = registry
        self._state_path = Path(registry.defaults.state_file)
        self._state_path.parent.mkdir(parents=True, exist_ok=True)

    def save(self) -> Path:
        """
        Snapshot all tmux sessions and panes to JSON.
        """
        panes = tmux.snapshot_panes()
        sessions = tmux.list_sessions()

        # Build session → windows → panes hierarchy
        session_map: dict = {}
        for p in panes:
            s = session_map.setdefault(p.session, {})
            win = s.setdefault(p.window_name, [])
            win.append({
                "window_index": p.window_index,
                "pane_index": p.pane_index,
                "command": p.current_command,
                "path": p.current_path,
            })

        # Enrich with registry metadata where available
        session_to_service = {
            svc.session: name
            for name, svc in self.registry.services.items()
        }

        state = {
            "saved_at": datetime.now().isoformat(),
            "timestamp": time.time(),
            "sessions": [],
        }

        for session_name in sessions:
            entry = {
                "name": session_name,
                "service": session_to_service.get(session_name),
                "windows": [],
            }
            windows = session_map.get(session_name, {})
            for win_name, pane_list in windows.items():
                entry["windows"].append({
                    "name": win_name,
                    "panes": pane_list,
                })
            state["sessions"].append(entry)

        with open(self._state_path, "w") as f:
            json.dump(state, f, indent=2)

        log.info(f"Workspace saved → {self._state_path}")
        return self._state_path

    def restore(self, only_registry: bool = True) -> None:
        """
        Restore workspace from JSON snapshot.

        only_registry=True: only restore sessions that correspond to registered services.
        only_registry=False: restore all saved sessions (best-effort).
        """
        if not self._state_path.exists():
            print("[FlamOS] No saved workspace found. Run: flam save")
            return

        with open(self._state_path) as f:
            state = json.load(f)

        saved_at = state.get("saved_at", "unknown")
        print(f"[FlamOS] Restoring workspace from {saved_at}")

        for session_data in state["sessions"]:
            session_name = session_data["name"]
            service_name = session_data.get("service")

            if only_registry and not service_name:
                log.debug(f"Skipping non-registry session '{session_name}'")
                continue

            if tmux.session_exists(session_name):
                print(f"  ↳ [{session_name}] already running — skipping")
                continue

            # Find the right command and dir from registry (most reliable)
            if service_name and service_name in self.registry.services:
                svc = self.registry.services[service_name]
                command = svc.resolved_command()
                working_dir = svc.resolved_dir()
            else:
                # Fall back to last known pane info
                windows = session_data.get("windows", [])
                first_pane = windows[0]["panes"][0] if windows and windows[0]["panes"] else {}
                command = first_pane.get("command", "bash")
                working_dir = first_pane.get("path", "/")

            ok = tmux.new_session(session_name, command, working_dir)
            status = "✓" if ok else "✗"
            print(f"  {status} [{session_name}] → {command[:60]}")

        print(f"[FlamOS] Resurrection complete.")

    def show(self) -> None:
        """Print the current saved state."""
        if not self._state_path.exists():
            print("[FlamOS] No saved state found.")
            return

        with open(self._state_path) as f:
            state = json.load(f)

        print(f"Workspace snapshot — {state.get('saved_at', 'unknown')}\n")
        for s in state["sessions"]:
            svc = f" ({s['service']})" if s.get("service") else ""
            print(f"  Session: {s['name']}{svc}")
            for w in s.get("windows", []):
                print(f"    Window: {w['name']}")
                for p in w.get("panes", []):
                    print(f"      Pane {p['pane_index']}: {p['command']} @ {p['path']}")
