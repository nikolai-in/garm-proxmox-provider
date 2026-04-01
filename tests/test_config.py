"""Tests for config loading."""

from __future__ import annotations

import tomllib
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

[defaults]
node = "pve1"
storage = "local-lvm"
cores = 2
memory_mb = 4096
disk_gb = 20
bridge = "vmbr0"
template_vmid = 9000
"""

FULL_TOML = MINIMAL_TOML + """\
snippets_storage = "shared-storage"
pool = "garm-pool"
ssh_public_key = "ssh-ed25519 AAAA test@example.com"
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
    assert cfg.defaults.node == "pve1"
    assert cfg.defaults.template_vmid == 9000
    assert cfg.defaults.cores == 2
    assert cfg.defaults.memory_mb == 4096
    assert cfg.defaults.disk_gb == 20
    assert cfg.defaults.bridge == "vmbr0"


def test_load_full_config(tmp_path: Path) -> None:
    path = _write_config(tmp_path, FULL_TOML)
    cfg = load_config(path)
    assert cfg.defaults.snippets_storage == "shared-storage"
    assert cfg.defaults.pool == "garm-pool"
    assert cfg.defaults.ssh_public_key is not None


def test_missing_pve_host(tmp_path: Path) -> None:
    bad = """\
[pve]
user = "root@pam"
token_name = "garm"
token_value = "aaaa"

[defaults]
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
