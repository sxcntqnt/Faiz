#!/usr/bin/env python3
"""
flam — FlamOS Control Plane CLI
─────────────────────────────────────────────────────────────
Usage:
  flam up [service]          Start one or all services
  flam down [service]        Stop one or all services
  flam restart [service]     Restart one or all services
  flam status                Show service status (once)
  flam dash                  Live dashboard (refreshes)
  flam jump <name>           Fuzzy-navigate to tmux session
  flam save                  Save workspace state
  flam restore               Restore workspace from snapshot
  flam snapshot              Show last saved snapshot
  flam watch                 Start watchdog daemon
  flam panic                 Open all critical log panes
  flam doctor                Run health checks and report
  flam list                  List all registered services
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
FLAM_ROOT = Path(os.getenv("FLAM_ROOT", Path(__file__).resolve().parent)).resolve()
sys.path.insert(0, str(FLAM_ROOT))

from core.registry import Registry
from core.orchestrator import Orchestrator, Status
from core.watchdog import Watchdog
from core.resurrection import Resurrection
from core import tmux
from core.utils import _status_icon ,_fmt_time

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_DIR = FLAM_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "flam.log"),
    ],
)
log = logging.getLogger("flam")

# ── Shared setup ──────────────────────────────────────────────────────────────

def _setup() -> tuple[Registry, Orchestrator, Resurrection]:
    registry = Registry(FLAM_ROOT / "config" / "services.yaml")
    orchestrator = Orchestrator(registry)
    resurrection = Resurrection(registry)
    return registry, orchestrator, resurrection


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_up(args, registry, orchestrator, **_):
    name = args.service or None
    target = name or "all services"
    print(f"[FlamOS] Starting {target}...")
    orchestrator.up(name)
    print(f"[FlamOS] Done.")


def cmd_down(args, registry, orchestrator, **_):
    name = args.service or None
    target = name or "all services"
    print(f"[FlamOS] Stopping {target}...")
    orchestrator.down(name)
    print(f"[FlamOS] Done.")


def cmd_restart(args, registry, orchestrator, **_):
    name = args.service or None
    target = name or "all services"
    print(f"[FlamOS] Restarting {target}...")
    orchestrator.restart(name)
    print(f"[FlamOS] Done.")


def cmd_status(args, registry, orchestrator, **_):
    result = orchestrator.status(args.service)

    # ── JSON mode (machine layer) ─────────────────────────────
    if getattr(args, "json", False):
        import json
        print(json.dumps({k: str(v) for k, v in result.items()}, indent=2))
        return

    # ── single service view (alive tree node) ─────────────────
    if args.service:
        name, state = next(iter(result.items()))
        svc = registry.services[name]

        status = state.status if hasattr(state, "status") else state
        icon = _status_icon(status)

        print(f"""
⚡ FlamOS · Live Node

{icon} {name}
├─ status      {status.value if hasattr(status, "value") else status}
├─ session     {svc.session}
├─ port        {svc.port or "—"}
├─ critical    {"YES" if svc.critical else "no"}
├─ tags        {", ".join(svc.tags) if svc.tags else "—"}
├─ restarts    {state.restart_count if hasattr(state, "restart_count") else 0}
├─ last_seen   {_fmt_time(getattr(state, "last_seen", None))}
└─ error       {getattr(state, "last_error", "") or "—"}
""".strip())
        return

    # ── full system view (delegated live dashboard) ───────────
    from core.dashboard import show_once
    show_once(orchestrator, registry)


def cmd_dash(args, registry, orchestrator, **_):
    from core.dashboard import show_live
    refresh = getattr(args, "refresh", 3.0)
    show_live(orchestrator, registry, refresh=refresh)


def cmd_jump(args, registry, **_):
    name = args.name
    router = FLAM_ROOT / "core" / "router.sh"

    # Build FLAM_SESSIONS env var from registry
    sessions_str = ",".join(
        f"{svc_name}:{svc.session}"
        for svc_name, svc in registry.services.items()
    )

    env = {**os.environ, "FLAM_SESSIONS": sessions_str}
    result = subprocess.run(["bash", str(router), name], env=env)
    sys.exit(result.returncode)


def cmd_save(args, resurrection, **_):
    path = resurrection.save()
    print(f"[FlamOS] Workspace saved → {path}")


def cmd_restore(args, resurrection, **_):
    resurrection.restore(only_registry=not getattr(args, "all", False))


def cmd_snapshot(args, resurrection, **_):
    resurrection.show()


def cmd_watch(args, registry, orchestrator, **_):
    watchdog = Watchdog(orchestrator, registry)
    watchdog.start()


def cmd_panic(args, registry, **_):
    """
    Open a dedicated tmux window for every service's logs simultaneously.
    The terminal equivalent of hitting all the alarms at once.
    """
    print("[FlamOS] 🚨 PANIC — opening all critical log panes")
    PANIC_SESSION = "FlamPanic"

    if not tmux.session_exists(PANIC_SESSION):
        tmux.new_session(PANIC_SESSION, "bash", "/")

    for name, svc in registry.services.items():
        if not svc.critical and not getattr(args, "all", False):
            continue

        win_name = f"log-{name}"
        if svc.log_file:
            cmd = f"echo '=== {name} logs ===' && tail -f {svc.log_file}"
        else:
            cmd = f"echo '=== {name} health ===' && watch -n2 '{svc.health_check}'"

        tmux.open_alert_pane(PANIC_SESSION, win_name, cmd)
        print(f"  ✓ Opened: {win_name}")

    # Jump into the panic session
    print(f"\n[FlamOS] Jumping to {PANIC_SESSION}...")
    tmux.switch_to(PANIC_SESSION)


def cmd_doctor(args, registry, orchestrator, **_):
    """
    Run all health checks and print a clear report.
    """
    print("[FlamOS] Running diagnostics...\n")
    all_ok = True

    for name, svc in registry.services.items():
        healthy = orchestrator.is_healthy(name)
        session_ok = tmux.session_exists(svc.session)
        status_icon = "✓" if healthy else "✗"
        session_icon = "●" if session_ok else "○"

        tag = f"[{', '.join(svc.tags)}]" if svc.tags else ""
        print(f"  {status_icon} {name:<16} session:{session_icon}  health:{'OK' if healthy else 'FAIL':<6}  {tag}")

        if not healthy:
            all_ok = False
            print(f"     └─ check: {svc.health_check}")
            if svc.critical:
                print(f"     └─ ⚠  CRITICAL — run: flam restart {name}")

    print()
    print(f"  {'✓ All systems nominal' if all_ok else '✗ Issues detected — run: flam dash for live view'}")


def cmd_list(args, registry, **_):
    print(f"{'NAME':<16} {'SESSION':<22} {'TAGS':<22} {'PORT':<6} CRITICAL")
    print("─" * 75)
    for name, svc in registry.services.items():
        tags = ", ".join(svc.tags) if svc.tags else "—"
        port = str(svc.port) if svc.port else "—"
        critical = "✓" if svc.critical else "·"
        print(f"{name:<16} {svc.session:<22} {tags:<22} {port:<6} {critical}")


# ── Argument parser ───────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flam",
        description="FlamOS — Terminal Control Plane",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # up / down / restart — optional service name
    for cmd in ("up", "down", "restart"):
        p = sub.add_parser(cmd, help=f"{cmd.capitalize()} service(s)")
        p.add_argument("service", nargs="?", help="Service name (omit for all)")

    # status / dash
    p_status = sub.add_parser(
        "status",
        aliases=["stat"],
        help="Show service status snapshot"
    )
    p_status.add_argument("service", nargs="?", help="Service name (optional)")
    p_status.add_argument("--json", action="store_true", help="Raw output")

    p_dash = sub.add_parser("dash", help="Live refreshing dashboard")
    p_dash.add_argument("--refresh", type=float, default=3.0, help="Refresh interval (seconds)")

    # jump
    p_jump = sub.add_parser("jump", help="Fuzzy-navigate to a tmux session")
    p_jump.add_argument("name", help="Fuzzy service/session name")

    # save / restore / snapshot
    sub.add_parser("save", help="Save workspace state")
    p_restore = sub.add_parser("restore", help="Restore workspace from snapshot")
    p_restore.add_argument("--all", action="store_true", help="Restore non-registry sessions too")
    sub.add_parser("snapshot", help="Show last saved snapshot")

    # watch / panic / doctor / list
    sub.add_parser("watch", help="Start the watchdog daemon")
    p_panic = sub.add_parser("panic", help="Open all critical log panes")
    p_panic.add_argument("--all", action="store_true", help="Include non-critical services")
    sub.add_parser("doctor", help="Run health checks and report")
    sub.add_parser("list", help="List all registered services")

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
