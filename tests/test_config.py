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


def test_missing_template_source(tmp_path: Path) -> None:
    """Config with neither template_vmid nor [pool_templates] must fail."""
    bad = """\
[pve]
host = "https://pve.example.com:8006"
user = "root@pam"
token_name = "garm"
token_value = "aaaa"

[defaults]
node = "pve1"
storage = "local-lvm"
"""
    with pytest.raises(ConfigError, match="template_vmid"):
        load_config(_write_config(tmp_path, bad))


def test_pool_templates_loaded(tmp_path: Path) -> None:
    toml = """\
[pve]
host = "https://pve.example.com:8006"
user = "root@pam"
token_name = "garm"
token_value = "aaaa"

[defaults]
node = "pve1"

[pool_templates]
"linux/amd64" = 9000
"linux/arm64" = 9001
"windows/amd64" = 9002
"""
    cfg = load_config(_write_config(tmp_path, toml))
    assert cfg.defaults.pool_templates == {
        "linux/amd64": 9000,
        "linux/arm64": 9001,
        "windows/amd64": 9002,
    }
    assert cfg.defaults.template_vmid is None


def test_pool_templates_only_no_default_vmid_ok(tmp_path: Path) -> None:
    """pool_templates alone (without template_vmid) is a valid config."""
    toml = """\
[pve]
host = "https://pve.example.com:8006"
user = "root@pam"
token_name = "garm"
token_value = "aaaa"

[defaults]
node = "pve1"

[pool_templates]
"linux/amd64" = 9000
"""
    cfg = load_config(_write_config(tmp_path, toml))
    assert cfg.defaults.template_vmid is None
    assert cfg.defaults.pool_templates["linux/amd64"] == 9000


def test_pool_templates_invalid_vmid(tmp_path: Path) -> None:
    toml = """\
[pve]
host = "https://pve.example.com:8006"
user = "root@pam"
token_name = "garm"
token_value = "aaaa"

[defaults]
node = "pve1"

[pool_templates]
"linux/amd64" = "not-an-int"
"""
    with pytest.raises(ConfigError, match="integer VMID"):
        load_config(_write_config(tmp_path, toml))


# ---------------------------------------------------------------------------
# LXC instance_type
# ---------------------------------------------------------------------------


def test_default_instance_type_is_vm(tmp_path: Path) -> None:
    cfg = load_config(_write_config(tmp_path, MINIMAL_TOML))
    assert cfg.defaults.instance_type == "vm"
    assert cfg.defaults.lxc_unprivileged is True


def test_lxc_instance_type_loads(tmp_path: Path) -> None:
    toml = MINIMAL_TOML + """\
instance_type = "lxc"
lxc_unprivileged = false
"""
    cfg = load_config(_write_config(tmp_path, toml))
    assert cfg.defaults.instance_type == "lxc"
    assert cfg.defaults.lxc_unprivileged is False


def test_invalid_instance_type_raises(tmp_path: Path) -> None:
    toml = MINIMAL_TOML + 'instance_type = "docker"\n'
    with pytest.raises(ConfigError, match="instance_type"):
        load_config(_write_config(tmp_path, toml))
