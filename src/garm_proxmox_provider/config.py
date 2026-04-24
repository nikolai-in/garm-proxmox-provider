"""TOML configuration loader for the GARM Proxmox provider.

This module now supports an optional [logging] section in the TOML config.
Note: runtime environment variables (e.g. GARM_LOG_FILE/GARM_LOG_LEVEL) should
take precedence over TOML values; the TOML loader only provides defaults that
the CLI's logging setup can choose to honor when env vars are absent.

Templates are no longer predefined in config.  Instead, both the VM and LXC
providers look up templates by name at runtime (searching for a QEMU or LXC
resource with ``template=1`` and a matching name).  Only flavors need to be
predefined.

Extra specs (passed per-pool via GARM) can override per-instance settings:
  - ``cores``, ``memory_mb``, ``node`` — resource sizing / placement
  - ``lxc_unprivileged`` — LXC container privilege mode (default: true)
  - ``ssh_public_key`` — inject an SSH public key into the runner user
  - ``runner_install_template`` — base64-encoded bootstrap script provided
    by GARM (required to actually register the runner)
  - ``forge_type`` — "github" or "gitea" (auto-detected from repo URL when absent)
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field


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
class ClusterConfig:
    node: str
    storage: str = "local-lvm"
    pool: str | None = None
    bridge: str = "vmbr0"
    snippets_storage: str | None = None
    ssh_public_key: str | None = None
    lxc_unprivileged: bool = (
        True  # default for LXC containers; overridable via extra_specs
    )


@dataclass
class FlavorConfig:
    cores: int = 2
    memory_mb: int = 4096


@dataclass
class LoggingConfig:
    """Optional logging configuration loaded from TOML [logging]."""

    level: str | None = None  # e.g. "DEBUG", "INFO" - prefer env var when present
    file: str | None = None  # path to log file (rotating handler)
    json: bool = False  # whether to use json formatter
    debug_dump: bool = False  # whether to write startup diagnostic dump to /tmp


@dataclass
class VMIDRangeConfig:
    """Configurable VMID range for randomized assignment."""

    min: int = 1100
    max: int = 1999

    def pick(self) -> int:
        """Pick a random VMID in the configured range."""
        import random

        return random.randint(self.min, self.max)


@dataclass
class Config:
    pve: PVEConfig
    cluster: ClusterConfig
    flavors: dict[str, FlavorConfig] = field(default_factory=dict)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    vmid_range: VMIDRangeConfig = field(default_factory=VMIDRangeConfig)

    def get_flavor(self, name: str) -> FlavorConfig:
        """Return the requested flavor, fallback to 'default', or a baseline flavor."""
        if name and name in self.flavors:
            return self.flavors[name]
        if "default" in self.flavors:
            return self.flavors["default"]
        return FlavorConfig()


def load_config(path: str) -> Config:
    """Load and validate configuration from a TOML file.

    Adds optional [logging] parsing and returns a Config containing a LoggingConfig.
    """
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
        host=str(pve_data["host"]),
        user=str(pve_data["user"]),
        token_name=str(pve_data["token_name"]),
        token_value=str(pve_data["token_value"]),
        verify_ssl=bool(pve_data.get("verify_ssl", True)),
    )

    # --- Cluster section --------------------------------------------------
    cluster_data = data.get("cluster", {})
    if not cluster_data.get("node"):
        raise ConfigError("[cluster].node is required")

    cluster = ClusterConfig(
        node=str(cluster_data["node"]),
        storage=str(cluster_data.get("storage", "local-lvm")),
        pool=str(cluster_data.get("pool")) if cluster_data.get("pool") else None,
        bridge=str(cluster_data.get("bridge", "vmbr0")),
        snippets_storage=str(cluster_data.get("snippets_storage"))
        if cluster_data.get("snippets_storage")
        else None,
        ssh_public_key=str(cluster_data.get("ssh_public_key"))
        if cluster_data.get("ssh_public_key")
        else None,
        lxc_unprivileged=bool(cluster_data.get("lxc_unprivileged", True)),
    )

    # --- Flavors section --------------------------------------------------
    flavors_data = data.get("flavors", {})
    flavors: dict[str, FlavorConfig] = {}
    for key, val in flavors_data.items():
        if not isinstance(val, dict):
            raise ConfigError(f"[flavors].{key} must be a dictionary")
        flavors[key] = FlavorConfig(
            cores=int(val.get("cores", 2)),
            memory_mb=int(val.get("memory_mb", 4096)),
        )

    # --- Logging section (optional) ---------------------------------------
    logging_data = data.get("logging", {}) or {}
    logging_cfg = LoggingConfig(
        level=str(logging_data.get("level")).upper()
        if logging_data.get("level")
        else None,
        file=str(logging_data.get("file")) if logging_data.get("file") else None,
        json=bool(logging_data.get("json", False)),
        debug_dump=bool(logging_data.get("debug_dump", False)),
    )

    # --- VMID Range section (optional) ------------------------------------
    vmid_range_data = data.get("vmid_range", {}) or {}
    vmid_range_cfg = VMIDRangeConfig(
        min=int(vmid_range_data.get("min", 1100)),
        max=int(vmid_range_data.get("max", 1999)),
    )

    return Config(
        pve=pve,
        cluster=cluster,
        flavors=flavors,
        logging=logging_cfg,
        vmid_range=vmid_range_cfg,
    )


def load_logging_from_toml(path: str) -> LoggingConfig | None:
    """Load optional [logging] section from a TOML file.

    Returns a LoggingConfig when a [logging] section is present, otherwise None.
    This is a tolerant, best-effort helper used by the CLI to prefer TOML-based
    logging configuration when environment variables are cleared by the controller.
    """
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return None

    logging_data = data.get("logging") or {}
    if not logging_data:
        return None

    try:
        level = logging_data.get("level")
        # Accept either "file" or legacy "log_file" keys
        file = logging_data.get("file") or logging_data.get("log_file")
        json_flag = bool(logging_data.get("json", False))
        debug_dump = bool(logging_data.get("debug_dump", False))
        return LoggingConfig(
            level=level, file=file, json=json_flag, debug_dump=debug_dump
        )
    except Exception:
        return None
