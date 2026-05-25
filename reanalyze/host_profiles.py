"""Host profile helpers for multi-cluster Purohit workflows."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import socket
from typing import Any

import yaml


@dataclass(frozen=True)
class HostProfile:
    """Description of one source or submit host/cluster."""

    name: str
    ssh: str | None = None
    home: Path | None = None
    project_dir: Path | None = None
    scheduler: str = "condor"
    hostname_contains: tuple[str, ...] = ()
    metadata: dict[str, Any] | None = None

    @classmethod
    def from_mapping(cls, name: str, data: dict[str, Any]) -> "HostProfile":
        return cls(
            name=name,
            ssh=data.get("ssh"),
            home=Path(data["home"]).expanduser() if data.get("home") else None,
            project_dir=Path(data["project_dir"]).expanduser() if data.get("project_dir") else None,
            scheduler=str(data.get("scheduler", "condor")),
            hostname_contains=tuple(str(item).lower() for item in data.get("hostname_contains", []) or []),
            metadata={key: value for key, value in data.items() if key not in {"ssh", "home", "project_dir", "scheduler", "hostname_contains"}},
        )

    def require_ssh(self) -> str:
        if not self.ssh:
            raise ValueError(f"host profile {self.name!r} is missing ssh")
        return self.ssh

    def require_home(self) -> Path:
        if self.home is None:
            raise ValueError(f"host profile {self.name!r} is missing home")
        return self.home

    def require_project_dir(self) -> Path:
        if self.project_dir is None:
            raise ValueError(f"host profile {self.name!r} is missing project_dir")
        return self.project_dir

    def matches_hostname(self, hostname: str) -> bool:
        lower = hostname.lower()
        return any(fragment and fragment in lower for fragment in self.hostname_contains)


@dataclass(frozen=True)
class HostProfiles:
    hosts: dict[str, HostProfile]

    @classmethod
    def load(cls, path: Path) -> "HostProfiles":
        data = yaml.safe_load(path.expanduser().read_text()) or {}
        raw_hosts = data.get("hosts", data)
        if not isinstance(raw_hosts, dict):
            raise ValueError(f"host profile file must contain a mapping: {path}")
        hosts = {}
        for name, item in raw_hosts.items():
            if not isinstance(item, dict):
                raise ValueError(f"host profile {name!r} must be a mapping")
            hosts[str(name)] = HostProfile.from_mapping(str(name), item)
        return cls(hosts=hosts)

    def __getitem__(self, name: str) -> HostProfile:
        try:
            return self.hosts[name]
        except KeyError as exc:
            raise KeyError(f"unknown host profile {name!r}; available: {sorted(self.hosts)}") from exc

    def detect_current(self) -> HostProfile | None:
        hostname = socket.getfqdn() or socket.gethostname()
        hostname = hostname.lower().strip()
        if not hostname:
            return None
        matches = [profile for profile in self.hosts.values() if profile.matches_hostname(hostname)]
        return matches[0] if len(matches) == 1 else None
