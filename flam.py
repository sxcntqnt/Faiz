#!/usr/bin/env python3
"""
flam — FlamOS Control Plane CLI
─────────────────────────────────────────────────────────────
Usage:
  flam up [service|group]     Start one, a group, or all
  flam down [service|group]   Stop one, a group, or all
  flam restart [service|group] Restart one, a group, or all
  flam status [service|group]  Status snapshot
  flam dash [service|group]    Live dashboard
  flam doctor [service|group]  Health check report
  flam panic [service|group]   Open log panes
  flam watch [service|group]   Watchdog daemon
  flam restore [service|group] Restore from snapshot
  flam jump <name>             Fuzzy-navigate to tmux session
  flam save                    Save workspace state
  flam snapshot                Show last saved snapshot
  flam list                    List services and groups
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
            key = key.strip()
            val = val.strip()
            # Strip surrounding single or double quotes so that
            # VAULT_ADDR='http://...' resolves to http://... not 'http://...'
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                val = val[1:-1]
            os.environ.setdefault(key, val)

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


def _resolve_targets(name: str | None, registry: Registry) -> list[str] | None:
    """
    Resolve a user-supplied name to a list of service names.
      None          → caller uses all services
      [name]        → single validated service
      [a, b, ...]   → expanded group
    """
    if name is None:
        return None
    if name in registry.services:
        return [name]
    if name in registry.groups:
        return registry.groups[name]
    known = sorted(list(registry.services) + list(registry.groups))
    print(f"[FlamOS] Unknown service or group '{name}'.", file=sys.stderr)
    print(f"[FlamOS] Available: {', '.join(known)}", file=sys.stderr)
    sys.exit(1)


def _filter_registry(registry: Registry, service_names: list[str]) -> Registry:
    """Return a shallow-copied registry scoped to the given service names."""
    filtered = copy.copy(registry)
    filtered.services = {n: registry.services[n] for n in service_names}
    return filtered


def _setup() -> tuple[Registry, Orchestrator, Resurrection]:
    registry = Registry(FLAM_ROOT / "config" / "services.yaml")
    orchestrator = Orchestrator(registry)
    resurrection = Resurrection(registry)
    return registry, orchestrator, resurrection


# ── Commands ──────────────────────────────────────────────────────────────────

def _has_internal_deps(names: list[str], registry: Registry) -> bool:
    """True if any service in names depends on another service also in names."""
    name_set = set(names)
    return any(
        dep in name_set
        for n in names
        for dep in registry.services[n].depends_on
    )


def cmd_up(args, registry, orchestrator, **_):
    names = _resolve_targets(args.service, registry)
    label = args.service or "all services"
    target_list = names or list(registry.services)

    print(f"[FlamOS] Starting {label}...")
    if len(target_list) > 1 and _has_internal_deps(target_list, registry):
        orchestrator.up_ordered(target_list)
    else:
        for n in target_list:
            orchestrator.up(n)
    print("[FlamOS] Done.")


def cmd_down(args, registry, orchestrator, **_):
    names = _resolve_targets(args.service, registry)
    label = args.service or "all services"
    target_list = names or list(registry.services)

    print(f"[FlamOS] Stopping {label}...")
    if len(target_list) > 1 and _has_internal_deps(target_list, registry):
        orchestrator.down_ordered(target_list)
    else:
        for n in target_list:
            orchestrator.down(n)
    print("[FlamOS] Done.")


def cmd_restart(args, registry, orchestrator, **_):
    names = _resolve_targets(args.service, registry)
    label = args.service or "all services"
    target_list = names or list(registry.services)

    print(f"[FlamOS] Restarting {label}...")
    if len(target_list) > 1 and _has_internal_deps(target_list, registry):
        orchestrator.restart_ordered(target_list)
    else:
        for n in target_list:
            orchestrator.restart(n)
    print("[FlamOS] Done.")


def cmd_status(args, registry, orchestrator, **_):
    names = _resolve_targets(getattr(args, "service", None), registry)
    single = names[0] if names and len(names) == 1 else None
    result = orchestrator.status(single)

    # ── JSON mode ─────────────────────────────────────────────────────────────
    if getattr(args, "json", False):
        import json
        scope = {n: registry.services[n] for n in (names or registry.services)}
        out = {
            k: {
                "status": result[k].status.value if k in result else "unknown",
                "restart_count": result[k].restart_count if k in result else 0,
                "last_seen": _fmt_time(result[k].last_seen) if k in result else "—",
                "last_error": result[k].last_error or None if k in result else None,
            }
            for k in scope
        }
        print(json.dumps(out, indent=2))
        return

    # ── Single service — focused node view ────────────────────────────────────
    if single:
        state = result[single]
        svc = registry.services[single]
        icon = _status_icon(state.status)
        sess_alive = tmux.session_exists(svc.session)
        deps = list(svc.depends_on.keys()) if svc.depends_on else []
        print(f"""
⚡ FlamOS · {single}

{icon} {state.status.value}
├─ session     {svc.session}  {"●" if sess_alive else "○"}
├─ port        {svc.port or "—"}
├─ critical    {"YES" if svc.critical else "no"}
├─ tags        {", ".join(svc.tags) if svc.tags else "—"}
├─ depends_on  {", ".join(deps) if deps else "—"}
├─ restarts    {state.restart_count}
├─ last seen   {_fmt_time(state.last_seen)}
└─ last error  {state.last_error or "—"}
""".strip())
        return

    # ── Group or all — filtered dashboard snapshot ────────────────────────────
    from core.dashboard import show_once
    scope = _filter_registry(registry, names) if names else registry
    show_once(orchestrator, scope)


def cmd_dash(args, registry, orchestrator, **_):
    names = _resolve_targets(getattr(args, "service", None), registry)
    refresh = getattr(args, "refresh", 3.0)
    single = names[0] if names and len(names) == 1 else None

    # ── Group or all — filtered live dashboard ────────────────────────────────
    if not single:
        from core.dashboard import show_live
        scope = _filter_registry(registry, names) if names else registry
        show_live(orchestrator, scope, refresh=refresh)
        return

    # ── Single service — focused live panel ───────────────────────────────────
    try:
        from rich.live import Live
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text
    except ImportError:
        raise SystemExit("rich is required: pip install rich")

    svc = registry.services[single]

    def _build_focused() -> Panel:
        orchestrator._refresh_status(svc)
        state = orchestrator._state[single]
        icon = _status_icon(state.status)
        healthy = orchestrator.is_healthy(single)
        sess_alive = tmux.session_exists(svc.session)
        deps = list(svc.depends_on.keys()) if svc.depends_on else []

        t = Table(box=None, show_header=False, padding=(0, 2))
        t.add_column("key",   style="dim", width=14)
        t.add_column("value", style="bold bright_white")

        rows = [
            ("status",     Text(
                f"{icon}  {state.status.value}",
                style="bright_green" if state.status == Status.RUNNING
                      else "bright_yellow" if state.status == Status.DEGRADED
                      else "dim white",
            )),
            ("session",    Text(
                f"{svc.session}  {'●' if sess_alive else '○'}",
                style="bright_green" if sess_alive else "dim",
            )),
            ("health",     Text("OK" if healthy else "FAIL",
                               style="bright_green" if healthy else "bright_red")),
            ("port",       str(svc.port or "—")),
            ("critical",   Text("YES", style="bright_red bold") if svc.critical else Text("no", style="dim")),
            ("tags",       ", ".join(svc.tags) if svc.tags else "—"),
            ("depends_on", ", ".join(deps) if deps else "—"),
            ("restarts",   str(state.restart_count) if state.restart_count else "·"),
            ("last seen",  _fmt_time(state.last_seen) if state.status == Status.RUNNING else "—"),
            ("last error", state.last_error or "—"),
        ]

        for key, val in rows:
            t.add_row(key, val if isinstance(val, Text) else Text(str(val)))

        ts = datetime.now().strftime("%H:%M:%S")
        border = ("bright_green" if state.status == Status.RUNNING
                  else "bright_yellow" if state.status == Status.DEGRADED
                  else "dim")
        return Panel(
            t,
            title=f"[bold bright_white]⚡ {single}[/]  [dim]{ts}[/]",
            subtitle="[dim]Ctrl-C to quit[/]",
            border_style=border,
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
    names = _resolve_targets(getattr(args, "service", None), registry)

    if names:
        for n in names:
            svc = registry.services[n]
            if tmux.session_exists(svc.session):
                print(f"  · '{n}' already running in {svc.session}")
                continue
            print(f"  ↳ Restoring {n}...")
            orchestrator.up(n)
        print("[FlamOS] Done.")
        return

    resurrection.restore(only_registry=not getattr(args, "all", False))


def cmd_snapshot(args, resurrection, **_):
    resurrection.show()


def cmd_watch(args, registry, orchestrator, **_):
    names = _resolve_targets(getattr(args, "service", None), registry)

    if names:
        filtered = _filter_registry(registry, names)
        label = args.service
        watchdog = Watchdog(orchestrator, filtered)
        print(f"[FlamWatchdog] Watching: {label} ({', '.join(names)})")
    else:
        watchdog = Watchdog(orchestrator, registry)

    watchdog.start()


def cmd_panic(args, registry, **_):
    names = _resolve_targets(getattr(args, "service", None), registry)
    PANIC_SESSION = "FlamPanic"

    if not tmux.session_exists(PANIC_SESSION):
        tmux.new_session(PANIC_SESSION, "bash", "/")

    pool = {n: registry.services[n] for n in (names or registry.services)}

    opened = 0
    for svc_name, svc in pool.items():
        if not names and not svc.critical and not getattr(args, "all", False):
            continue

        win_name = f"log-{svc_name}"
        cmd = (
            f"echo '=== {svc_name} ===' && tail -f {svc.log_file}"
            if svc.log_file
            else f"echo '=== {svc_name} ===' && watch -n2 '{svc.health_check}'"
        )
        tmux.open_alert_pane(PANIC_SESSION, win_name, cmd)
        print(f"  ✓ {win_name}")
        opened += 1

    if opened == 0:
        print("  No panes opened — try: flam panic --all")
        return

    label = args.service or "critical services"
    print(f"\n[FlamOS] 🚨 {label} → {PANIC_SESSION}")
    tmux.switch_to(PANIC_SESSION)


def cmd_doctor(args, registry, orchestrator, **_):
    names = _resolve_targets(getattr(args, "service", None), registry)
    pool = {n: registry.services[n] for n in (names or registry.services)}

    print(f"[FlamOS] Diagnostics — {args.service or 'all services'}\n")
    all_ok = True

    for svc_name, svc in pool.items():
        healthy = orchestrator.is_healthy(svc_name)
        sess_ok = tmux.session_exists(svc.session)
        s_icon = "✓" if healthy else "✗"
        sess_icon = "●" if sess_ok else "○"
        tags = f"[{', '.join(svc.tags)}]" if svc.tags else ""

        print(f"  {s_icon} {svc_name:<24} session:{sess_icon}  health:{'OK' if healthy else 'FAIL':<6}  {tags}")

        if not healthy:
            all_ok = False
            print(f"     └─ check:   {svc.health_check}")
            if not sess_ok:
                print(f"     └─ session: not running — flam up {svc_name}")
            if svc.critical:
                print(f"     └─ ⚠  CRITICAL — flam restart {svc_name}")
            if svc.depends_on:
                print(f"     └─ needs:   {', '.join(svc.depends_on)}")

    print()
    hint = f" {args.service}" if args.service else ""
    print("  ✓ All systems nominal" if all_ok else f"  ✗ Issues found — run: flam dash{hint}")


def cmd_list(args, registry, **_):
    names = _resolve_targets(getattr(args, "service", None), registry)
    pool = {n: registry.services[n] for n in (names or registry.services)}

    print(f"{'NAME':<26} {'SESSION':<24} {'TAGS':<26} {'PORT':<6} CRIT  DEPS")
    print("─" * 95)
    for svc_name, svc in pool.items():
        tags     = ", ".join(svc.tags) if svc.tags else "—"
        port     = str(svc.port) if svc.port else "—"
        critical = "✓" if svc.critical else "·"
        deps     = ", ".join(svc.depends_on) if svc.depends_on else "—"
        print(f"{svc_name:<26} {svc.session:<24} {tags:<26} {port:<6} {critical:<5} {deps}")

    if registry.groups and not names:
        print(f"\n{'GROUP':<16} {'MEMBERS'}")
        print("─" * 60)
        for group_name, members in registry.groups.items():
            print(f"{group_name:<16} {', '.join(members)}")


# ── Argument parser ───────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flam",
        description="FlamOS — Terminal Control Plane",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    for cmd in ("up", "down", "restart"):
        p = sub.add_parser(cmd, help=f"{cmd.capitalize()} service(s) or group")
        p.add_argument("service", nargs="?", help="Service name, group, or omit for all")

    for alias in ("status", "stat"):
        p_s = sub.add_parser(alias, help="Status snapshot — service, group, or all")
        p_s.add_argument("service", nargs="?", help="Service name or group")
        p_s.add_argument("--json", action="store_true", help="Machine-readable output")

    p_dash = sub.add_parser("dash", help="Live dashboard — service, group, or all")
    p_dash.add_argument("service", nargs="?", help="Service name or group")
    p_dash.add_argument("--refresh", type=float, default=3.0, help="Refresh interval (s)")

    p_jump = sub.add_parser("jump", help="Fuzzy-navigate to a tmux session")
    p_jump.add_argument("name", help="Fuzzy service/session name or alias")

    sub.add_parser("save", help="Save workspace state to JSON")

    p_restore = sub.add_parser("restore", help="Restore from snapshot — service, group, or all")
    p_restore.add_argument("service", nargs="?", help="Service name or group")
    p_restore.add_argument("--all", action="store_true", help="Include non-registry sessions")

    sub.add_parser("snapshot", help="Print last saved snapshot")

    p_watch = sub.add_parser("watch", help="Watchdog daemon — service, group, or all")
    p_watch.add_argument("service", nargs="?", help="Service name or group")

    p_panic = sub.add_parser("panic", help="Open log panes — service, group, or all critical")
    p_panic.add_argument("service", nargs="?", help="Service name or group")
    p_panic.add_argument("--all", action="store_true", help="Include non-critical services")

    p_doc = sub.add_parser("doctor", help="Health check — service, group, or all")
    p_doc.add_argument("service", nargs="?", help="Service name or group")

    p_list = sub.add_parser("list", help="List services and groups")
    p_list.add_argument("service", nargs="?", help="Service name or group to filter")

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
