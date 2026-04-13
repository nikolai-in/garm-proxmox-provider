"""Tests for data models."""

from __future__ import annotations

import json

import pytest

from garm_proxmox_provider.models import (
    BootstrapInstance,
    Instance,
    InstanceStatus,
    RunnerTool,
)

BOOTSTRAP_DICT = {
    "name": "runner-abcdef",
    "tools": [
        {
            "os": "linux",
            "arch": "amd64",
            "download_url": "https://example.com/runner-2.0.0-linux-x64.tar.gz",
            "filename": "runner-2.0.0-linux-x64.tar.gz",
            "sha256_checksum": "deadbeef",
        },
        {
            "os": "linux",
            "arch": "arm64",
            "download_url": "https://example.com/runner-2.0.0-linux-arm64.tar.gz",
            "filename": "runner-2.0.0-linux-arm64.tar.gz",
            "sha256_checksum": "cafebabe",
        },
    ],
    "repo_url": "https://github.com/myorg/myrepo",
    "metadata_url": "https://garm.example.com/api/v1/metadata",
    "callback_url": "https://garm.example.com/api/v1/instances/callback",
    "instance_token": "secret-token-123",
    "pool_id": "pool-uuid-1234",
    "controller_id": "ctrl-uuid-5678",
    "os_type": "linux",
    "os_arch": "amd64",
    "labels": ["self-hosted", "linux", "x64"],
}


def test_bootstrap_from_dict_basic() -> None:
    b = BootstrapInstance.from_dict(BOOTSTRAP_DICT)
    assert b.name == "runner-abcdef"
    assert len(b.tools) == 2
    assert b.pool_id == "pool-uuid-1234"
    assert b.controller_id == "ctrl-uuid-5678"
    assert b.labels == ["self-hosted", "linux", "x64"]


def test_bootstrap_get_tool_amd64() -> None:
    b = BootstrapInstance.from_dict(BOOTSTRAP_DICT)
    tool = b.get_tool()
    assert tool is not None
    assert tool.arch == "amd64"
    assert "x64" in tool.filename


def test_bootstrap_get_tool_arm64() -> None:
    data = {**BOOTSTRAP_DICT, "os_arch": "arm64"}
    b = BootstrapInstance.from_dict(data)
    tool = b.get_tool()
    assert tool is not None
    assert tool.arch == "arm64"


def test_bootstrap_get_tool_fallback() -> None:
    """When arch not matched, fall back to first tool."""
    data = {**BOOTSTRAP_DICT, "os_arch": "riscv64"}
    b = BootstrapInstance.from_dict(data)
    tool = b.get_tool()
    assert tool is not None  # falls back to first


def test_bootstrap_extra_specs_json_string() -> None:
    data = {**BOOTSTRAP_DICT, "extra_specs": '{"cores": 4, "memory_mb": 8192}'}
    b = BootstrapInstance.from_dict(data)
    assert b.extra_specs == {"cores": 4, "memory_mb": 8192}


def test_bootstrap_extra_specs_bad_json() -> None:
    data = {**BOOTSTRAP_DICT, "extra_specs": "not-json"}
    b = BootstrapInstance.from_dict(data)
    assert b.extra_specs == {}


def test_instance_to_json_round_trip() -> None:
    inst = Instance(
        provider_id="1001",
        name="runner-abcdef",
        status=InstanceStatus.RUNNING,
        pool_id="pool-uuid-1234",
    )
    payload = json.loads(inst.to_json())
    assert payload["provider_id"] == "1001"
    assert payload["name"] == "runner-abcdef"
    assert payload["status"] == "running"
    assert payload["pool_id"] == "pool-uuid-1234"
    assert payload["addresses"] == []


def test_instance_status_values() -> None:
    for status in InstanceStatus:
        inst = Instance(provider_id="1", name="x", status=status)
        d = inst.to_dict()
        assert isinstance(d["status"], str)
