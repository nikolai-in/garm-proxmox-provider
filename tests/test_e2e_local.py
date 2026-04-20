"""Local-only end-to-end tests for the create/start/bootstrap flows.

These tests use a fake in-process Proxmox implementation so no real Proxmox
server is required.  Run them with::

    pytest -m local

They are excluded from the default CI test run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from garm_proxmox_provider.cli import cli

# ---------------------------------------------------------------------------
# Minimal TOML configs
# ---------------------------------------------------------------------------

_TOML_QEMU = """\
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

[images.ubuntu-runner]
type = "vm"

[flavors.default]
cores = 2
memory_mb = 4096
"""

_TOML_LXC = """\
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

[images.ubuntu-lxc]
type = "lxc"

[flavors.default]
cores = 2
memory_mb = 4096
"""

_TOML_QM_SSH = """\
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
qm_ssh_fallback = true
qm_ssh_user = "root"

[images.ubuntu-runner]
type = "vm"

[flavors.default]
cores = 2
memory_mb = 4096
"""

# ---------------------------------------------------------------------------
# Bootstrap payloads
# ---------------------------------------------------------------------------

_BOOTSTRAP_QEMU: dict[str, Any] = {
    "name": "runner-e2e",
    "tools": [
        {
            "os": "linux",
            "arch": "amd64",
            "download_url": "https://example.com/runner.tar.gz",
            "filename": "runner.tar.gz",
            "sha256_checksum": "",
        }
    ],
    "repo_url": "https://github.com/myorg/myrepo",
    "metadata_url": "https://garm.example.com/api/v1/metadata",
    "callback_url": "https://garm.example.com/api/v1/instances/callback",
    "instance_token": "secret-token",
    "pool_id": "pool-e2e",
    "controller_id": "ctrl-e2e",
    "os_type": "linux",
    "os_arch": "amd64",
    "image": "ubuntu-runner",
    "flavor": "default",
    "labels": ["self-hosted"],
}

_BOOTSTRAP_LXC: dict[str, Any] = {
    **_BOOTSTRAP_QEMU,
    "image": "ubuntu-lxc",
}

# ---------------------------------------------------------------------------
# Fake Proxmox implementation
# ---------------------------------------------------------------------------

_TEMPLATE_VMID = 9000
_NEW_VMID = 101
_NODE = "pve1"


class _FakeTaskStatusProxy:
    def get(self) -> dict[str, Any]:
        return {"status": "stopped", "exitstatus": "OK"}


class _FakeTaskProxy:
    @property
    def status(self) -> _FakeTaskStatusProxy:
        return _FakeTaskStatusProxy()


class _FakeAgentProxy:
    """Tracks calls to agent.ping and agent.exec for assertions."""

    def __init__(self) -> None:
        self.ping_calls: int = 0
        self.exec_calls: list[dict[str, Any]] = []

    @property
    def ping(self) -> "_FakePingProxy":
        return _FakePingProxy(self)

    @property
    def exec(self) -> "_FakeExecProxy":
        return _FakeExecProxy(self)


class _FakePingProxy:
    def __init__(self, agent: _FakeAgentProxy) -> None:
        self._agent = agent

    def post(self) -> None:
        self._agent.ping_calls += 1


class _FakeExecProxy:
    def __init__(self, agent: _FakeAgentProxy) -> None:
        self._agent = agent

    def post(self, **kwargs: Any) -> dict[str, Any]:
        self._agent.exec_calls.append(kwargs)
        return {"pid": 1234}


class _FakeLxcExecProxy:
    """Tracks calls to lxc.exec for assertions."""

    def __init__(self, recorder: list[dict[str, Any]]) -> None:
        self._recorder = recorder

    def post(self, **kwargs: Any) -> None:
        self._recorder.append(kwargs)


class _FakeVmProxy:
    def __init__(self, vmid: int, agent: _FakeAgentProxy | None = None) -> None:
        self._vmid = vmid
        self._agent = agent or _FakeAgentProxy()

    @property
    def clone(self) -> "_FakeCloneProxy":
        return _FakeCloneProxy()

    @property
    def config(self) -> "_FakeConfigProxy":
        return _FakeConfigProxy(self._vmid)

    @property
    def status(self) -> "_FakeStatusProxy":
        return _FakeStatusProxy()

    @property
    def agent(self) -> _FakeAgentProxy:
        return self._agent


class _FakeCloneProxy:
    def post(self, **kwargs: Any) -> str:
        return f"UPID:pve1:00001234:00000001:clone:{kwargs.get('newid', 0)}"


class _FakeConfigProxy:
    _DESCRIPTIONS: dict[int, str] = {}

    def __init__(self, vmid: int) -> None:
        self._vmid = vmid

    def get(self) -> dict[str, Any]:
        desc = _FakeConfigProxy._DESCRIPTIONS.get(self._vmid, "")
        return {"name": f"vm-{self._vmid}", "description": desc}

    def post(self, **kwargs: Any) -> None:
        if "description" in kwargs:
            _FakeConfigProxy._DESCRIPTIONS[self._vmid] = kwargs["description"]

    put = post


class _FakeStatusProxy:
    @property
    def start(self) -> "_FakeActionProxy":
        return _FakeActionProxy()

    @property
    def stop(self) -> "_FakeActionProxy":
        return _FakeActionProxy()


class _FakeActionProxy:
    def post(self, **kwargs: Any) -> str:
        return ""


class _FakeLxcProxy:
    def __init__(self, vmid: int, exec_recorder: list[dict[str, Any]]) -> None:
        self._vmid = vmid
        self._exec_recorder = exec_recorder

    @property
    def clone(self) -> _FakeCloneProxy:
        return _FakeCloneProxy()

    @property
    def config(self) -> _FakeConfigProxy:
        return _FakeConfigProxy(self._vmid)

    @property
    def status(self) -> _FakeStatusProxy:
        return _FakeStatusProxy()

    @property
    def exec(self) -> _FakeLxcExecProxy:
        return _FakeLxcExecProxy(self._exec_recorder)

    def interfaces(self) -> list[Any]:
        return []


class FakeProxmox:
    """Minimal fake for proxmoxer.ProxmoxAPI used by the QEMU path."""

    def __init__(self) -> None:
        self._vmid_counter = iter([_NEW_VMID, _NEW_VMID + 1, _NEW_VMID + 2])
        self._agent = _FakeAgentProxy()
        self._lxc_exec: list[dict[str, Any]] = []
        _FakeConfigProxy._DESCRIPTIONS.clear()

    # cluster.resources.get(type="vm")
    @property
    def cluster(self) -> "_FakeClusterProxy":
        return _FakeClusterProxy(self._vmid_counter)

    def nodes(self, node: str) -> "_FakeNodeProxy":
        return _FakeNodeProxy(self._agent, self._lxc_exec)

    def version(self) -> dict[str, str]:  # pragma: no cover
        return {"version": "8.0"}


class _FakeClusterProxy:
    def __init__(self, vmid_counter: Any) -> None:
        self._counter = vmid_counter

    @property
    def resources(self) -> "_FakeResourcesProxy":
        return _FakeResourcesProxy()

    @property
    def nextid(self) -> "_FakeNextidProxy":
        return _FakeNextidProxy(self._counter)


class _FakeResourcesProxy:
    def get(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "vmid": _TEMPLATE_VMID,
                "name": "ubuntu-runner",
                "node": _NODE,
                "type": "qemu",
                "template": 1,
                "status": "stopped",
            }
        ]


class _FakeNextidProxy:
    def __init__(self, counter: Any) -> None:
        self._counter = counter

    def get(self) -> int:
        return next(self._counter)


class _FakeNodeProxy:
    def __init__(
        self,
        agent: _FakeAgentProxy,
        lxc_exec: list[dict[str, Any]],
    ) -> None:
        self._agent = agent
        self._lxc_exec = lxc_exec

    def qemu(self, vmid: int) -> _FakeVmProxy:
        return _FakeVmProxy(vmid, self._agent)

    def lxc(self, vmid: int) -> _FakeLxcProxy:
        return _FakeLxcProxy(vmid, self._lxc_exec)

    def tasks(self, upid: str) -> _FakeTaskProxy:
        return _FakeTaskProxy()

    def storage(self, name: str) -> Any:  # pragma: no cover
        return _FakeStorageProxy()


class _FakeStorageProxy:
    def content(self, path: str) -> Any:  # pragma: no cover
        return _FakeDeleteProxy()


class _FakeDeleteProxy:
    def delete(self) -> None:  # pragma: no cover
        pass


class FakeProxmoxLxc(FakeProxmox):
    """Variant that returns the LXC template for the LXC path."""

    @property
    def cluster(self) -> "_FakeClusterProxyLxc":  # type: ignore[override]
        return _FakeClusterProxyLxc(self._vmid_counter)


class _FakeClusterProxyLxc(_FakeClusterProxy):
    @property
    def resources(self) -> "_FakeResourcesProxyLxc":
        return _FakeResourcesProxyLxc()

    @property
    def nextid(self) -> _FakeNextidProxy:
        return _FakeNextidProxy(self._counter)


class _FakeResourcesProxyLxc:
    def get(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "vmid": _TEMPLATE_VMID,
                "name": "ubuntu-lxc",
                "node": _NODE,
                "type": "lxc",
                "template": 1,
                "status": "stopped",
            }
        ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_config(tmp_path: Path, toml: str) -> str:
    p = tmp_path / "provider.toml"
    p.write_text(toml)
    return str(p)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.local
def test_create_instance_qemu_executes_userdata_via_qga(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """CreateInstance for a QEMU image should call agent.exec.post via QGA."""
    config_path = _write_config(tmp_path, _TOML_QEMU)

    fake = FakeProxmox()
    monkeypatch.setattr(
        "garm_proxmox_provider.client.ProxmoxAPI",
        lambda *a, **kw: fake,
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--config", config_path, "create-instance"],
        input=json.dumps(_BOOTSTRAP_QEMU),
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["provider_id"] == str(_NEW_VMID)
    assert payload["name"] == "runner-e2e"
    assert payload["status"] == "running"

    # QGA exec must have been invoked
    assert len(fake._agent.exec_calls) >= 1, "Expected agent.exec.post to be called"
    first_call = fake._agent.exec_calls[0]
    assert "command" in first_call


@pytest.mark.local
def test_create_instance_lxc_executes_userdata_via_exec(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """CreateInstance for an LXC image should call lxc.exec.post."""
    config_path = _write_config(tmp_path, _TOML_LXC)

    fake = FakeProxmoxLxc()
    monkeypatch.setattr(
        "garm_proxmox_provider.client.ProxmoxAPI",
        lambda *a, **kw: fake,
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--config", config_path, "create-instance"],
        input=json.dumps(_BOOTSTRAP_LXC),
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["provider_id"] == str(_NEW_VMID)
    assert payload["status"] == "running"

    # LXC exec must have been invoked
    assert len(fake._lxc_exec) >= 1, "Expected lxc.exec.post to be called"
    assert "command" in fake._lxc_exec[0]


@pytest.mark.local
def test_create_instance_returns_valid_instance_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Instance JSON must contain all required fields."""
    config_path = _write_config(tmp_path, _TOML_QEMU)

    fake = FakeProxmox()
    monkeypatch.setattr(
        "garm_proxmox_provider.client.ProxmoxAPI",
        lambda *a, **kw: fake,
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--config", config_path, "create-instance"],
        input=json.dumps(_BOOTSTRAP_QEMU),
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    for field in ("provider_id", "name", "os_type", "os_arch", "status", "pool_id"):
        assert field in payload, f"Missing field: {field}"


@pytest.mark.local
def test_qga_fallback_to_ssh_on_agent_not_ready(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When QGA never responds and qm_ssh_fallback=true, the SSH helper is invoked."""
    import subprocess

    config_path = _write_config(tmp_path, _TOML_QM_SSH)

    # Make QGA ping always fail so the timeout fires
    class _NeverReadyAgent(_FakeAgentProxy):
        @property
        def ping(self) -> "_FailingPingProxy":
            return _FailingPingProxy(self)

    class _FailingPingProxy(_FakePingProxy):
        def post(self) -> None:
            raise RuntimeError("agent not ready")

    fake = FakeProxmox()
    fake._agent = _NeverReadyAgent()
    monkeypatch.setattr(
        "garm_proxmox_provider.client.ProxmoxAPI",
        lambda *a, **kw: fake,
    )

    # Capture subprocess.run calls to verify SSH fallback fires
    ssh_calls: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:  # type: ignore[type-arg]
        ssh_calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("garm_proxmox_provider.client.subprocess.run", _fake_run)
    # Patch sleep to speed up the test
    monkeypatch.setattr("garm_proxmox_provider.client.time.sleep", lambda _: None)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--config", config_path, "create-instance"],
        input=json.dumps(_BOOTSTRAP_QEMU),
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    # SSH fallback should have been called
    assert len(ssh_calls) >= 1, "Expected SSH subprocess call for qm fallback"
    first = ssh_calls[0]
    assert "ssh" in first[0]
    assert "qm" in first


@pytest.mark.local
def test_list_instances_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ListInstances returns an empty list when no GARM-tagged VMs exist."""
    config_path = _write_config(tmp_path, _TOML_QEMU)

    fake = FakeProxmox()
    monkeypatch.setattr(
        "garm_proxmox_provider.client.ProxmoxAPI",
        lambda *a, **kw: fake,
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--config", config_path, "list-instances", "--pool-id", "pool-e2e"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert isinstance(payload, list)
    # The template VM has no GARM description, so the list should be empty
    assert payload == []
