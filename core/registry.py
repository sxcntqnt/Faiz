"""
core/registry.py
Load and expose the service registry from services.yaml.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    raise SystemExit("pyyaml is required: pip install pyyaml")

REGISTRY_PATH = Path(__file__).parent.parent / "config" / "services.yaml"


def _resolve_env(value: str) -> str:
    """Expand ${VAR} placeholders using os.environ."""
    return re.sub(
        r"\$\{(\w+)\}",
        lambda m: os.environ.get(m.group(1), m.group(0)),
        value,
    )


@dataclass
class Service:
    name: str
    session: str
    command: str
    dir: str
    health_check: str
    color: str = "white"
    critical: bool = False
    tags: list[str] = field(default_factory=list)
    log_file: str | None = None
    port: int | None = None

    def resolved_command(self) -> str:
        return _resolve_env(self.command.strip())

    def resolved_dir(self) -> str:
        return _resolve_env(self.dir)


@dataclass
class Defaults:
    watchdog_interval: int = 10
    restart_limit: int = 5
    restart_cooldown: int = 30
    state_file: str = "./state/workspace.json"
    log_dir: str = "./logs"


class Registry:
    def __init__(self, path: Path = REGISTRY_PATH):
        self._path = path
        self._raw: dict[str, Any] = {}
        self.defaults = Defaults()
        self.services: dict[str, Service] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            raise FileNotFoundError(f"Registry not found: {self._path}")

        with open(self._path) as f:
            self._raw = yaml.safe_load(f) or {}

        raw_defaults = self._raw.get("defaults", {})
        self.defaults = Defaults(**{
            k: raw_defaults[k]
            for k in Defaults.__dataclass_fields__
            if k in raw_defaults
        })

        self.services = {}
        for name, raw in (self._raw.get("services") or {}).items():
            self.services[name] = Service(
                name=name,
                session=raw["session"],
                command=raw["command"],
                dir=raw.get("dir", "/"),
                health_check=raw.get("health_check", "true"),
                color=raw.get("color", "white"),
                critical=raw.get("critical", False),
                tags=raw.get("tags", []),
                log_file=raw.get("log_file"),
                port=raw.get("port"),
            )

    def reload(self) -> None:
        self._load()

    def get(self, name: str) -> Service:
        if name not in self.services:
            raise KeyError(f"Unknown service '{name}'. Available: {list(self.services)}")
        return self.services[name]

    def by_tag(self, tag: str) -> list[Service]:
        return [s for s in self.services.values() if tag in s.tags]

    def critical(self) -> list[Service]:
        return [s for s in self.services.values() if s.critical]

    def all(self) -> list[Service]:
        return list(self.services.values())
