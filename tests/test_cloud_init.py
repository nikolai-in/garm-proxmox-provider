from garm_proxmox_provider.cloud_init import _is_gitea, render_userdata
from garm_proxmox_provider.config import ClusterConfig
from garm_proxmox_provider.models import BootstrapInstance


def _bootstrap(
    os_type: str = "linux",
    repo_url: str = "https://github.com/org/repo",
    labels: list[str] | None = None,
    extra_specs: dict | None = None,
) -> BootstrapInstance:
    if labels is None:
        labels = ["self-hosted", "linux"]
    return BootstrapInstance(
        name="runner-1",
        tools=[],
        repo_url=repo_url,
        metadata_url="https://garm.example.com/api/v1/instances/",
        callback_url="https://garm.example.com/api/v1/instances/callback",
        instance_token="s3cr3t",
        pool_id="pool-111",
        controller_id="ctrl-222",
        os_type=os_type,
        os_arch="amd64",
        flavor="default",
        image="ubuntu",
        labels=labels,
        extra_specs=extra_specs or {},
    )


def _mock_cluster_config(ssh_public_key: str = "") -> ClusterConfig:
    return ClusterConfig(
        node="pve1",
        storage="local-lvm",
        pool="",
        bridge="vmbr0",
        ssh_public_key=ssh_public_key,
    )
def test_is_gitea_implicit() -> None:
    b = _bootstrap(repo_url="https://gitea.example.com/org/repo")
    assert _is_gitea(b) is True

    b2 = _bootstrap(repo_url="https://github.com/org/repo")
    assert _is_gitea(b2) is False


def test_is_gitea_explicit() -> None:
    b = _bootstrap(
        repo_url="https://github.com/org/repo", extra_specs={"forge_type": "gitea"}
    )
    assert _is_gitea(b) is True

    b2 = _bootstrap(
        repo_url="https://gitea.example.com/org/repo",
        extra_specs={"forge_type": "github"},
    )
    assert _is_gitea(b2) is False


def test_linux_github_userdata() -> None:
    b = _bootstrap()
    ud = render_userdata(b, "1001", _mock_cluster_config())
    assert "#!/bin/bash" in ud
    assert 'export METADATA_URL="https://garm.example.com/api/v1/instances"' in ud
    assert (
        'export CALLBACK_URL="https://garm.example.com/api/v1/instances/callback"' in ud
    )
    assert 'export BEARER_TOKEN="s3cr3t"' in ud
    assert 'export REPO_URL="https://github.com/org/repo"' in ud
    assert 'export RUNNER_NAME="runner-1"' in ud
    assert 'export RUNNER_LABELS="self-hosted,linux"' in ud
    assert 'export FORGE_TYPE="github"' in ud
    assert 'export PROVIDER_ID="1001"' in ud
    # No baked-in script path — bootstrap comes entirely from GARM.
    assert "bash /opt/garm/scripts/startup-linux.sh" not in ud


def test_linux_gitea_userdata() -> None:
    b = _bootstrap(repo_url="https://gitea.example.com/org/repo")
    ud = render_userdata(b, "1001", _mock_cluster_config())
    assert 'export FORGE_TYPE="gitea"' in ud


def test_linux_ssh_key_injected() -> None:
    b = _bootstrap()
    ud = render_userdata(
        b, "1001", _mock_cluster_config(ssh_public_key="ssh-ed25519 AAAA test@h")
    )
    assert "mkdir -p /home/runner/.ssh" in ud
    assert 'echo "ssh-ed25519 AAAA test@h" >> /home/runner/.ssh/authorized_keys' in ud


def test_linux_ssh_key_from_extra_specs() -> None:
    """ssh_public_key in extra_specs takes precedence over cluster config."""
    b = _bootstrap(extra_specs={"ssh_public_key": "ssh-ed25519 EXTRA extra@h"})
    ud = render_userdata(b, "1001", _mock_cluster_config(ssh_public_key="ssh-ed25519 DEFAULT def@h"))
    assert 'echo "ssh-ed25519 EXTRA extra@h" >> /home/runner/.ssh/authorized_keys' in ud
    assert "DEFAULT" not in ud


def test_windows_github_userdata() -> None:
    b = _bootstrap(os_type="windows")
    ud = render_userdata(b, "2001", _mock_cluster_config())
    assert "#ps1_sysnative" in ud
    assert '$env:METADATA_URL = "https://garm.example.com/api/v1/instances"' in ud
    assert (
        '$env:CALLBACK_URL = "https://garm.example.com/api/v1/instances/callback"' in ud
    )
    assert '$env:BEARER_TOKEN = "s3cr3t"' in ud
    assert '$env:REPO_URL = "https://github.com/org/repo"' in ud
    assert '$env:RUNNER_NAME = "runner-1"' in ud
    assert '$env:RUNNER_LABELS = "self-hosted,linux"' in ud
    assert '$env:FORGE_TYPE = "github"' in ud
    assert '$env:PROVIDER_ID = "2001"' in ud
    # No baked-in script path — bootstrap comes entirely from GARM.
    assert "startup-windows.ps1" not in ud


def test_windows_gitea_userdata() -> None:
    b = _bootstrap(os_type="windows", repo_url="https://gitea.example.com/org/repo")
    ud = render_userdata(b, "2001", _mock_cluster_config())
    assert '$env:FORGE_TYPE = "gitea"' in ud


def test_linux_labels_fallback_to_pool_id() -> None:
    b = _bootstrap(labels=[])
    ud = render_userdata(b, "1001", _mock_cluster_config())
    assert 'export RUNNER_LABELS="pool-111"' in ud


def test_windows_labels_fallback_to_pool_id() -> None:
    b = _bootstrap(os_type="windows", labels=[])
    ud = render_userdata(b, "2001", _mock_cluster_config())
    assert '$env:RUNNER_LABELS = "pool-111"' in ud


def test_linux_runner_install_template_decoded() -> None:
    """When runner_install_template is in extra_specs, it is decoded and used directly."""
    import base64

    custom_script = "#!/bin/bash\necho 'custom runner install'\n"
    b64 = base64.b64encode(custom_script.encode()).decode()
    b = _bootstrap(extra_specs={"runner_install_template": b64})
    ud = render_userdata(b, "1001", _mock_cluster_config())
    assert "echo 'custom runner install'" in ud
    # The pre-baked fallback path should NOT be called.
    assert "bash /opt/garm/scripts/startup-linux.sh" not in ud


def test_linux_runner_install_template_bad_b64_produces_no_body() -> None:
    """Invalid base64 in runner_install_template: warning is logged, body is empty."""
    b = _bootstrap(extra_specs={"runner_install_template": "NOT_VALID_BASE64!!!"})
    ud = render_userdata(b, "1001", _mock_cluster_config())
    # No baked-in fallback; env vars are still present.
    assert "bash /opt/garm/scripts/startup-linux.sh" not in ud
    assert "startup-windows.ps1" not in ud
    assert "export METADATA_URL" in ud
