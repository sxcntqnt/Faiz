"""
core/dashboard.py
Live terminal dashboard. Shows all service statuses, uptime, restart counts.
Requires: pip install rich
"""

from __future__ import annotations

import time
import logging
from datetime import datetime, timedelta

try:
    from rich.console import Console
    from rich.table import Table
    from rich.live import Live
    from rich.text import Text
    from rich.panel import Panel
    from rich.columns import Columns
    from rich import box
except ImportError:
    raise SystemExit("rich is required: pip install rich")

from .orchestrator import Orchestrator, Status
from .registry import Registry

log = logging.getLogger("flam.dashboard")

console = Console()

COLOR_MAP = {
    "green":   "bright_green",
    "blue":    "bright_blue",
    "cyan":    "bright_cyan",
    "orange":  "bright_yellow",
    "magenta": "bright_magenta",
    "white":   "white",
    "red":     "bright_red",
}

STATUS_STYLE = {
    Status.RUNNING:  ("● running",  "bright_green"),
    Status.STOPPED:  ("■ stopped",  "dim white"),
    Status.DEGRADED: ("▲ degraded", "bright_yellow"),
    Status.UNKNOWN:  ("? unknown",  "dim yellow"),
}


def _age(ts: float) -> str:
    if not ts:
        return "—"
    delta = timedelta(seconds=int(time.time() - ts))
    h, rem = divmod(int(delta.total_seconds()), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def _build_table(orchestrator: Orchestrator, registry: Registry) -> Table:
    statuses = orchestrator.status()

    table = Table(
        box=box.SIMPLE_HEAVY,
        border_style="bright_black",
        show_header=True,
        header_style="bold bright_white",
        expand=True,
        pad_edge=True,
        show_lines=False,
    )

    table.add_column("Service",    style="bold", min_width=12)
    table.add_column("Status",     min_width=14)
    table.add_column("Session",    style="dim", min_width=18)
    table.add_column("Tags",       style="dim cyan", min_width=16)
    table.add_column("Port",       justify="right", min_width=6)
    table.add_column("Last seen",  justify="right", min_width=10)
    table.add_column("Restarts",   justify="right", min_width=8)
    table.add_column("Critical",   justify="center", min_width=8)

    for name, svc in registry.services.items():
        state = statuses.get(name)
        if not state:
            continue

        label, style = STATUS_STYLE.get(state.status, ("? unknown", "dim"))
        status_text = Text(label, style=style)

        svc_color = COLOR_MAP.get(svc.color, "white")
        svc_name = Text(name, style=f"bold {svc_color}")

        tags_str = " ".join(f"[{t}]" for t in svc.tags) if svc.tags else "—"
        port_str = str(svc.port) if svc.port else "—"
        critical_str = Text("✓", style="bright_red bold") if svc.critical else Text("·", style="dim")
        last_seen = _age(state.last_seen) if state.status == Status.RUNNING else "—"
        restarts = str(state.restart_count) if state.restart_count else "·"

        table.add_row(
            svc_name,
            status_text,
            svc.session,
            tags_str,
            port_str,
            last_seen,
            restarts,
            critical_str,
        )

    return table


def _build_header() -> Panel:
    now = datetime.now().strftime("%H:%M:%S")
    title = Text.assemble(
        ("⚡ FlamOS", "bold bright_white"),
        ("  —  ", "dim"),
        ("Control Plane", "bright_cyan"),
        ("  ", ""),
        (now, "dim"),
    )
    return Panel(title, style="bright_black", padding=(0, 2))


def _build_footer(registry: Registry) -> Text:
    cmds = [
        ("flam up", "start all"),
        ("flam down <name>", "stop one"),
        ("flam jump <name>", "navigate"),
        ("flam save", "snapshot"),
        ("flam restore", "resurrect"),
        ("flam panic", "all logs"),
        ("q", "quit"),
    ]
    parts = []
    for cmd, desc in cmds:
        parts.append((f" {cmd} ", "bold bright_white on grey19"))
        parts.append((f" {desc}  ", "dim"))
    return Text.assemble(*parts)


def show_once(orchestrator: Orchestrator, registry: Registry) -> None:
    """Print a single static snapshot of service status."""
    console.print(_build_header())
    console.print(_build_table(orchestrator, registry))
    console.print(_build_footer(registry))


def show_live(orchestrator: Orchestrator, registry: Registry, refresh: float = 3.0) -> None:
    """
    Render a live-refreshing dashboard. Exits on 'q' or Ctrl-C.
    """
    try:
        with Live(
            console=console,
            refresh_per_second=1 / refresh,
            screen=True,
        ) as live:
            while True:
                table = _build_table(orchestrator, registry)
                header = _build_header()
                footer = _build_footer(registry)
                live.update(Panel(
                    Columns([table], expand=True),
                    title="[bold bright_cyan]FlamOS[/] [dim]Control Plane[/]",
                    subtitle=str(footer),
                    border_style="bright_black",
                    padding=(1, 2),
                ))
                time.sleep(refresh)

    except KeyboardInterrupt:
        pass
