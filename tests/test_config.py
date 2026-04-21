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

[flavors.default]
cores = 2
memory_mb = 4096
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
lxc_unprivileged = false

[flavors.default]
cores = 2
memory_mb = 4096
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
    assert cfg.cluster.bridge == "vmbr0"
    assert cfg.cluster.lxc_unprivileged is True
    # No images section — config has no images dict.
    assert not hasattr(cfg, "images")


def test_load_full_config(tmp_path: Path) -> None:
    path = _write_config(tmp_path, FULL_TOML)
    cfg = load_config(path)
    assert cfg.cluster.snippets_storage == "shared-storage"
    assert cfg.cluster.pool == "garm-pool"
    assert cfg.cluster.ssh_public_key == "ssh-ed25519 AAAA test@example.com"
    assert cfg.cluster.lxc_unprivileged is False


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


def test_lxc_unprivileged_defaults_to_true(tmp_path: Path) -> None:
    """When lxc_unprivileged is omitted it defaults to True."""
    cfg = load_config(_write_config(tmp_path, MINIMAL_TOML))
    assert cfg.cluster.lxc_unprivileged is True


def test_lxc_unprivileged_can_be_false(tmp_path: Path) -> None:
    cfg = load_config(_write_config(tmp_path, FULL_TOML))
    assert cfg.cluster.lxc_unprivileged is False


def test_no_qm_ssh_fields_in_cluster(tmp_path: Path) -> None:
    """ClusterConfig must not have qm_ssh_* fields after the purge."""
    cfg = load_config(_write_config(tmp_path, MINIMAL_TOML))
    assert not hasattr(cfg.cluster, "qm_ssh_fallback")
    assert not hasattr(cfg.cluster, "qm_ssh_user")
    assert not hasattr(cfg.cluster, "qm_ssh_identity_file")

