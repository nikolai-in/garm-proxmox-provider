"""Tests for cloud_init user-data rendering (Linux/Windows × GitHub/Gitea)."""

from __future__ import annotations

from typing import Any

from garm_proxmox_provider.cloud_init import (
    _is_gitea,
    render_lxc_env_vars,
    render_userdata,
)
from garm_proxmox_provider.config import ClusterConfig
from garm_proxmox_provider.models import BootstrapInstance


def _mock_cluster_config(**kwargs: Any) -> ClusterConfig:
    base = dict(node="pve1", pool="garm")
    base.update(kwargs)
    cluster_keys = [
        "node",
        "storage",
        "pool",
        "bridge",
        "snippets_storage",
        "ssh_public_key",
    ]
    cluster_data = {k: v for k, v in base.items() if k in cluster_keys}
    return ClusterConfig(**cluster_data)  # type: ignore[arg-type]


def _bootstrap(**kwargs: Any) -> BootstrapInstance:
    base = dict(
        name="runner-test",
        tools=[],
        repo_url="https://github.com/myorg/myrepo",
        metadata_url="https://garm.example.com/api/v1/metadata",
        callback_url="https://garm.example.com/api/v1/instances/callback",
        instance_token="tok-abc123",
        pool_id="pool-111",
        controller_id="ctrl-222",
        os_type="linux",
        os_arch="amd64",
        labels=["self-hosted", "linux"],
    )
    base.update(kwargs)
    return BootstrapInstance(**base)


# ---------------------------------------------------------------------------
# _is_gitea detection
# ---------------------------------------------------------------------------


def test_is_gitea_by_url() -> None:
    b = _bootstrap(repo_url="https://gitea.example.com/org/repo")
    assert _is_gitea(b) is True


def test_is_github_by_url() -> None:
    b = _bootstrap(repo_url="https://github.com/org/repo")
    assert _is_gitea(b) is False


def test_is_gitea_explicit_extra_spec() -> None:
    b = _bootstrap(extra_specs={"forge_type": "gitea"})
    assert _is_gitea(b) is True


def test_is_forgejo_explicit_extra_spec() -> None:
    b = _bootstrap(extra_specs={"forge_type": "forgejo"})
    assert _is_gitea(b) is True


def test_is_github_explicit_extra_spec_overrides_url() -> None:
    """An explicit forge_type=github on a non-github URL → not Gitea."""
    b = _bootstrap(
        repo_url="https://gitea.example.com/org/repo",
        extra_specs={"forge_type": "github"},
    )
    assert _is_gitea(b) is False


# ---------------------------------------------------------------------------
# Linux / GitHub
# ---------------------------------------------------------------------------


def test_linux_github_cloud_config_header() -> None:
    b = _bootstrap()
    ud = render_userdata(b, "1001", _mock_cluster_config())
    assert ud.startswith("#cloud-config")


def test_linux_github_contains_config_sh() -> None:
    b = _bootstrap()
    ud = render_userdata(b, "1001", _mock_cluster_config())
    assert "./config.sh" in ud
    assert "--url 'https://github.com/myorg/myrepo'" in ud
    assert "--name 'runner-test'" in ud
    assert "--labels 'self-hosted,linux'" in ud


def test_linux_github_contains_callback() -> None:
    b = _bootstrap()
    ud = render_userdata(b, "1001", _mock_cluster_config())
    assert "callback_url" not in ud  # placeholder replaced
    assert "https://garm.example.com/api/v1/instances/callback" in ud
    assert '"provider_id":"1001"' in ud


def test_linux_github_contains_token_fetch() -> None:
    b = _bootstrap()
    ud = render_userdata(b, "1001", _mock_cluster_config())
    assert "runner-registration-token" in ud
    assert "tok-abc123" in ud


def test_linux_github_no_download_steps() -> None:
    """The slimmed script must not contain any tarball download logic."""
    b = _bootstrap()
    ud = render_userdata(b, "1001", _mock_cluster_config())
    assert "curl" in ud  # still uses curl for token/callback
    assert "tar xzf" not in ud
    assert "apt-get" not in ud


def test_linux_github_svc_sh_start() -> None:
    b = _bootstrap()
    ud = render_userdata(b, "1001", _mock_cluster_config())
    assert "./svc.sh start" in ud


def test_linux_github_ssh_key_injected() -> None:
    b = _bootstrap()
    ud = render_userdata(
        b, "1001", _mock_cluster_config(ssh_public_key="ssh-ed25519 AAAA test@h")
    )
    assert "ssh_authorized_keys" in ud
    assert "ssh-ed25519 AAAA test@h" in ud


# ---------------------------------------------------------------------------
# Linux / Gitea
# ---------------------------------------------------------------------------


def test_linux_gitea_uses_act_runner() -> None:
    b = _bootstrap(repo_url="https://gitea.example.com/org/repo")
    ud = render_userdata(b, "1001", _mock_cluster_config())
    assert "act_runner" in ud
    assert "./act_runner register" in ud
    assert "--instance 'https://gitea.example.com/org/repo'" in ud
    assert "--no-interactive" in ud


def test_linux_gitea_systemctl_start() -> None:
    b = _bootstrap(repo_url="https://gitea.example.com/org/repo")
    ud = render_userdata(b, "1001", _mock_cluster_config())
    assert "systemctl start act_runner" in ud


def test_linux_gitea_no_config_sh() -> None:
    b = _bootstrap(repo_url="https://gitea.example.com/org/repo")
    ud = render_userdata(b, "1001", _mock_cluster_config())
    assert "config.sh" not in ud


# ---------------------------------------------------------------------------
# Windows / GitHub (cloudbase-init)
# ---------------------------------------------------------------------------


def test_windows_github_ps1_sysnative_header() -> None:
    b = _bootstrap(os_type="windows")
    ud = render_userdata(b, "2001", _mock_cluster_config())
    assert ud.startswith("#ps1_sysnative")


def test_windows_github_config_cmd() -> None:
    b = _bootstrap(os_type="windows")
    ud = render_userdata(b, "2001", _mock_cluster_config())
    assert "config.cmd" in ud
    assert "$RepoUrl" in ud
    assert "$RunnerToken" in ud
    assert "$RunnerName" in ud
    assert "$RunnerLabels" in ud


def test_windows_github_svc_cmd_start() -> None:
    b = _bootstrap(os_type="windows")
    ud = render_userdata(b, "2001", _mock_cluster_config())
    assert "svc.cmd start" in ud


def test_windows_github_callback() -> None:
    b = _bootstrap(os_type="windows")
    ud = render_userdata(b, "2001", _mock_cluster_config())
    assert "Invoke-RestMethod" in ud
    assert "$ProviderId" in ud
    assert "$RunnerName" in ud


def test_windows_github_provider_id_substituted() -> None:
    b = _bootstrap(os_type="windows")
    ud = render_userdata(b, "2001", _mock_cluster_config())
    assert "2001" in ud
    assert "{provider_id}" not in ud


def test_windows_github_no_bash_shebang() -> None:
    b = _bootstrap(os_type="windows")
    ud = render_userdata(b, "2001", _mock_cluster_config())
    assert "#!/bin/bash" not in ud


# ---------------------------------------------------------------------------
# Windows / Gitea
# ---------------------------------------------------------------------------


def test_windows_gitea_act_runner_exe() -> None:
    b = _bootstrap(
        os_type="windows",
        repo_url="https://gitea.example.com/org/repo",
    )
    ud = render_userdata(b, "2001", _mock_cluster_config())
    assert "act_runner.exe" in ud
    assert "--instance $RepoUrl" in ud
    assert "--no-interactive" in ud


def test_windows_gitea_start_service() -> None:
    b = _bootstrap(
        os_type="windows",
        repo_url="https://gitea.example.com/org/repo",
    )
    ud = render_userdata(b, "2001", _mock_cluster_config())
    assert "Start-Service act_runner" in ud


def test_windows_gitea_no_config_cmd() -> None:
    b = _bootstrap(
        os_type="windows",
        repo_url="https://gitea.example.com/org/repo",
    )
    ud = render_userdata(b, "2001", _mock_cluster_config())
    assert "config.cmd" not in ud


# ---------------------------------------------------------------------------
# Labels fallback to pool_id when no labels provided
# ---------------------------------------------------------------------------


def test_linux_labels_fallback_to_pool_id() -> None:
    b = _bootstrap(labels=[])
    ud = render_userdata(b, "1001", _mock_cluster_config())
    assert "--labels 'pool-111'" in ud


def test_windows_labels_fallback_to_pool_id() -> None:
    b = _bootstrap(os_type="windows", labels=[])
    ud = render_userdata(b, "2001", _mock_cluster_config())
    assert "$RunnerLabels = 'pool-111'" in ud


# ---------------------------------------------------------------------------
# render_lxc_env_vars — GitHub
# ---------------------------------------------------------------------------


def test_lxc_env_vars_github_keys_present() -> None:
    b = _bootstrap()
    env = render_lxc_env_vars(b, "1001")
    for key in (
        "GARM_METADATA_URL",
        "GARM_INSTANCE_TOKEN",
        "GARM_REPO_URL",
        "GARM_LABELS",
        "GARM_NAME",
        "GARM_CALLBACK_URL",
        "GARM_PROVIDER_ID",
        "GARM_FORGE_TYPE",
    ):
        assert key in env, f"Missing key: {key}"


def test_lxc_env_vars_github_values() -> None:
    b = _bootstrap()
    env = render_lxc_env_vars(b, "1001")
    assert env["GARM_PROVIDER_ID"] == "1001"
    assert env["GARM_FORGE_TYPE"] == "github"
    assert env["GARM_NAME"] == "runner-test"
    assert env["GARM_REPO_URL"] == "https://github.com/myorg/myrepo"
    assert env["GARM_INSTANCE_TOKEN"] == "tok-abc123"
    assert "callback" in env["GARM_CALLBACK_URL"]


def test_lxc_env_vars_github_labels_joined() -> None:
    b = _bootstrap()
    env = render_lxc_env_vars(b, "1001")
    assert env["GARM_LABELS"] == "self-hosted,linux"


def test_lxc_env_vars_labels_fallback_to_pool_id() -> None:
    b = _bootstrap(labels=[])
    env = render_lxc_env_vars(b, "1001")
    assert env["GARM_LABELS"] == "pool-111"


def test_lxc_env_vars_metadata_url_stripped() -> None:
    b = _bootstrap(metadata_url="https://garm.example.com/api/v1/metadata/")
    env = render_lxc_env_vars(b, "1001")
    assert not env["GARM_METADATA_URL"].endswith("/")


# ---------------------------------------------------------------------------
# render_lxc_env_vars — Gitea
# ---------------------------------------------------------------------------


def test_lxc_env_vars_gitea_forge_type() -> None:
    b = _bootstrap(repo_url="https://gitea.example.com/org/repo")
    env = render_lxc_env_vars(b, "2001")
    assert env["GARM_FORGE_TYPE"] == "gitea"


def test_lxc_env_vars_gitea_explicit_extra_spec() -> None:
    b = _bootstrap(extra_specs={"forge_type": "forgejo"})
    env = render_lxc_env_vars(b, "2001")
    assert env["GARM_FORGE_TYPE"] == "gitea"


def test_lxc_env_vars_provider_id_is_string() -> None:
    b = _bootstrap()
    env = render_lxc_env_vars(b, "9999")
    assert isinstance(env["GARM_PROVIDER_ID"], str)
    assert env["GARM_PROVIDER_ID"] == "9999"
