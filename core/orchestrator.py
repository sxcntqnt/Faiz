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

HEALTH_GATE_TIMEOUT = 30   # seconds to wait per service in ordered boot
HEALTH_GATE_POLL    = 300    # seconds between health check attempts
POST_LAUNCH_DELAY   = 20  # seconds to wait before confirming session is alive


class Status(str, Enum):
    RUNNING  = "running"
    STOPPED  = "stopped"
    DEGRADED = "degraded"
    UNKNOWN  = "unknown"


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
        """Start one service (or all, unordered). For ordered group boot use up_ordered()."""
        targets = [self.registry.get(name)] if name else self.registry.all()
        for svc in targets:
            self._start(svc)

    def up_ordered(self, names: list[str], timeout: int = HEALTH_GATE_TIMEOUT) -> None:
        """
        Start services in dependency order with health-check gates between steps.

        Boot sequence:
          - Topologically sorts names by depends_on
          - Starts each service in order
          - After starting a service that has downstream dependents still to boot,
            waits until its health check passes before continuing
          - Halts the chain if any health gate times out

        Example output:
          [1/5] vault           started ✓
          [2/5] dgraph-zero     started ✓
          [3/5] dgraph-alpha    → healthy ✓
          [4/5] auth-bootstrap  → healthy ✓
          [5/5] auth-service    started ✓
        """
        try:
            ordered = self._topo_sort(names)
        except ValueError as e:
            print(f"[FlamOS] ✗ {e}")
            return

        total = len(ordered)
        name_set = set(names)

        for i, name in enumerate(ordered, 1):
            svc = self.registry.services[name]
            prefix = f"  [{i}/{total}] {name:<26}"

            if tmux.session_exists(svc.session):
                print(f"{prefix} already running ✓")
                self._state[name].status = Status.RUNNING
                continue

            print(f"{prefix}", end="", flush=True)
            self._start(svc)

            # Health gate only if later services depend on this one
            has_downstream = any(
                dep == name
                for later_name in ordered[i:]
                for dep in self.registry.services[later_name].depends_on
                if dep in name_set
            )

            if has_downstream:
                print(" → waiting...", end="", flush=True)
                healthy = self._wait_healthy(svc, timeout=timeout)
                if healthy:
                    print(" healthy ✓")
                else:
                    print(f" timed out ✗")
                    log.error(f"[{name}] health gate failed after {timeout}s")
                    print(f"\n  [FlamOS] ✗ Ordered boot halted at '{name}'")
                    print(f"  [FlamOS]   Check: {svc.health_check}")
                    if i < total:
                        print(f"  [FlamOS]   Resume with: flam up {ordered[i]}")
                    return
            else:
                print(" started ✓")

    def down(self, name: str | None = None) -> None:
        """Stop one or all services."""
        targets = [self.registry.get(name)] if name else self.registry.all()
        for svc in targets:
            self._stop(svc)

    def down_ordered(self, names: list[str]) -> None:
        """
        Stop services in reverse dependency order (dependents first, then their deps).
        Safe teardown that mirrors up_ordered.
        """
        try:
            ordered = self._topo_sort(names)
        except ValueError as e:
            print(f"[FlamOS] ✗ {e}")
            return

        total = len(ordered)
        for i, name in enumerate(reversed(ordered), 1):
            svc = self.registry.services[name]
            print(f"  [{i}/{total}] {name:<26}", end="", flush=True)
            if not tmux.session_exists(svc.session):
                print(" already stopped ·")
                self._state[name].status = Status.STOPPED
            else:
                self._stop(svc)
                print(" stopped ✓")

    def restart(self, name: str | None = None) -> None:
        """Restart one or all services (unordered)."""
        targets = [self.registry.get(name)] if name else self.registry.all()
        for svc in targets:
            self._stop(svc)
            time.sleep(1)
            self._start(svc)

    def restart_ordered(self, names: list[str], timeout: int = HEALTH_GATE_TIMEOUT) -> None:
        """Stop in reverse dep order, then boot in dep order with health gates."""
        self.down_ordered(names)
        time.sleep(1)
        self.up_ordered(names, timeout=timeout)

    def status(self, name: str | None = None) -> dict[str, ServiceState]:
        """Return current status of one or all services."""
        targets = [name] if name else list(self.registry.services.keys())
        for n in targets:
            self._refresh_status(self.registry.get(n))
        if name:
            return {name: self._state[name]}
        return dict(self._state)

    def is_healthy(self, name: str) -> bool:
        return self._check_health(self.registry.get(name))

    # ── Dependency graph ──────────────────────────────────────────────────────

    def _topo_sort(self, names: list[str]) -> list[str]:
        """
        Topological sort using Kahn's algorithm.
        Only considers dependencies within the given name set.
        Raises ValueError on cycles.
        """
        name_set = set(names)
        in_degree: dict[str, int] = {n: 0 for n in names}
        dependents: dict[str, list[str]] = {n: [] for n in names}

        for n in names:
            svc = self.registry.services[n]
            for dep in svc.depends_on:
                if dep in name_set:
                    in_degree[n] += 1
                    dependents[dep].append(n)

        # Start with services that have no deps (sorted for determinism)
        queue = sorted(n for n in names if in_degree[n] == 0)
        result: list[str] = []

        while queue:
            node = queue.pop(0)
            result.append(node)
            for dependent in sorted(dependents[node]):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        if len(result) != len(names):
            unresolved = [n for n in names if n not in result]
            raise ValueError(f"Dependency cycle detected among: {unresolved}")

        return result

    # ── Internal ──────────────────────────────────────────────────────────────

    def _start(self, svc: Service) -> None:
        if tmux.session_exists(svc.session):
            log.info(f"[{svc.name}] already running in {svc.session}")
            self._state[svc.name].status = Status.RUNNING
            return

        log.info(f"[{svc.name}] starting → '{svc.session}'")
        ok = tmux.new_session(
            name=svc.session,
            command=svc.resolved_command(),
            working_dir=svc.resolved_dir(),
        )

        if not ok:
            self._state[svc.name].status = Status.DEGRADED
            self._state[svc.name].last_error = "tmux new-session failed"
            log.error(f"[{svc.name}] ✗ tmux new-session returned non-zero")
            print(f"\n  ✗ [{svc.name}] tmux refused to create session")
            return

        # ── Post-launch verification ──────────────────────────────────────────
        # Wait briefly then confirm the session is still alive.
        # If the command exits immediately (bad path, syntax error, missing dep),
        # tmux closes the session and we'd otherwise report success silently.
        time.sleep(POST_LAUNCH_DELAY)

        if not tmux.session_exists(svc.session):
            # Try to grab whatever was printed before the pane closed.
            # This works if tmux hasn't fully reaped the session yet.
            last_output = tmux.capture_pane(svc.session, lines=20)

            error_msg = "command exited immediately"
            self._state[svc.name].status = Status.DEGRADED
            self._state[svc.name].last_error = error_msg
            log.error(f"[{svc.name}] ✗ {error_msg}")

            print(f"\n  ✗ [{svc.name}] session exited immediately")
            print(f"     command: {svc.resolved_command()[:80]}")
            print(f"     dir:     {svc.resolved_dir()}")

            if last_output:
                print(f"     output:")
                for line in last_output.splitlines()[-10:]:
                    print(f"       {line}")
            else:
                print(f"     (no output captured — run manually to see the error)")
                print(f"     $ cd {svc.resolved_dir()} && {svc.resolved_command()[:60]}")

            if svc.depends_on:
                unmet = [
                    dep for dep in svc.depends_on
                    if not tmux.session_exists(
                        self.registry.services[dep].session
                        if dep in self.registry.services else ""
                    )
                ]
                if unmet:
                    print(f"     deps not running: {', '.join(unmet)}")
                    print(f"     → run: flam up {' '.join(unmet)}")
            return

        self._state[svc.name].status = Status.RUNNING
        self._state[svc.name].last_seen = time.time()
        log.info(f"[{svc.name}] ✓ started")

    def _stop(self, svc: Service) -> None:
        if not tmux.session_exists(svc.session):
            log.info(f"[{svc.name}] already stopped")
            self._state[svc.name].status = Status.STOPPED
            return

        log.info(f"[{svc.name}] stopping '{svc.session}'")
        tmux.kill_session(svc.session)
        self._state[svc.name].status = Status.STOPPED
        log.info(f"[{svc.name}] ✓ stopped")


    def _check_health(self, svc: Service) -> bool:
        result = subprocess.run(
            svc.resolved_health_check(),
            shell=True,
            capture_output=True,
            timeout=5,
        )

        return result.returncode == 0

    def _wait_healthy(self, svc: Service, timeout: int = HEALTH_GATE_TIMEOUT) -> bool:
        """Poll health check every HEALTH_GATE_POLL seconds until healthy or timeout."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self._check_health(svc):
                    return True
            except subprocess.TimeoutExpired:
                pass
            time.sleep(HEALTH_GATE_POLL)
        return False

    def _refresh_status(self, svc: Service) -> None:
        state = self._state[svc.name]
        session_alive = tmux.session_exists(svc.session)

        if not session_alive:
            state.status = Status.STOPPED
            return

        try:
            healthy = self._check_health(svc)
            if not healthy:
                time.sleep(0.3)
                healthy = self._check_health(svc)
            if healthy:
                state.status = Status.RUNNING
                state.last_seen = time.time()
            else:
                state.status = Status.DEGRADED
        except subprocess.TimeoutExpired:
            state.status = Status.DEGRADED
            state.last_error = "health check timed out"
