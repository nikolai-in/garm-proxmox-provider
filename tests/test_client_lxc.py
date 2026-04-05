"""Tests for LXC container support in PVEClient."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

from garm_proxmox_provider.client import PVEClient, _parse_garm_meta
from garm_proxmox_provider.config import DefaultsConfig, PVEConfig, Config
from garm_proxmox_provider.models import Address, Instance, InstanceStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GARM_META = json.dumps(
    {
        "__garm__": True,
        "garm_controller_id": "ctrl-1",
        "garm_pool_id": "pool-1",
        "garm_instance_name": "runner-lxc-1",
        "garm_os_type": "linux",
        "garm_os_arch": "amd64",
    },
    separators=(",", ":"),
)


def _make_config(instance_type: str = "lxc", **kwargs: Any) -> Config:
    pve = PVEConfig(
        host="pve.example.com",
        user="root@pam",
        token_name="garm",
        token_value="secret",
        verify_ssl=False,
    )
    base: dict[str, Any] = dict(
        node="pve1",
        storage="local-lvm",
        template_vmid=9100,
        cores=2,
        memory_mb=2048,
        instance_type=instance_type,
        lxc_unprivileged=True,
    )
    base.update(kwargs)
    defaults = DefaultsConfig(**base)
    return Config(pve=pve, defaults=defaults)


def _make_client(instance_type: str = "lxc", **cfg_kwargs: Any) -> tuple[PVEClient, MagicMock]:
    """Return (client, mock_prox) with a patched proxmoxer.ProxmoxAPI."""
    cfg = _make_config(instance_type=instance_type, **cfg_kwargs)
    with patch("garm_proxmox_provider.client.ProxmoxAPI") as MockAPI:
        mock_prox = MagicMock()
        MockAPI.return_value = mock_prox
        client = PVEClient(cfg)
    client._prox = mock_prox
    return client, mock_prox


# ---------------------------------------------------------------------------
# list_instances — mixed VM + LXC resources
# ---------------------------------------------------------------------------


def test_list_instances_returns_lxc_containers() -> None:
    client, mock_prox = _make_client()

    # cluster/resources returns one LXC container
    mock_prox.cluster.resources.get.return_value = [
        {"vmid": 200, "node": "pve1", "type": "lxc", "status": "running"},
    ]
    mock_prox.nodes.return_value.lxc.return_value.config.get.return_value = {
        "description": _GARM_META,
        "hostname": "runner-lxc-1",
    }

    instances = client.list_instances("pool-1")

    assert len(instances) == 1
    assert instances[0].provider_id == "200"
    assert instances[0].name == "runner-lxc-1"
    assert instances[0].status == InstanceStatus.RUNNING
    # Verify that lxc.config was queried, not qemu.config
    mock_prox.nodes.return_value.lxc.return_value.config.get.assert_called_once()


def test_list_instances_mixed_vm_and_lxc() -> None:
    client, mock_prox = _make_client(instance_type="vm")

    mock_prox.cluster.resources.get.return_value = [
        {"vmid": 100, "node": "pve1", "type": "qemu", "status": "stopped"},
        {"vmid": 200, "node": "pve1", "type": "lxc", "status": "running"},
    ]

    def _lxc_config_get():
        m = MagicMock()
        m.get.return_value = {"description": _GARM_META, "hostname": "runner-lxc-1"}
        return m

    def _qemu_config_get():
        m = MagicMock()
        m.get.return_value = {"description": _GARM_META, "name": "runner-vm-1"}
        return m

    mock_prox.nodes.return_value.lxc.return_value.config = _lxc_config_get()
    mock_prox.nodes.return_value.qemu.return_value.config = _qemu_config_get()

    instances = client.list_instances("pool-1")
    assert len(instances) == 2


def test_list_instances_skips_non_garm_lxc() -> None:
    client, mock_prox = _make_client()

    mock_prox.cluster.resources.get.return_value = [
        {"vmid": 300, "node": "pve1", "type": "lxc", "status": "running"},
    ]
    mock_prox.nodes.return_value.lxc.return_value.config.get.return_value = {
        "description": "just a regular container",
    }

    instances = client.list_instances("pool-1")
    assert instances == []


# ---------------------------------------------------------------------------
# get_instance — LXC path
# ---------------------------------------------------------------------------


def test_get_instance_lxc_uses_lxc_api() -> None:
    client, mock_prox = _make_client()

    mock_prox.cluster.resources.get.return_value = [
        {"vmid": 200, "node": "pve1", "type": "lxc", "status": "running"},
    ]
    mock_prox.nodes.return_value.lxc.return_value.config.get.return_value = {
        "description": _GARM_META,
        "hostname": "runner-lxc-1",
    }
    mock_prox.nodes.return_value.lxc.return_value.interfaces.get.return_value = [
        {"name": "eth0", "inet": "10.0.0.5/24"},
        {"name": "lo", "inet": "127.0.0.1/8"},
    ]

    inst = client.get_instance("200")

    assert inst.provider_id == "200"
    assert inst.status == InstanceStatus.RUNNING
    assert len(inst.addresses) == 1
    assert inst.addresses[0].address == "10.0.0.5"
    assert inst.addresses[0].type == "ipv4"
    # lxc.interfaces used, not qemu.agent
    mock_prox.nodes.return_value.lxc.return_value.interfaces.get.assert_called_once()


def test_get_instance_lxc_ipv6() -> None:
    client, mock_prox = _make_client()

    mock_prox.cluster.resources.get.return_value = [
        {"vmid": 201, "node": "pve1", "type": "lxc", "status": "running"},
    ]
    mock_prox.nodes.return_value.lxc.return_value.config.get.return_value = {
        "description": _GARM_META,
    }
    mock_prox.nodes.return_value.lxc.return_value.interfaces.get.return_value = [
        {"name": "eth0", "inet": "10.0.0.6/24", "inet6": "2001:db8::1/64"},
    ]

    inst = client.get_instance("201")
    types = {a.type for a in inst.addresses}
    assert "ipv4" in types
    assert "ipv6" in types


def test_get_instance_lxc_not_found() -> None:
    client, mock_prox = _make_client()
    mock_prox.cluster.resources.get.return_value = []
    with pytest.raises(RuntimeError, match="not found"):
        client.get_instance("999")


# ---------------------------------------------------------------------------
# create_instance — LXC path
# ---------------------------------------------------------------------------


def test_create_instance_lxc_clones_lxc_template() -> None:
    client, mock_prox = _make_client()

    mock_prox.cluster.nextid.get.return_value = 201
    mock_prox.nodes.return_value.lxc.return_value.clone.post.return_value = "UPID:pve1:ok"
    mock_prox.nodes.return_value.tasks.return_value.status.get.return_value = {
        "status": "stopped",
        "exitstatus": "OK",
    }
    mock_prox.nodes.return_value.lxc.return_value.config.put.return_value = None
    mock_prox.nodes.return_value.lxc.return_value.status.start.post.return_value = "UPID:pve1:ok"

    inst = client.create_instance(
        name="runner-lxc-1",
        controller_id="ctrl-1",
        pool_id="pool-1",
        lxc_env_vars={
            "GARM_METADATA_URL": "https://garm.example.com/api/v1/metadata",
            "GARM_INSTANCE_TOKEN": "tok-abc",
            "GARM_REPO_URL": "https://github.com/org/repo",
            "GARM_LABELS": "self-hosted",
            "GARM_NAME": "runner-lxc-1",
            "GARM_CALLBACK_URL": "https://garm.example.com/api/v1/callback",
            "GARM_PROVIDER_ID": "PLACEHOLDER",
            "GARM_FORGE_TYPE": "github",
        },
    )

    assert inst.provider_id == "201"
    assert inst.status == InstanceStatus.RUNNING

    # clone used lxc endpoint
    mock_prox.nodes.return_value.lxc.return_value.clone.post.assert_called_once()
    clone_kwargs = mock_prox.nodes.return_value.lxc.return_value.clone.post.call_args.kwargs
    assert clone_kwargs["newid"] == 201
    assert clone_kwargs["hostname"] == "runner-lxc-1"
    assert clone_kwargs["full"] == 1

    # config was updated via PUT (not POST)
    mock_prox.nodes.return_value.lxc.return_value.config.put.assert_called_once()

    # QEMU endpoints were never touched
    mock_prox.nodes.return_value.qemu.assert_not_called()


def test_create_instance_lxc_env_vars_injected() -> None:
    client, mock_prox = _make_client()

    mock_prox.cluster.nextid.get.return_value = 202
    mock_prox.nodes.return_value.lxc.return_value.clone.post.return_value = "UPID:pve1:ok"
    mock_prox.nodes.return_value.tasks.return_value.status.get.return_value = {
        "status": "stopped",
        "exitstatus": "OK",
    }
    mock_prox.nodes.return_value.lxc.return_value.config.put.return_value = None
    mock_prox.nodes.return_value.lxc.return_value.status.start.post.return_value = "UPID:pve1:ok"

    env_vars = {
        "GARM_METADATA_URL": "https://garm.example.com/api/v1/metadata",
        "GARM_INSTANCE_TOKEN": "tok-xyz",
        "GARM_REPO_URL": "https://github.com/org/repo",
        "GARM_LABELS": "self-hosted",
        "GARM_NAME": "runner-env-test",
        "GARM_CALLBACK_URL": "https://garm.example.com/api/v1/callback",
        "GARM_PROVIDER_ID": "PLACEHOLDER",
        "GARM_FORGE_TYPE": "github",
    }
    client.create_instance(
        name="runner-env-test",
        controller_id="ctrl-1",
        pool_id="pool-1",
        lxc_env_vars=env_vars,
    )

    put_kwargs = mock_prox.nodes.return_value.lxc.return_value.config.put.call_args.kwargs

    # Should have lxc[N] keys for environment injection
    lxc_keys = [k for k in put_kwargs if k.startswith("lxc[")]
    assert len(lxc_keys) == len(env_vars)

    # GARM_PROVIDER_ID should be replaced with the real VMID
    env_values = list(put_kwargs.values())
    provider_id_line = next(
        v for v in env_values if isinstance(v, str) and "GARM_PROVIDER_ID" in v
    )
    assert "202" in provider_id_line
    assert "PLACEHOLDER" not in provider_id_line

    # unprivileged should be set
    assert put_kwargs.get("unprivileged") == 1


def test_create_instance_lxc_privileged_container() -> None:
    client, mock_prox = _make_client(lxc_unprivileged=False)

    mock_prox.cluster.nextid.get.return_value = 203
    mock_prox.nodes.return_value.lxc.return_value.clone.post.return_value = "UPID:pve1:ok"
    mock_prox.nodes.return_value.tasks.return_value.status.get.return_value = {
        "status": "stopped",
        "exitstatus": "OK",
    }
    mock_prox.nodes.return_value.lxc.return_value.config.put.return_value = None
    mock_prox.nodes.return_value.lxc.return_value.status.start.post.return_value = "UPID:pve1:ok"

    client.create_instance(
        name="runner-priv",
        controller_id="ctrl-1",
        pool_id="pool-1",
        lxc_env_vars={"GARM_PROVIDER_ID": "PLACEHOLDER"},
    )

    put_kwargs = mock_prox.nodes.return_value.lxc.return_value.config.put.call_args.kwargs
    assert put_kwargs.get("unprivileged") == 0


# ---------------------------------------------------------------------------
# delete_instance — LXC path
# ---------------------------------------------------------------------------


def test_delete_lxc_running_container_stops_then_deletes() -> None:
    client, mock_prox = _make_client()

    mock_prox.cluster.resources.get.return_value = [
        {"vmid": 200, "node": "pve1", "type": "lxc", "status": "running"},
    ]
    stop_upid = "UPID:pve1:stop"
    delete_upid = "UPID:pve1:del"
    mock_prox.nodes.return_value.lxc.return_value.status.stop.post.return_value = stop_upid
    mock_prox.nodes.return_value.lxc.return_value.delete.return_value = delete_upid
    mock_prox.nodes.return_value.tasks.return_value.status.get.return_value = {
        "status": "stopped",
        "exitstatus": "OK",
    }

    client.delete_instance("200")

    mock_prox.nodes.return_value.lxc.return_value.status.stop.post.assert_called_once()
    mock_prox.nodes.return_value.lxc.return_value.delete.assert_called_once()
    # QEMU endpoints must NOT be called
    mock_prox.nodes.return_value.qemu.assert_not_called()


def test_delete_lxc_stopped_container_skips_stop() -> None:
    client, mock_prox = _make_client()

    mock_prox.cluster.resources.get.return_value = [
        {"vmid": 201, "node": "pve1", "type": "lxc", "status": "stopped"},
    ]
    mock_prox.nodes.return_value.lxc.return_value.delete.return_value = "UPID:pve1:del"
    mock_prox.nodes.return_value.tasks.return_value.status.get.return_value = {
        "status": "stopped",
        "exitstatus": "OK",
    }

    client.delete_instance("201")

    mock_prox.nodes.return_value.lxc.return_value.status.stop.post.assert_not_called()
    mock_prox.nodes.return_value.lxc.return_value.delete.assert_called_once()


def test_delete_lxc_not_found_is_noop() -> None:
    client, mock_prox = _make_client()
    mock_prox.cluster.resources.get.return_value = []
    client.delete_instance("999")  # should not raise
    mock_prox.nodes.return_value.lxc.return_value.delete.assert_not_called()


# ---------------------------------------------------------------------------
# start_instance / stop_instance — LXC path
# ---------------------------------------------------------------------------


def test_start_lxc_instance() -> None:
    client, mock_prox = _make_client()

    mock_prox.cluster.resources.get.return_value = [
        {"vmid": 200, "node": "pve1", "type": "lxc", "status": "stopped"},
    ]
    mock_prox.nodes.return_value.lxc.return_value.status.start.post.return_value = "UPID:pve1:ok"
    mock_prox.nodes.return_value.tasks.return_value.status.get.return_value = {
        "status": "stopped",
        "exitstatus": "OK",
    }
    mock_prox.nodes.return_value.lxc.return_value.config.get.return_value = {
        "description": _GARM_META,
    }
    mock_prox.nodes.return_value.lxc.return_value.interfaces.get.return_value = []

    inst = client.start_instance("200")

    mock_prox.nodes.return_value.lxc.return_value.status.start.post.assert_called_once()
    mock_prox.nodes.return_value.qemu.assert_not_called()


def test_stop_lxc_instance() -> None:
    client, mock_prox = _make_client()

    mock_prox.cluster.resources.get.return_value = [
        {"vmid": 200, "node": "pve1", "type": "lxc", "status": "running"},
    ]
    mock_prox.nodes.return_value.lxc.return_value.status.shutdown.post.return_value = "UPID:pve1:ok"
    mock_prox.nodes.return_value.tasks.return_value.status.get.return_value = {
        "status": "stopped",
        "exitstatus": "OK",
    }
    mock_prox.nodes.return_value.lxc.return_value.config.get.return_value = {
        "description": _GARM_META,
    }
    mock_prox.nodes.return_value.lxc.return_value.interfaces.get.return_value = []

    inst = client.stop_instance("200")

    mock_prox.nodes.return_value.lxc.return_value.status.shutdown.post.assert_called_once()
    mock_prox.nodes.return_value.qemu.assert_not_called()


def test_start_lxc_not_found_raises() -> None:
    client, mock_prox = _make_client()
    mock_prox.cluster.resources.get.return_value = []
    with pytest.raises(RuntimeError, match="not found"):
        client.start_instance("999")


# ---------------------------------------------------------------------------
# remove_all_instances — includes LXC containers
# ---------------------------------------------------------------------------


def test_remove_all_instances_deletes_lxc_containers() -> None:
    client, mock_prox = _make_client()

    mock_prox.cluster.resources.get.return_value = [
        {"vmid": 200, "node": "pve1", "type": "lxc", "status": "stopped"},
    ]
    mock_prox.nodes.return_value.lxc.return_value.config.get.return_value = {
        "description": _GARM_META,
    }
    mock_prox.nodes.return_value.lxc.return_value.delete.return_value = "UPID:pve1:del"
    mock_prox.nodes.return_value.tasks.return_value.status.get.return_value = {
        "status": "stopped",
        "exitstatus": "OK",
    }

    client.remove_all_instances("ctrl-1")

    mock_prox.nodes.return_value.lxc.return_value.delete.assert_called_once()
    mock_prox.nodes.return_value.qemu.assert_not_called()


def test_remove_all_instances_skips_wrong_controller() -> None:
    client, mock_prox = _make_client()

    meta_other = json.dumps(
        {
            "__garm__": True,
            "garm_controller_id": "ctrl-OTHER",
            "garm_pool_id": "pool-1",
            "garm_instance_name": "runner-other",
            "garm_os_type": "linux",
            "garm_os_arch": "amd64",
        },
        separators=(",", ":"),
    )
    mock_prox.cluster.resources.get.return_value = [
        {"vmid": 300, "node": "pve1", "type": "lxc", "status": "stopped"},
    ]
    mock_prox.nodes.return_value.lxc.return_value.config.get.return_value = {
        "description": meta_other,
    }

    client.remove_all_instances("ctrl-1")

    mock_prox.nodes.return_value.lxc.return_value.delete.assert_not_called()


# ---------------------------------------------------------------------------
# _lxc_get_ips — edge cases
# ---------------------------------------------------------------------------


def test_lxc_get_ips_skips_loopback() -> None:
    client, mock_prox = _make_client()
    mock_prox.nodes.return_value.lxc.return_value.interfaces.get.return_value = [
        {"name": "lo", "inet": "127.0.0.1/8", "inet6": "::1/128"},
        {"name": "eth0", "inet": "192.168.1.10/24"},
    ]
    ips = client._lxc_get_ips("pve1", 200)
    assert len(ips) == 1
    assert ips[0].address == "192.168.1.10"


def test_lxc_get_ips_skips_link_local() -> None:
    client, mock_prox = _make_client()
    mock_prox.nodes.return_value.lxc.return_value.interfaces.get.return_value = [
        {"name": "eth0", "inet": "169.254.1.1/16"},
    ]
    ips = client._lxc_get_ips("pve1", 200)
    assert ips == []


def test_lxc_get_ips_returns_empty_on_exception() -> None:
    client, mock_prox = _make_client()
    mock_prox.nodes.return_value.lxc.return_value.interfaces.get.side_effect = RuntimeError("oops")
    ips = client._lxc_get_ips("pve1", 200)
    assert ips == []
