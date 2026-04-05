"""Cloud-init / cloudbase-init user-data renderer for GARM runner bootstrap.

Templates assume the runner binary is already present on the VM image (installed
by the Packer build).  The scripts only handle registration, service start, and
the GARM status callback.

Linux (cloud-init):   renders a ``#cloud-config`` YAML with a ``runcmd`` block.
Windows (cloudbase-init): renders a ``#ps1_sysnative`` PowerShell script.

Forge detection:
  - Explicit:  ``extra_specs.forge_type = "gitea"`` (or ``"forgejo"``)
  - Implicit:  ``repo_url`` does not contain ``github.com``
"""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import DefaultsConfig
    from .models import BootstrapInstance

# ---------------------------------------------------------------------------
# Forge detection
# ---------------------------------------------------------------------------


def _is_gitea(bootstrap: BootstrapInstance) -> bool:
    """Return True if the bootstrap targets a Gitea/Forgejo instance."""
    forge_type = bootstrap.extra_specs.get("forge_type", "")
    if forge_type:
        return forge_type.lower() in ("gitea", "forgejo")
    return "github.com" not in bootstrap.repo_url


# ---------------------------------------------------------------------------
# Linux scripts — runner binary pre-installed by Packer image
# ---------------------------------------------------------------------------

_LINUX_GITHUB_SCRIPT = """\
#!/bin/bash
set -euo pipefail

export HOME=/home/runner
RUNNER_HOME=/home/runner/actions-runner

# Fetch runner registration token from GARM metadata
RUNNER_TOKEN=$(curl -fsSL \\
    -H "Authorization: Bearer {instance_token}" \\
    "{metadata_url}/runner-registration-token" | tr -d '"')

cd "$RUNNER_HOME"

# Configure runner (binary pre-installed by Packer template)
su -s /bin/bash runner -c \\
    "./config.sh \\
        --url '{repo_url}' \\
        --token '${{RUNNER_TOKEN}}' \\
        --name '{name}' \\
        --labels '{labels}' \\
        --unattended \\
        --replace \\
        --ephemeral"

# Start the pre-installed systemd service
./svc.sh start

# Notify GARM that the instance is running
curl -fsSL -X POST \\
    -H "Authorization: Bearer {instance_token}" \\
    -H "Content-Type: application/json" \\
    "{callback_url}" \\
    -d '{{"provider_id":"{provider_id}","name":"{name}","status":"running"}}'
"""

_LINUX_GITEA_SCRIPT = """\
#!/bin/bash
set -euo pipefail

export HOME=/home/runner
RUNNER_HOME=/home/runner/act_runner

# Fetch runner registration token from GARM metadata
RUNNER_TOKEN=$(curl -fsSL \\
    -H "Authorization: Bearer {instance_token}" \\
    "{metadata_url}/runner-registration-token" | tr -d '"')

cd "$RUNNER_HOME"

# Register act_runner (binary pre-installed by Packer template)
su -s /bin/bash runner -c \\
    "./act_runner register \\
        --instance '{repo_url}' \\
        --token '${{RUNNER_TOKEN}}' \\
        --name '{name}' \\
        --labels '{labels}' \\
        --no-interactive"

# Start the pre-installed systemd service
systemctl start act_runner

# Notify GARM that the instance is running
curl -fsSL -X POST \\
    -H "Authorization: Bearer {instance_token}" \\
    -H "Content-Type: application/json" \\
    "{callback_url}" \\
    -d '{{"provider_id":"{provider_id}","name":"{name}","status":"running"}}'
"""


def _render_linux_userdata(
    bootstrap: BootstrapInstance,
    provider_id: str,
    defaults: DefaultsConfig,
) -> str:
    """Render a ``#cloud-config`` YAML document for Linux."""
    labels = ",".join(bootstrap.labels) if bootstrap.labels else bootstrap.pool_id
    template = _LINUX_GITEA_SCRIPT if _is_gitea(bootstrap) else _LINUX_GITHUB_SCRIPT
    script = template.format(
        instance_token=bootstrap.instance_token,
        metadata_url=bootstrap.metadata_url.rstrip("/"),
        repo_url=bootstrap.repo_url,
        name=bootstrap.name,
        labels=labels,
        callback_url=bootstrap.callback_url,
        provider_id=provider_id,
    )

    ssh_keys: list[str] = []
    if defaults.ssh_public_key:
        ssh_keys.append(defaults.ssh_public_key.strip())

    ssh_block = ""
    if ssh_keys:
        keys_yaml = "\n".join(f"      - {k!r}" for k in ssh_keys)
        ssh_block = f"    ssh_authorized_keys:\n{keys_yaml}\n"

    script_indented = textwrap.indent(script.rstrip(), "      ")

    return f"""\
#cloud-config
users:
  - name: runner
    gecos: GARM runner
    shell: /bin/bash
    groups: [sudo]
    sudo: "ALL=(ALL) NOPASSWD:ALL"
{ssh_block}
package_update: false

runcmd:
  - |
{script_indented}
"""


# ---------------------------------------------------------------------------
# Windows scripts — runner binary pre-installed by Packer image
# cloudbase-init processes #ps1_sysnative as a 64-bit PowerShell script.
# ---------------------------------------------------------------------------

_WINDOWS_GITHUB_SCRIPT = """\
#ps1_sysnative
$ErrorActionPreference = 'Stop'

$RunnerHome = 'C:\\actions-runner'
$MetadataUrl = '{metadata_url}'
$InstanceToken = '{instance_token}'
$RepoUrl = '{repo_url}'
$RunnerName = '{name}'
$RunnerLabels = '{labels}'
$CallbackUrl = '{callback_url}'
$ProviderId = '{provider_id}'

# Fetch registration token from GARM metadata
$RunnerToken = (Invoke-RestMethod -Uri "$MetadataUrl/runner-registration-token" `
    -Headers @{{ Authorization = "Bearer $InstanceToken" }}).Trim('"')

Set-Location $RunnerHome

# Configure runner (binary pre-installed by Packer template)
& .\\config.cmd --url $RepoUrl `
    --token $RunnerToken `
    --name $RunnerName `
    --labels $RunnerLabels `
    --unattended --replace --ephemeral

# Start the pre-installed service
& .\\svc.cmd start

# Notify GARM that the instance is running
Invoke-RestMethod -Uri $CallbackUrl -Method Post `
    -Headers @{{ Authorization = "Bearer $InstanceToken"; 'Content-Type' = 'application/json' }} `
    -Body "{{`"provider_id`":`"$ProviderId`",`"name`":`"$RunnerName`",`"status`":`"running`"}}"
"""

_WINDOWS_GITEA_SCRIPT = """\
#ps1_sysnative
$ErrorActionPreference = 'Stop'

$RunnerHome = 'C:\\act_runner'
$MetadataUrl = '{metadata_url}'
$InstanceToken = '{instance_token}'
$RepoUrl = '{repo_url}'
$RunnerName = '{name}'
$RunnerLabels = '{labels}'
$CallbackUrl = '{callback_url}'
$ProviderId = '{provider_id}'

# Fetch registration token from GARM metadata
$RunnerToken = (Invoke-RestMethod -Uri "$MetadataUrl/runner-registration-token" `
    -Headers @{{ Authorization = "Bearer $InstanceToken" }}).Trim('"')

Set-Location $RunnerHome

# Register act_runner (binary pre-installed by Packer template)
& .\\act_runner.exe register `
    --instance $RepoUrl `
    --token $RunnerToken `
    --name $RunnerName `
    --labels $RunnerLabels `
    --no-interactive

# Start the pre-installed service
Start-Service act_runner

# Notify GARM that the instance is running
Invoke-RestMethod -Uri $CallbackUrl -Method Post `
    -Headers @{{ Authorization = "Bearer $InstanceToken"; 'Content-Type' = 'application/json' }} `
    -Body "{{`"provider_id`":`"$ProviderId`",`"name`":`"$RunnerName`",`"status`":`"running`"}}"
"""


def _render_windows_userdata(
    bootstrap: BootstrapInstance,
    provider_id: str,
) -> str:
    """Render a cloudbase-init PowerShell script for Windows."""
    labels = ",".join(bootstrap.labels) if bootstrap.labels else bootstrap.pool_id
    template = _WINDOWS_GITEA_SCRIPT if _is_gitea(bootstrap) else _WINDOWS_GITHUB_SCRIPT
    return template.format(
        instance_token=bootstrap.instance_token,
        metadata_url=bootstrap.metadata_url.rstrip("/"),
        repo_url=bootstrap.repo_url,
        name=bootstrap.name,
        labels=labels,
        callback_url=bootstrap.callback_url,
        provider_id=provider_id,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_lxc_env_vars(bootstrap: BootstrapInstance, provider_id: str) -> dict[str, str]:
    """Return a dict of environment variables to inject into an LXC container.

    The LXC template image must have a startup script (e.g. via systemd or
    ``/etc/rc.local``) that reads these variables and performs runner
    registration.  ``GARM_FORGE_TYPE`` is either ``"github"`` or ``"gitea"``
    so the startup script can branch accordingly.
    """
    labels = ",".join(bootstrap.labels) if bootstrap.labels else bootstrap.pool_id
    forge_type = "gitea" if _is_gitea(bootstrap) else "github"
    return {
        "GARM_METADATA_URL": bootstrap.metadata_url.rstrip("/"),
        "GARM_INSTANCE_TOKEN": bootstrap.instance_token,
        "GARM_REPO_URL": bootstrap.repo_url,
        "GARM_LABELS": labels,
        "GARM_NAME": bootstrap.name,
        "GARM_CALLBACK_URL": bootstrap.callback_url,
        "GARM_PROVIDER_ID": provider_id,
        "GARM_FORGE_TYPE": forge_type,
    }


def render_userdata(
    bootstrap: BootstrapInstance,
    provider_id: str,
    defaults: DefaultsConfig,
) -> str:
    """Return the appropriate user-data document for the bootstrap's OS type."""
    if bootstrap.os_type == "windows":
        return _render_windows_userdata(bootstrap, provider_id)
    return _render_linux_userdata(bootstrap, provider_id, defaults)
