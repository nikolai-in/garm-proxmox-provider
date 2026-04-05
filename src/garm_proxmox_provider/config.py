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
    pool_templates: dict[str, int] = field(default_factory=dict)
    instance_type: str = "vm"
    """Either ``"vm"`` (QEMU) or ``"lxc"`` (Proxmox LXC container)."""
    lxc_unprivileged: bool = True
    """When True, LXC containers are created as unprivileged (recommended)."""
    """Map of ``"os_type/os_arch"`` → VMID for per-OS template selection.

    Example TOML::

        [pool_templates]
        "linux/amd64"   = 9000
        "linux/arm64"   = 9001
        "windows/amd64" = 9002

    When creating an instance, the provider looks up ``os_type/os_arch`` in
    this map first, then falls back to ``template_vmid``.
    """


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

    # --- Pool templates section (optional) ------------------------------------
    # Format: {"linux/amd64": 9000, "windows/amd64": 9002, ...}
    pool_templates_raw = data.get("pool_templates", {})
    pool_templates: dict[str, int] = {}
    for key, val in pool_templates_raw.items():
        try:
            pool_templates[str(key)] = int(val)
        except (TypeError, ValueError):
            raise ConfigError(
                f"[pool_templates].{key!r} must be an integer VMID"
            ) from None

    # Require at least one template source so create_instance always has a VMID
    if template_vmid is None and not pool_templates:
        raise ConfigError(
            "At least one of [defaults].template_vmid or [pool_templates] must be set"
        )

    # --- instance_type ----------------------------------------------------
    instance_type = def_data.get("instance_type", "vm")
    if instance_type not in ("vm", "lxc"):
        raise ConfigError(
            f"[defaults].instance_type must be 'vm' or 'lxc', got {instance_type!r}"
        )

    lxc_unprivileged = bool(def_data.get("lxc_unprivileged", True))

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
        pool_templates=pool_templates,
        instance_type=instance_type,
        lxc_unprivileged=lxc_unprivileged,
    )

    return Config(pve=pve, defaults=defaults)
