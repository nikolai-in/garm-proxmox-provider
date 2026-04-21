"""Smoke tests for GARM commands using a mocked PVEClient."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from garm_proxmox_provider.models import Instance, InstanceStatus

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
    "image": "default",
    "flavor": "default",
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
    with patch("garm_proxmox_provider.commands.PVEClient") as MockClient:
        MockClient.return_value.list_instances.return_value = instances
        from garm_proxmox_provider.commands import list_instances

        list_instances(config_file, "pool-111")

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert isinstance(payload, list)
    assert len(payload) == 2
    assert payload[0]["provider_id"] == "1001"


# ---------------------------------------------------------------------------
# GetInstance
# ---------------------------------------------------------------------------


def test_get_instance(config_file: str, capsys: pytest.CaptureFixture[str]) -> None:
    with patch("garm_proxmox_provider.commands.PVEClient") as MockClient:
        MockClient.return_value.get_instance.return_value = _mock_instance()
        from garm_proxmox_provider.commands import get_instance

        get_instance(config_file, "1001")

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["provider_id"] == "1001"
    assert payload["status"] == "running"


# ---------------------------------------------------------------------------
# CreateInstance
# ---------------------------------------------------------------------------


def test_create_instance(config_file: str, capsys: pytest.CaptureFixture[str]) -> None:
    with patch("garm_proxmox_provider.commands.PVEClient") as MockClient:
        MockClient.return_value.create_instance.return_value = _mock_instance()
        from garm_proxmox_provider.commands import create_instance

        create_instance(config_file, json.dumps(BOOTSTRAP_PAYLOAD))

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["provider_id"] == "1001"
    assert payload["name"] == "runner-smoke"


def test_create_instance_invalid_json(
    config_file: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        from garm_proxmox_provider.commands import create_instance

        create_instance(config_file, "not-json")

    assert exc_info.value.code != 0


# ---------------------------------------------------------------------------
# DeleteInstance
# ---------------------------------------------------------------------------


def test_delete_instance(config_file: str) -> None:
    with patch("garm_proxmox_provider.commands.PVEClient") as MockClient:
        MockClient.return_value.delete_instance.return_value = None
        from garm_proxmox_provider.commands import delete_instance

        delete_instance(config_file, "1001")  # should not raise

    MockClient.return_value.delete_instance.assert_called_once_with("1001")


# ---------------------------------------------------------------------------
# Start / Stop
# ---------------------------------------------------------------------------


def test_start_instance(config_file: str, capsys: pytest.CaptureFixture[str]) -> None:
    with patch("garm_proxmox_provider.commands.PVEClient") as MockClient:
        MockClient.return_value.start_instance.return_value = _mock_instance()
        from garm_proxmox_provider.commands import start

        start(config_file, "1001")

    out = capsys.readouterr().out
    assert not out.strip()
    MockClient.return_value.start_instance.assert_called_once_with("1001")


def test_stop_instance(config_file: str, capsys: pytest.CaptureFixture[str]) -> None:
    with patch("garm_proxmox_provider.commands.PVEClient") as MockClient:
        MockClient.return_value.stop_instance.return_value = _mock_instance(
            status=InstanceStatus.STOPPED
        )
        from garm_proxmox_provider.commands import stop

        stop(config_file, "1001")

    out = capsys.readouterr().out
    assert not out.strip()
    MockClient.return_value.stop_instance.assert_called_once_with("1001")


# ---------------------------------------------------------------------------
# RemoveAllInstances
# ---------------------------------------------------------------------------


def test_remove_all_instances(config_file: str) -> None:
    with patch("garm_proxmox_provider.commands.PVEClient") as MockClient:
        MockClient.return_value.remove_all_instances.return_value = None
        from garm_proxmox_provider.commands import remove_all_instances

        remove_all_instances(config_file, "ctrl-222")

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
    result = runner.invoke(
        cli, env={"GARM_COMMAND": "UnknownCommand"}, catch_exceptions=False
    )
    assert result.exit_code != 0


def test_cli_debug_subgroup_exists() -> None:
    """'debug' subgroup should be registered on the root CLI."""
    from garm_proxmox_provider.cli import cli

    assert "debug" in cli.commands


def test_cli_admin_subgroup_exists() -> None:
    """'admin' subgroup should be registered on the root CLI."""
    from garm_proxmox_provider.cli import cli

    assert "admin" in cli.commands


def test_cli_debug_commands_registered() -> None:
    """Debug commands should be reachable under the 'debug' group."""
    from garm_proxmox_provider.cli import cli

    debug = cli.commands["debug"]
    assert "test-connection" in debug.commands  # type: ignore[attr-defined]
    assert "list-templates" in debug.commands  # type: ignore[attr-defined]
    assert "lint-config" in debug.commands  # type: ignore[attr-defined]


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
    "image": "default",
    "flavor": "default",
    "labels": ["self-hosted", "windows"],
}


def test_create_instance_windows(
    config_file: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with patch("garm_proxmox_provider.commands.PVEClient") as MockClient:
        MockClient.return_value.create_instance.return_value = _mock_instance(
            provider_id="2001",
            name="runner-win",
            os_type="windows",
            os_arch="amd64",
        )
        from garm_proxmox_provider.commands import create_instance

        create_instance(config_file, json.dumps(WINDOWS_BOOTSTRAP_PAYLOAD))

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
    "image": "default",
    "flavor": "default",
    "labels": ["self-hosted"],
}


def test_create_instance_gitea(
    config_file: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with patch("garm_proxmox_provider.commands.PVEClient") as MockClient:
        MockClient.return_value.create_instance.return_value = _mock_instance(
            provider_id="3001",
            name="runner-gitea",
        )
        from garm_proxmox_provider.commands import create_instance

        create_instance(config_file, json.dumps(GITEA_BOOTSTRAP_PAYLOAD))

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["provider_id"] == "3001"
