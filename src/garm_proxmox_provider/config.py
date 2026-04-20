"""TOML configuration loader for the GARM Proxmox provider."""

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
    # Optional QGA SSH fallback for bootstrap execution
    qm_ssh_fallback: bool = False
    qm_ssh_user: str = "root"
    qm_ssh_identity_file: str | None = None


@dataclass
class FlavorConfig:
    cores: int = 2
    memory_mb: int = 4096


@dataclass
class ImageConfig:
    type: str = "vm"  # "vm" or "lxc"
    lxc_unprivileged: bool = True


@dataclass
class Config:
    pve: PVEConfig
    cluster: ClusterConfig
    flavors: dict[str, FlavorConfig] = field(default_factory=dict)
    images: dict[str, ImageConfig] = field(default_factory=dict)

    def get_flavor(self, name: str) -> FlavorConfig:
        """Return the requested flavor, fallback to 'default', or a baseline flavor."""
        if name and name in self.flavors:
            return self.flavors[name]
        if "default" in self.flavors:
            return self.flavors["default"]
        return FlavorConfig()

    def get_image(self, name: str) -> ImageConfig:
        """Return the requested image configuration."""
        if not name:
            raise ConfigError(
                "No image name provided by GARM. Please configure an image in your GARM pool."
            )
        if name not in self.images:
            raise ConfigError(
                f"Image '{name}' not found in provider [images] configuration."
            )
        return self.images[name]


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
        qm_ssh_fallback=bool(cluster_data.get("qm_ssh_fallback", False)),
        qm_ssh_user=str(cluster_data.get("qm_ssh_user", "root")),
        qm_ssh_identity_file=str(cluster_data.get("qm_ssh_identity_file"))
        if cluster_data.get("qm_ssh_identity_file")
        else None,
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

    # --- Images section ---------------------------------------------------
    images_data = data.get("images", {})
    images: dict[str, ImageConfig] = {}
    for key, val in images_data.items():
        if not isinstance(val, dict):
            raise ConfigError(f"[images].{key} must be a dictionary")

        type_val = str(val.get("type", "vm"))
        if type_val not in ("vm", "lxc"):
            raise ConfigError(f"[images].{key}.type must be 'vm' or 'lxc'")

        images[key] = ImageConfig(
            type=type_val,
            lxc_unprivileged=bool(val.get("lxc_unprivileged", True)),
        )

    return Config(
        pve=pve,
        cluster=cluster,
        flavors=flavors,
        images=images,
    )
