#!/usr/bin/env python3
"""
flam — FlamOS Control Plane CLI
─────────────────────────────────────────────────────────────
Usage:
  flam up [service]          Start one or all services
  flam down [service]        Stop one or all services
  flam restart [service]     Restart one or all services
  flam status [service]      Status snapshot — one or all
  flam dash [service]        Live dashboard — one or all
  flam doctor [service]      Health check — one or all
  flam panic [service]       Log panes — one or all critical
  flam watch [service]       Watchdog daemon — one or all
  flam restore [service]     Restore from snapshot — one or all
  flam jump <name>           Fuzzy-navigate to tmux session
  flam save                  Save workspace state
  flam snapshot              Show last saved snapshot
  flam list                  List all registered services
"""

from __future__ import annotations

import argparse
import copy
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
FLAM_ROOT = Path(os.getenv("FLAM_ROOT", Path(__file__).resolve().parent)).resolve()
sys.path.insert(0, str(FLAM_ROOT))

from core.registry import Registry
from core.orchestrator import Orchestrator, Status
from core.watchdog import Watchdog
from core.resurrection import Resurrection
from core import tmux

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_DIR = FLAM_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    handlers=[logging.FileHandler(LOG_DIR / "flam.log")],
)
log = logging.getLogger("flam")

# ── .env loader ───────────────────────────────────────────────────────────────

def _load_env(root: Path) -> None:
    env_file = root / ".env"
    if not env_file.exists():
        return
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

_load_env(FLAM_ROOT)

# ── Shared helpers ────────────────────────────────────────────────────────────

def _status_icon(status: Status) -> str:
    return {
        Status.RUNNING:  "●",
        Status.STOPPED:  "■",
        Status.DEGRADED: "▲",
        Status.UNKNOWN:  "?",
    }.get(status, "?")


def _fmt_time(ts: float | None) -> str:
    if not ts:
        return "—"
    delta = timedelta(seconds=int(time.time() - ts))
    h, rem = divmod(int(delta.total_seconds()), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m ago"
    if m:
        return f"{m}m {s}s ago"
    return f"{s}s ago"


def _resolve_service(name: str | None, registry: Registry) -> str | None:
    """Validate a service name if given; exit with a clear message if unknown."""
    if name is None:
        return None
    if name not in registry.services:
        known = ", ".join(registry.services.keys())
        print(f"[FlamOS] Unknown service '{name}'. Available: {known}", file=sys.stderr)
        sys.exit(1)
    return name


def _filter_registry(registry: Registry, service_name: str) -> Registry:
    """Return a shallow-copied registry containing only the named service."""
    filtered = copy.copy(registry)
    filtered.services = {service_name: registry.services[service_name]}
    return filtered


def _setup() -> tuple[Registry, Orchestrator, Resurrection]:
    registry = Registry(FLAM_ROOT / "config" / "services.yaml")
    orchestrator = Orchestrator(registry)
    resurrection = Resurrection(registry)
    return registry, orchestrator, resurrection


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_up(args, registry, orchestrator, **_):
    name = _resolve_service(args.service, registry)
    print(f"[FlamOS] Starting {name or 'all services'}...")
    orchestrator.up(name)
    print("[FlamOS] Done.")


def cmd_down(args, registry, orchestrator, **_):
    name = _resolve_service(args.service, registry)
    print(f"[FlamOS] Stopping {name or 'all services'}...")
    orchestrator.down(name)
    print("[FlamOS] Done.")


def cmd_restart(args, registry, orchestrator, **_):
    name = _resolve_service(args.service, registry)
    print(f"[FlamOS] Restarting {name or 'all services'}...")
    orchestrator.restart(name)
    print("[FlamOS] Done.")


def cmd_status(args, registry, orchestrator, **_):
    name = _resolve_service(getattr(args, "service", None), registry)
    result = orchestrator.status(name)

    # ── JSON mode ────────────────────────────────────────────────────────────
    if getattr(args, "json", False):
        import json
        out = {
            k: {
                "status": v.status.value,
                "restart_count": v.restart_count,
                "last_seen": _fmt_time(v.last_seen),
                "last_error": v.last_error or None,
            }
            for k, v in result.items()
        }
        print(json.dumps(out, indent=2))
        return

    # ── Single service — focused node view ───────────────────────────────────
    if name:
        state = result[name]
        svc = registry.services[name]
        icon = _status_icon(state.status)
        print(f"""
⚡ FlamOS · {name}

{icon} {state.status.value}
├─ session     {svc.session}  {"●" if tmux.session_exists(svc.session) else "○"}
├─ port        {svc.port or "—"}
├─ critical    {"YES" if svc.critical else "no"}
├─ tags        {", ".join(svc.tags) if svc.tags else "—"}
├─ restarts    {state.restart_count}
├─ last seen   {_fmt_time(state.last_seen)}
└─ last error  {state.last_error or "—"}
""".strip())
        return

    # ── All services — full dashboard snapshot ────────────────────────────────
    from core.dashboard import show_once
    show_once(orchestrator, registry)


def cmd_dash(args, registry, orchestrator, **_):
    name = _resolve_service(getattr(args, "service", None), registry)
    refresh = getattr(args, "refresh", 3.0)

    # ── All services — existing live dashboard ────────────────────────────────
    if not name:
        from core.dashboard import show_live
        show_live(orchestrator, registry, refresh=refresh)
        return

    # ── Single service — focused live panel ───────────────────────────────────
    try:
        from rich.live import Live
        from rich.panel import Panel
        from rich.table import Table
        from rich import box
        from rich.text import Text
    except ImportError:
        raise SystemExit("rich is required: pip install rich")

    svc = registry.services[name]

    def _build_focused() -> Panel:
        orchestrator._refresh_status(svc)
        state = orchestrator._state[name]
        icon = _status_icon(state.status)
        healthy = orchestrator.is_healthy(name)
        session_alive = tmux.session_exists(svc.session)

        t = Table(box=None, show_header=False, padding=(0, 2))
        t.add_column("key",   style="dim", width=14)
        t.add_column("value", style="bold bright_white")

        rows = [
            ("status",     Text(f"{icon}  {state.status.value}", style=(
                "bright_green" if state.status == Status.RUNNING else
                "bright_yellow" if state.status == Status.DEGRADED else
                "dim white"
            ))),
            ("session",    Text(f"{svc.session}  {'●' if session_alive else '○'}", style=(
                "bright_green" if session_alive else "dim"
            ))),
            ("health",     Text("OK" if healthy else "FAIL", style="bright_green" if healthy else "bright_red")),
            ("port",       str(svc.port or "—")),
            ("critical",   Text("YES", style="bright_red bold") if svc.critical else Text("no", style="dim")),
            ("tags",       ", ".join(svc.tags) if svc.tags else "—"),
            ("restarts",   str(state.restart_count) if state.restart_count else "·"),
            ("last seen",  _fmt_time(state.last_seen) if state.status == Status.RUNNING else "—"),
            ("last error", state.last_error or "—"),
        ]

        for key, val in rows:
            t.add_row(key, val if isinstance(val, Text) else Text(str(val)))

        ts = datetime.now().strftime("%H:%M:%S")
        return Panel(
            t,
            title=f"[bold bright_white]⚡ {name}[/]  [dim]{ts}[/]",
            subtitle="[dim]Ctrl-C to quit[/]",
            border_style="bright_cyan" if state.status == Status.RUNNING else "bright_yellow",
            padding=(1, 3),
        )

    try:
        with Live(refresh_per_second=1 / refresh, screen=True) as live:
            while True:
                live.update(_build_focused())
                time.sleep(refresh)
    except KeyboardInterrupt:
        pass


def cmd_jump(args, registry, **_):
    router = FLAM_ROOT / "core" / "router.sh"
    sessions_str = ",".join(
        f"{n}:{s.session}" for n, s in registry.services.items()
    )
    env = {**os.environ, "FLAM_SESSIONS": sessions_str}
    result = subprocess.run(["bash", str(router), args.name], env=env)
    sys.exit(result.returncode)


def cmd_save(args, resurrection, **_):
    path = resurrection.save()
    print(f"[FlamOS] Workspace saved → {path}")


def cmd_restore(args, registry, orchestrator, resurrection, **_):
    name = _resolve_service(getattr(args, "service", None), registry)

    if name:
        # Single service restore: just (re)start it via orchestrator
        svc = registry.services[name]
        if tmux.session_exists(svc.session):
            print(f"[FlamOS] '{name}' is already running in {svc.session}")
            return
        print(f"[FlamOS] Restoring {name}...")
        orchestrator.up(name)
        print(f"[FlamOS] Done.")
        return

    resurrection.restore(only_registry=not getattr(args, "all", False))


def cmd_snapshot(args, resurrection, **_):
    resurrection.show()


def cmd_watch(args, registry, orchestrator, **_):
    name = _resolve_service(getattr(args, "service", None), registry)

    if name:
        # Narrow the watchdog to a single service
        filtered = _filter_registry(registry, name)
        watchdog = Watchdog(orchestrator, filtered)
        print(f"[FlamWatchdog] Watching single service: {name}")
    else:
        watchdog = Watchdog(orchestrator, registry)

    watchdog.start()


def cmd_panic(args, registry, **_):
    name = _resolve_service(getattr(args, "service", None), registry)
    PANIC_SESSION = "FlamPanic"

    if not tmux.session_exists(PANIC_SESSION):
        tmux.new_session(PANIC_SESSION, "bash", "/")

    targets = (
        {name: registry.services[name]}.items()
        if name
        else registry.services.items()
    )

    opened = 0
    for svc_name, svc in targets:
        # When targeting all: skip non-critical unless --all
        if not name and not svc.critical and not getattr(args, "all", False):
            continue

        win_name = f"log-{svc_name}"
        if svc.log_file:
            cmd = f"echo '=== {svc_name} logs ===' && tail -f {svc.log_file}"
        else:
            cmd = f"echo '=== {svc_name} health ===' && watch -n2 '{svc.health_check}'"

        tmux.open_alert_pane(PANIC_SESSION, win_name, cmd)
        print(f"  ✓ {win_name}")
        opened += 1

    if opened == 0:
        print("  No panes opened — try --all to include non-critical services")
        return

    label = name or "critical services"
    print(f"\n[FlamOS] 🚨 Panic panes open for {label} → {PANIC_SESSION}")
    tmux.switch_to(PANIC_SESSION)


def cmd_doctor(args, registry, orchestrator, **_):
    name = _resolve_service(getattr(args, "service", None), registry)
    targets = (
        {name: registry.services[name]}.items()
        if name
        else registry.services.items()
    )

    label = name or "all services"
    print(f"[FlamOS] Diagnostics — {label}\n")
    all_ok = True

    for svc_name, svc in targets:
        healthy = orchestrator.is_healthy(svc_name)
        session_ok = tmux.session_exists(svc.session)
        s_icon = "✓" if healthy else "✗"
        sess_icon = "●" if session_ok else "○"
        tags = f"[{', '.join(svc.tags)}]" if svc.tags else ""

        print(f"  {s_icon} {svc_name:<16} session:{sess_icon}  health:{'OK' if healthy else 'FAIL':<6}  {tags}")

        if not healthy:
            all_ok = False
            print(f"     └─ check:   {svc.health_check}")
            if not session_ok:
                print(f"     └─ session: not running — flam up {svc_name}")
            if svc.critical:
                print(f"     └─ ⚠  CRITICAL — flam restart {svc_name}")

    print()
    if all_ok:
        print("  ✓ All systems nominal")
    else:
        print("  ✗ Issues found — run: flam dash" + (f" {name}" if name else ""))


def cmd_list(args, registry, **_):
    name = _resolve_service(getattr(args, "service", None), registry)
    targets = (
        {name: registry.services[name]}.items()
        if name
        else registry.services.items()
    )

    print(f"{'NAME':<16} {'SESSION':<22} {'TAGS':<22} {'PORT':<6} CRITICAL")
    print("─" * 75)
    for svc_name, svc in targets:
        tags = ", ".join(svc.tags) if svc.tags else "—"
        port = str(svc.port) if svc.port else "—"
        critical = "✓" if svc.critical else "·"
        print(f"{svc_name:<16} {svc.session:<22} {tags:<22} {port:<6} {critical}")


# ── Argument parser ───────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flam",
        description="FlamOS — Terminal Control Plane",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # ── up / down / restart ───────────────────────────────────────────────────
    for cmd in ("up", "down", "restart"):
        p = sub.add_parser(cmd, help=f"{cmd.capitalize()} service(s)")
        p.add_argument("service", nargs="?", help="Service name (omit for all)")

    # ── status ────────────────────────────────────────────────────────────────
    for alias in ("status", "stat"):
        p_stat = sub.add_parser(alias, help="Service status snapshot")
        p_stat.add_argument("service", nargs="?", help="Service name (omit for all)")
        p_stat.add_argument("--json", action="store_true", help="Machine-readable output")

    # ── dash ──────────────────────────────────────────────────────────────────
    p_dash = sub.add_parser("dash", help="Live dashboard — one or all services")
    p_dash.add_argument("service", nargs="?", help="Service name for focused view")
    p_dash.add_argument("--refresh", type=float, default=3.0, help="Refresh interval (s)")

    # ── jump ──────────────────────────────────────────────────────────────────
    p_jump = sub.add_parser("jump", help="Fuzzy-navigate to a tmux session")
    p_jump.add_argument("name", help="Fuzzy service/session name")

    # ── save / restore / snapshot ─────────────────────────────────────────────
    sub.add_parser("save", help="Save workspace state")

    p_restore = sub.add_parser("restore", help="Restore from snapshot — one or all")
    p_restore.add_argument("service", nargs="?", help="Service name to restore individually")
    p_restore.add_argument("--all", action="store_true", help="Include non-registry sessions")

    sub.add_parser("snapshot", help="Show last saved snapshot")

    # ── watch ─────────────────────────────────────────────────────────────────
    p_watch = sub.add_parser("watch", help="Watchdog daemon — one or all services")
    p_watch.add_argument("service", nargs="?", help="Service name to watch individually")

    # ── panic ─────────────────────────────────────────────────────────────────
    p_panic = sub.add_parser("panic", help="Open log panes — one or all critical")
    p_panic.add_argument("service", nargs="?", help="Service name for single pane")
    p_panic.add_argument("--all", action="store_true", help="Include non-critical services")

    # ── doctor ────────────────────────────────────────────────────────────────
    p_doctor = sub.add_parser("doctor", help="Health check report — one or all")
    p_doctor.add_argument("service", nargs="?", help="Service name to check individually")

    # ── list ──────────────────────────────────────────────────────────────────
    p_list = sub.add_parser("list", help="List registered services")
    p_list.add_argument("service", nargs="?", help="Service name for detail view")

    return parser


COMMAND_MAP = {
    "up":       cmd_up,
    "down":     cmd_down,
    "restart":  cmd_restart,
    "status":   cmd_status,
    "stat":     cmd_status,
    "dash":     cmd_dash,
    "jump":     cmd_jump,
    "save":     cmd_save,
    "restore":  cmd_restore,
    "snapshot": cmd_snapshot,
    "watch":    cmd_watch,
    "panic":    cmd_panic,
    "doctor":   cmd_doctor,
    "list":     cmd_list,
}


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    handler = COMMAND_MAP.get(args.command)
    if not handler:
        parser.print_help()
        sys.exit(1)

    registry, orchestrator, resurrection = _setup()

    handler(
        args,
        registry=registry,
        orchestrator=orchestrator,
        resurrection=resurrection,
    )


if __name__ == "__main__":
    main()
