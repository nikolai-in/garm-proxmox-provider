"""TOML configuration loader for the GARM Proxmox provider."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ConfigError(Exception):
    """Raised when the configuration is invalid or missing required fields."""


@dataclass
class PVEConfig:
    host: str
    user: str
    token_name: str
    token_value: str
    verify_ssl: bool = True


@dataclass
class DefaultsConfig:
    node: str
    storage: str = "local-lvm"
    pool: str | None = None
    template_vmid: int | None = None
    cores: int = 2
    memory_mb: int = 4096
    disk_gb: int = 20
    bridge: str = "vmbr0"
    ssh_public_key: str | None = None
    snippets_storage: str | None = None


@dataclass
class Config:
    pve: PVEConfig
    defaults: DefaultsConfig


def load_config(path: str) -> Config:
    """Load and validate configuration from a TOML file."""
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {path}")
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in config file: {exc}") from exc

    # --- PVE section -------------------------------------------------------
    pve_data = data.get("pve", {})
    for required in ("host", "user", "token_name", "token_value"):
        if not pve_data.get(required):
            raise ConfigError(f"[pve].{required} is required")

    pve = PVEConfig(
        host=pve_data["host"],
        user=pve_data["user"],
        token_name=pve_data["token_name"],
        token_value=pve_data["token_value"],
        verify_ssl=pve_data.get("verify_ssl", True),
    )

    # --- Defaults section --------------------------------------------------
    def_data = data.get("defaults", {})
    if not def_data.get("node"):
        raise ConfigError("[defaults].node is required")

    template_vmid_raw = def_data.get("template_vmid")
    template_vmid: int | None = None
    if template_vmid_raw is not None:
        try:
            template_vmid = int(template_vmid_raw)
        except (TypeError, ValueError):
            raise ConfigError(
                "[defaults].template_vmid must be an integer"
            ) from None

    defaults = DefaultsConfig(
        node=def_data["node"],
        storage=def_data.get("storage", "local-lvm"),
        pool=def_data.get("pool"),
        template_vmid=template_vmid,
        cores=int(def_data.get("cores", 2)),
        memory_mb=int(def_data.get("memory_mb", 4096)),
        disk_gb=int(def_data.get("disk_gb", 20)),
        bridge=def_data.get("bridge", "vmbr0"),
        ssh_public_key=def_data.get("ssh_public_key"),
        snippets_storage=def_data.get("snippets_storage"),
    )

    return Config(pve=pve, defaults=defaults)
