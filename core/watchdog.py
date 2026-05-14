"""
core/watchdog.py
Runs as a daemon loop. Monitors service health, auto-restarts failures,
opens alert panes in tmux when critical services degrade.
"""

from __future__ import annotations

import logging
import time
import signal
import sys
from collections import defaultdict
from datetime import datetime

from .orchestrator import Orchestrator, Status
from .registry import Registry, Service
from . import tmux

log = logging.getLogger("flam.watchdog")

ALERT_SESSION = "FlamWatchdog"


class Watchdog:
    def __init__(self, orchestrator: Orchestrator, registry: Registry):
        self.orc = orchestrator
        self.registry = registry
        self._running = False
        self._restart_counts: dict[str, int] = defaultdict(int)
        self._last_restart: dict[str, float] = {}
        self._degraded_since: dict[str, float] = {}

    def start(self) -> None:
        """Block and run the watchdog loop."""
        interval = self.registry.defaults.watchdog_interval
        restart_limit = self.registry.defaults.restart_limit
        cooldown = self.registry.defaults.restart_cooldown

        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        self._running = True
        log.info(f"Watchdog started — interval={interval}s, restart_limit={restart_limit}")
        print(f"[FlamWatchdog] 👁  Monitoring {len(self.registry.services)} services every {interval}s")
        print(f"[FlamWatchdog] Press Ctrl-C to stop\n")

        while self._running:
            self._tick(restart_limit, cooldown)
            time.sleep(interval)

    def _tick(self, restart_limit: int, cooldown: int) -> None:
        now = time.time()

        for name, svc in self.registry.services.items():
            states = self.orc.status(name)
            state = states[name]

            if state.status == Status.RUNNING:
                # Clear degraded tracking if recovered
                self._degraded_since.pop(name, None)
                self._restart_counts[name] = 0
                continue

            if state.status == Status.STOPPED:
                continue  # Intentionally stopped

            # Degraded or unknown — attempt recovery
            if name not in self._degraded_since:
                self._degraded_since[name] = now
                log.warning(f"[{name}] first degraded signal")

            last_restart = self._last_restart.get(name, 0)
            if now - last_restart < cooldown:
                continue  # In cooldown

            count = self._restart_counts[name]

            if count >= restart_limit:
                # Service has exceeded restart limit — open alert pane once
                alert_key = f"alert_opened_{name}"
                if not getattr(self, alert_key, False):
                    self._open_alert(svc)
                    setattr(self, alert_key, True)
                    log.error(f"[{name}] restart limit reached — ALERT PANE OPENED")
                continue

            log.warning(f"[{name}] degraded — restarting (attempt {count + 1}/{restart_limit})")
            print(f"  ⚡ [{name}] restart #{count + 1} @ {datetime.now().strftime('%H:%M:%S')}")
            self.orc.restart(name)
            self._restart_counts[name] += 1
            self._last_restart[name] = now

    def _open_alert(self, svc: Service) -> None:
        """
        Open a panic window in FlamWatchdog session with live logs/status.
        """
        if not tmux.session_exists(ALERT_SESSION):
            tmux.new_session(
                name=ALERT_SESSION,
                command="bash",
                working_dir="/",
            )

        alert_name = f"ALERT-{svc.name}"
        cmd = f"echo '🚨 {svc.name.upper()} has exceeded restart limit.'; "

        if svc.log_file:
            cmd += f"tail -f {svc.log_file}"
        else:
            cmd += f"watch -n2 '{svc.health_check}; echo exit: $?'"

        tmux.open_alert_pane(ALERT_SESSION, alert_name, cmd)

        # Also print to terminal
        print(f"\n  🚨 ALERT: [{svc.name}] exceeded restart limit. Alert pane opened in '{ALERT_SESSION}'")
        print(f"  Run: flam jump watchdog\n")

    def _handle_signal(self, sig, frame) -> None:
        print(f"\n[FlamWatchdog] Signal {sig} received — shutting down")
        self._running = False
        sys.exit(0)
