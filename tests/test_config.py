"""Tests for config loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from garm_proxmox_provider.config import Config, ConfigError, load_config

MINIMAL_TOML = """\
[pve]
host = "https://pve.example.com:8006"
user = "root@pam"
token_name = "garm"
token_value = "aaaa-bbbb-cccc-dddd"
verify_ssl = false

[cluster]
node = "pve1"
storage = "local-lvm"
bridge = "vmbr0"

[images.default]

[flavors.default]
cores = 2
memory_mb = 4096
disk_gb = 20
"""

FULL_TOML = """\
[pve]
host = "https://pve.example.com:8006"
user = "root@pam"
token_name = "garm"
token_value = "aaaa-bbbb-cccc-dddd"
verify_ssl = false

[cluster]
node = "pve1"
storage = "local-lvm"
bridge = "vmbr0"
snippets_storage = "shared-storage"
pool = "garm-pool"
ssh_public_key = "ssh-ed25519 AAAA test@example.com"

[images.default]

[flavors.default]
cores = 2
memory_mb = 4096
disk_gb = 20
"""


def _write_config(tmp_path: Path, content: str) -> str:
    p = tmp_path / "provider.toml"
    p.write_text(content)
    return str(p)


def test_load_minimal_config(tmp_path: Path) -> None:
    path = _write_config(tmp_path, MINIMAL_TOML)
    cfg = load_config(path)
    assert isinstance(cfg, Config)
    assert cfg.pve.host == "https://pve.example.com:8006"
    assert cfg.pve.user == "root@pam"
    assert cfg.pve.token_name == "garm"
    assert cfg.pve.token_value == "aaaa-bbbb-cccc-dddd"
    assert cfg.pve.verify_ssl is False
    assert cfg.cluster.node == "pve1"
    assert cfg.cluster.storage == "local-lvm"
    assert cfg.flavors["default"].cores == 2
    assert cfg.flavors["default"].memory_mb == 4096
    assert cfg.flavors["default"].disk_gb == 20
    assert cfg.cluster.bridge == "vmbr0"


def test_load_full_config(tmp_path: Path) -> None:
    path = _write_config(tmp_path, FULL_TOML)
    cfg = load_config(path)
    assert cfg.cluster.snippets_storage == "shared-storage"
    assert cfg.cluster.pool == "garm-pool"
    assert cfg.cluster.ssh_public_key == "ssh-ed25519 AAAA test@example.com"


def test_missing_pve_host(tmp_path: Path) -> None:
    bad = """\
[pve]
user = "root@pam"
token_name = "garm"
token_value = "aaaa"

[cluster]
node = "pve1"
"""
    with pytest.raises(ConfigError, match="host"):
        load_config(_write_config(tmp_path, bad))


def test_missing_defaults_node(tmp_path: Path) -> None:
    bad = """\
[pve]
host = "https://pve.example.com:8006"
user = "root@pam"
token_name = "garm"
token_value = "aaaa"

[defaults]
storage = "local-lvm"
"""
    with pytest.raises(ConfigError, match="node"):
        load_config(_write_config(tmp_path, bad))


def test_file_not_found() -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config("/nonexistent/path/provider.toml")


def test_invalid_toml(tmp_path: Path) -> None:
    bad_path = tmp_path / "bad.toml"
    bad_path.write_text("this is not valid toml [[[[")
    with pytest.raises(ConfigError, match="Invalid TOML"):
        load_config(str(bad_path))


def test_missing_template_source(tmp_path: Path) -> None:
    """Config with no images must fail on get_image()."""
    bad = """\
[pve]
host = "https://pve.example.com:8006"
user = "root@pam"
token_name = "garm"
token_value = "aaaa"

[cluster]
node = "pve1"
storage = "local-lvm"
"""
    cfg = load_config(_write_config(tmp_path, bad))
    with pytest.raises(ConfigError, match="not found in provider"):
        cfg.get_image("default")


def test_images_config(tmp_path: Path) -> None:
    toml = """\
[pve]
host = "pve.example.com"
user = "root@pam"
token_name = "garm"
token_value = "aaaa"

[cluster]
node = "pve1"

[images.default]

[images.win]
"""
    cfg = load_config(_write_config(tmp_path, toml))


def test_invalid_image_type_raises(tmp_path: Path) -> None:
    toml = """\
[pve]
host = "https://pve.example.com:8006"
user = "root@pam"
token_name = "garm"
token_value = "aaaa"

[cluster]
node = "pve1"

[images.broken]
type = "docker"
"""
    with pytest.raises(ConfigError, match="must be 'vm' or 'lxc'"):
        load_config(_write_config(tmp_path, toml))


# ---------------------------------------------------------------------------
# LXC instance_type
# ---------------------------------------------------------------------------


def test_default_instance_type_is_vm(tmp_path: Path) -> None:
    cfg = load_config(_write_config(tmp_path, MINIMAL_TOML))
    assert cfg.images["default"].type == "vm"
    assert cfg.images["default"].lxc_unprivileged is True


def test_lxc_instance_type_loads(tmp_path: Path) -> None:
    toml = """\
[pve]
host = "pve.example.com"
user = "root@pam"
token_name = "garm"
token_value = "aaaa"

[cluster]
node = "pve1"

[images.default]
type = "lxc"
lxc_unprivileged = false
"""
    cfg = load_config(_write_config(tmp_path, toml))
    assert cfg.images["default"].type == "lxc"
    assert cfg.images["default"].lxc_unprivileged is False
