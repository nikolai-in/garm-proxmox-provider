"""Data models for the GARM external provider contract."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class InstanceStatus(str, Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"
    PENDING_DELETE = "pending_delete"
    PENDING_CREATE = "pending_create"
    UNKNOWN = "unknown"


@dataclass
class Address:
    address: str
    type: str = "ipv4"

    def to_dict(self) -> dict[str, str]:
        return {"address": self.address, "type": self.type}


@dataclass
class Instance:
    provider_id: str
    name: str
    os_type: str = "linux"
    os_name: str = ""
    os_version: str = ""
    os_arch: str = "amd64"
    status: InstanceStatus = InstanceStatus.UNKNOWN
    pool_id: str = ""
    provider_fault: str = ""
    addresses: list[Address] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "name": self.name,
            "os_type": self.os_type,
            "os_name": self.os_name,
            "os_version": self.os_version,
            "os_arch": self.os_arch,
            "status": self.status.value
            if isinstance(self.status, InstanceStatus)
            else self.status,
            "pool_id": self.pool_id,
            "provider_fault": self.provider_fault,
            "addresses": [a.to_dict() for a in self.addresses],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


@dataclass
class RunnerTool:
    os: str
    arch: str
    download_url: str
    filename: str
    sha256_checksum: str = ""


@dataclass
class BootstrapInstance:
    name: str
    tools: list[RunnerTool]
    repo_url: str
    metadata_url: str
    callback_url: str
    instance_token: str
    pool_id: str
    controller_id: str
    os_type: str = "linux"
    os_arch: str = "amd64"
    flavor: str = ""
    image: str = ""
    labels: list[str] = field(default_factory=list)
    pool_params: dict[str, Any] = field(default_factory=dict)
    extra_specs: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BootstrapInstance:
        tools = [
            RunnerTool(
                os=t.get("os", "linux"),
                arch=t.get("arch", "amd64"),
                download_url=t.get("download_url", ""),
                filename=t.get("filename", ""),
                sha256_checksum=t.get("sha256_checksum", ""),
            )
            for t in data.get("tools", [])
        ]
        extra_specs_raw = data.get("extra_specs", {})
        if isinstance(extra_specs_raw, str):
            try:
                extra_specs_raw = json.loads(extra_specs_raw)
            except (json.JSONDecodeError, ValueError):
                extra_specs_raw = {}
        return cls(
            name=data["name"],
            tools=tools,
            repo_url=data.get("repo_url", ""),
            metadata_url=data.get("metadata_url", ""),
            callback_url=data.get("callback_url", ""),
            instance_token=data.get("instance_token", ""),
            pool_id=data.get("pool_id", ""),
            controller_id=data.get("controller_id", ""),
            os_type=data.get("os_type", "linux"),
            os_arch=data.get("os_arch", "amd64"),
            flavor=data.get("flavor", ""),
            image=data.get("image", ""),
            labels=data.get("labels", []),
            pool_params=data.get("pool_params", {}),
            extra_specs=extra_specs_raw,
        )

    def get_tool(self) -> RunnerTool | None:
        """Return the runner tool matching os_type/os_arch, or first available."""
        # Normalise arch names (GARM uses "amd64", runner tools may say "x64")
        arch_aliases: dict[str, list[str]] = {
            "amd64": ["amd64", "x64", "x86_64"],
            "arm64": ["arm64", "aarch64"],
            "arm": ["arm", "armv7l"],
        }
        accepted = arch_aliases.get(self.os_arch, [self.os_arch])
        for tool in self.tools:
            if tool.os == self.os_type and tool.arch in accepted:
                return tool
        return self.tools[0] if self.tools else None
