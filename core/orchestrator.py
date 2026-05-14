"""
core/orchestrator.py
Start, stop, restart services declared in the registry.
All subprocess management goes through tmux sessions.
"""

from __future__ import annotations

import subprocess
import time
import logging
from dataclasses import dataclass, field
from enum import Enum

from .registry import Registry, Service
from . import tmux

log = logging.getLogger("flam.orchestrator")


class Status(str, Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


@dataclass
class ServiceState:
    name: str
    status: Status = Status.UNKNOWN
    restart_count: int = 0
    last_seen: float = field(default_factory=time.time)
    last_error: str = ""


class Orchestrator:
    def __init__(self, registry: Registry):
        self.registry = registry
        self._state: dict[str, ServiceState] = {
            name: ServiceState(name=name)
            for name in registry.services
        }

    # ── Public interface ──────────────────────────────────────────────────────

    def up(self, name: str | None = None) -> None:
        """Start one or all services."""
        targets = [self.registry.get(name)] if name else self.registry.all()
        for svc in targets:
            self._start(svc)

    def down(self, name: str | None = None) -> None:
        """Stop one or all services."""
        targets = [self.registry.get(name)] if name else self.registry.all()
        for svc in targets:
            self._stop(svc)

    def restart(self, name: str | None = None) -> None:
        """Restart one or all services."""
        targets = [self.registry.get(name)] if name else self.registry.all()
        for svc in targets:
            self._stop(svc)
            time.sleep(1)
            self._start(svc)

    def status(self, name: str | None = None) -> dict[str, ServiceState]:
        """Return current status of one or all services."""
        targets = ([name] if name else list(self.registry.services.keys()))
        for n in targets:
            self._refresh_status(self.registry.get(n))
        if name:
            return {name: self._state[name]}
        return dict(self._state)

    def is_healthy(self, name: str) -> bool:
        svc = self.registry.get(name)
        return self._check_health(svc)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _start(self, svc: Service) -> None:
        if tmux.session_exists(svc.session):
            log.info(f"[{svc.name}] already running in session {svc.session}")
            self._state[svc.name].status = Status.RUNNING
            return

        log.info(f"[{svc.name}] starting → tmux session '{svc.session}'")
        ok = tmux.new_session(
            name=svc.session,
            command=svc.resolved_command(),
            working_dir=svc.resolved_dir(),
        )
        if ok:
            self._state[svc.name].status = Status.RUNNING
            self._state[svc.name].last_seen = time.time()
            log.info(f"[{svc.name}] ✓ started")
        else:
            self._state[svc.name].status = Status.DEGRADED
            self._state[svc.name].last_error = "tmux new-session failed"
            log.error(f"[{svc.name}] ✗ failed to start")

    def _stop(self, svc: Service) -> None:
        if not tmux.session_exists(svc.session):
            log.info(f"[{svc.name}] already stopped")
            self._state[svc.name].status = Status.STOPPED
            return

        log.info(f"[{svc.name}] stopping session '{svc.session}'")
        tmux.kill_session(svc.session)
        self._state[svc.name].status = Status.STOPPED
        log.info(f"[{svc.name}] ✓ stopped")

    def _check_health(self, svc: Service) -> bool:
        result = subprocess.run(
            svc.health_check,
            shell=True,
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0

    def _refresh_status(self, svc: Service) -> None:
        state = self._state[svc.name]
        session_alive = tmux.session_exists(svc.session)

        if not session_alive:
            state.status = Status.STOPPED
            return

        try:
            healthy = self._check_health(svc)
            if healthy:
                state.status = Status.RUNNING
                state.last_seen = time.time()
            else:
                state.status = Status.DEGRADED
        except subprocess.TimeoutExpired:
            state.status = Status.DEGRADED
            state.last_error = "health check timed out"
