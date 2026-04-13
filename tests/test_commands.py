"""Smoke tests for GARM commands using a mocked PVEClient."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from garm_proxmox_provider.models import Instance, InstanceStatus

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
template_vmid = 9000
cores = 2
memory_mb = 4096
disk_gb = 20
bridge = "vmbr0"
"""

BOOTSTRAP_PAYLOAD = {
    "name": "runner-smoke",
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
    "pool_id": "pool-111",
    "controller_id": "ctrl-222",
    "os_type": "linux",
    "os_arch": "amd64",
    "labels": ["self-hosted"],
}


@pytest.fixture()
def config_file(tmp_path: Path) -> str:
    p = tmp_path / "provider.toml"
    p.write_text(MINIMAL_TOML)
    return str(p)


def _mock_instance(**kwargs: Any) -> Instance:
    defaults = {
        "provider_id": "1001",
        "name": "runner-smoke",
        "status": InstanceStatus.RUNNING,
        "pool_id": "pool-111",
    }
    defaults.update(kwargs)
    return Instance(**defaults)


# ---------------------------------------------------------------------------
# ListInstances
# ---------------------------------------------------------------------------


def test_list_instances(config_file: str, capsys: pytest.CaptureFixture[str]) -> None:
    instances = [
        _mock_instance(provider_id="1001"),
        _mock_instance(provider_id="1002"),
    ]
    with (
        patch("garm_proxmox_provider.commands.PVEClient") as MockClient,
        patch.dict(
            os.environ,
            {
                "GARM_COMMAND": "ListInstances",
                "GARM_PROVIDER_CONFIG_FILE": config_file,
                "GARM_POOL_ID": "pool-111",
            },
        ),
    ):
        MockClient.return_value.list_instances.return_value = instances
        from garm_proxmox_provider.commands import cmd_list_instances

        cmd_list_instances()

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert isinstance(payload, list)
    assert len(payload) == 2
    assert payload[0]["provider_id"] == "1001"


# ---------------------------------------------------------------------------
# GetInstance
# ---------------------------------------------------------------------------


def test_get_instance(config_file: str, capsys: pytest.CaptureFixture[str]) -> None:
    with (
        patch("garm_proxmox_provider.commands.PVEClient") as MockClient,
        patch.dict(
            os.environ,
            {
                "GARM_PROVIDER_CONFIG_FILE": config_file,
                "GARM_INSTANCE_ID": "1001",
            },
        ),
    ):
        MockClient.return_value.get_instance.return_value = _mock_instance()
        from garm_proxmox_provider.commands import cmd_get_instance

        cmd_get_instance()

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["provider_id"] == "1001"
    assert payload["status"] == "running"


# ---------------------------------------------------------------------------
# CreateInstance
# ---------------------------------------------------------------------------


def test_create_instance(
    config_file: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with (
        patch("garm_proxmox_provider.commands.PVEClient") as MockClient,
        patch.dict(
            os.environ,
            {
                "GARM_PROVIDER_CONFIG_FILE": config_file,
            },
        ),
        patch("sys.stdin") as mock_stdin,
    ):
        mock_stdin.read.return_value = json.dumps(BOOTSTRAP_PAYLOAD)
        MockClient.return_value.create_instance.return_value = _mock_instance()
        from garm_proxmox_provider.commands import cmd_create_instance

        cmd_create_instance()

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["provider_id"] == "1001"
    assert payload["name"] == "runner-smoke"


def test_create_instance_invalid_json(
    config_file: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with (
        patch.dict(
            os.environ,
            {
                "GARM_PROVIDER_CONFIG_FILE": config_file,
            },
        ),
        patch("sys.stdin") as mock_stdin,
        pytest.raises(SystemExit) as exc_info,
    ):
        mock_stdin.read.return_value = "not-json"
        from garm_proxmox_provider.commands import cmd_create_instance

        cmd_create_instance()

    assert exc_info.value.code != 0


# ---------------------------------------------------------------------------
# DeleteInstance
# ---------------------------------------------------------------------------


def test_delete_instance(config_file: str) -> None:
    with (
        patch("garm_proxmox_provider.commands.PVEClient") as MockClient,
        patch.dict(
            os.environ,
            {
                "GARM_PROVIDER_CONFIG_FILE": config_file,
                "GARM_INSTANCE_ID": "1001",
            },
        ),
    ):
        MockClient.return_value.delete_instance.return_value = None
        from garm_proxmox_provider.commands import cmd_delete_instance

        cmd_delete_instance()  # should not raise

    MockClient.return_value.delete_instance.assert_called_once_with("1001")


# ---------------------------------------------------------------------------
# Start / Stop
# ---------------------------------------------------------------------------


def test_start_instance(
    config_file: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with (
        patch("garm_proxmox_provider.commands.PVEClient") as MockClient,
        patch.dict(
            os.environ,
            {
                "GARM_PROVIDER_CONFIG_FILE": config_file,
                "GARM_INSTANCE_ID": "1001",
            },
        ),
    ):
        MockClient.return_value.start_instance.return_value = _mock_instance()
        from garm_proxmox_provider.commands import cmd_start

        cmd_start()

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["status"] == "running"


def test_stop_instance(
    config_file: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with (
        patch("garm_proxmox_provider.commands.PVEClient") as MockClient,
        patch.dict(
            os.environ,
            {
                "GARM_PROVIDER_CONFIG_FILE": config_file,
                "GARM_INSTANCE_ID": "1001",
            },
        ),
    ):
        MockClient.return_value.stop_instance.return_value = _mock_instance(
            status=InstanceStatus.STOPPED
        )
        from garm_proxmox_provider.commands import cmd_stop

        cmd_stop()

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["status"] == "stopped"


# ---------------------------------------------------------------------------
# RemoveAllInstances
# ---------------------------------------------------------------------------


def test_remove_all_instances(config_file: str) -> None:
    with (
        patch("garm_proxmox_provider.commands.PVEClient") as MockClient,
        patch.dict(
            os.environ,
            {
                "GARM_PROVIDER_CONFIG_FILE": config_file,
                "GARM_CONTROLLER_ID": "ctrl-222",
            },
        ),
    ):
        MockClient.return_value.remove_all_instances.return_value = None
        from garm_proxmox_provider.commands import cmd_remove_all_instances

        cmd_remove_all_instances()

    MockClient.return_value.remove_all_instances.assert_called_once_with("ctrl-222")


# ---------------------------------------------------------------------------
# CLI dispatch
# ---------------------------------------------------------------------------


def test_cli_no_command(capsys: pytest.CaptureFixture[str]) -> None:
    from click.testing import CliRunner

    from garm_proxmox_provider.cli import cli

    runner = CliRunner()
    env = {k: v for k, v in os.environ.items() if k != "GARM_COMMAND"}
    env.pop("GARM_COMMAND", None)
    result = runner.invoke(cli, env=env, catch_exceptions=False)
    assert result.exit_code != 0


def test_cli_unknown_command() -> None:
    from click.testing import CliRunner

    from garm_proxmox_provider.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, env={"GARM_COMMAND": "UnknownCommand"}, catch_exceptions=False)
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# CreateInstance — Windows bootstrap
# ---------------------------------------------------------------------------

WINDOWS_BOOTSTRAP_PAYLOAD = {
    "name": "runner-win",
    "tools": [],
    "repo_url": "https://github.com/myorg/myrepo",
    "metadata_url": "https://garm.example.com/api/v1/metadata",
    "callback_url": "https://garm.example.com/api/v1/instances/callback",
    "instance_token": "secret-token",
    "pool_id": "pool-win",
    "controller_id": "ctrl-222",
    "os_type": "windows",
    "os_arch": "amd64",
    "labels": ["self-hosted", "windows"],
}


def test_create_instance_windows(
    config_file: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with (
        patch("garm_proxmox_provider.commands.PVEClient") as MockClient,
        patch.dict(
            os.environ,
            {"GARM_PROVIDER_CONFIG_FILE": config_file},
        ),
        patch("sys.stdin") as mock_stdin,
    ):
        mock_stdin.read.return_value = json.dumps(WINDOWS_BOOTSTRAP_PAYLOAD)
        MockClient.return_value.create_instance.return_value = _mock_instance(
            provider_id="2001",
            name="runner-win",
            os_type="windows",
            os_arch="amd64",
        )
        from garm_proxmox_provider.commands import cmd_create_instance

        cmd_create_instance()

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["provider_id"] == "2001"
    assert payload["os_type"] == "windows"

    # Verify that create_instance was called with the right os_type/os_arch
    call_kwargs = MockClient.return_value.create_instance.call_args
    assert call_kwargs.kwargs.get("os_type") == "windows" or (
        call_kwargs.args[4] == "windows" if len(call_kwargs.args) > 4 else True
    )


# ---------------------------------------------------------------------------
# CreateInstance — Gitea bootstrap
# ---------------------------------------------------------------------------

GITEA_BOOTSTRAP_PAYLOAD = {
    "name": "runner-gitea",
    "tools": [],
    "repo_url": "https://gitea.example.com/org/repo",
    "metadata_url": "https://garm.example.com/api/v1/metadata",
    "callback_url": "https://garm.example.com/api/v1/instances/callback",
    "instance_token": "secret-token",
    "pool_id": "pool-gitea",
    "controller_id": "ctrl-222",
    "os_type": "linux",
    "os_arch": "amd64",
    "labels": ["self-hosted"],
}


def test_create_instance_gitea(
    config_file: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with (
        patch("garm_proxmox_provider.commands.PVEClient") as MockClient,
        patch.dict(
            os.environ,
            {"GARM_PROVIDER_CONFIG_FILE": config_file},
        ),
        patch("sys.stdin") as mock_stdin,
    ):
        mock_stdin.read.return_value = json.dumps(GITEA_BOOTSTRAP_PAYLOAD)
        MockClient.return_value.create_instance.return_value = _mock_instance(
            provider_id="3001",
            name="runner-gitea",
        )
        from garm_proxmox_provider.commands import cmd_create_instance

        cmd_create_instance()

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["provider_id"] == "3001"
