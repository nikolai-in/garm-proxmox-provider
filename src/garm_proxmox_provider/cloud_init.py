"""Cloud-init / cloudbase-init user-data renderer for GARM runner bootstrap.

Templates assume the runner binary is already present on the VM image
(installed by the Packer build), along with the startup script at:
  - Linux: /opt/garm/scripts/startup-linux.sh
  - Windows: C:\\garm\\scripts\\startup-windows.ps1

The scripts only handle registration, service start, and status callback.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import ClusterConfig
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


def _render_linux_userdata(
    bootstrap: BootstrapInstance,
    provider_id: str,
    defaults: ClusterConfig,
) -> str:
    """Render a bash script for Linux (works with cloud-init and LXC exec)."""
    labels = ",".join(bootstrap.labels) if bootstrap.labels else bootstrap.pool_id
    forge_type = "gitea" if _is_gitea(bootstrap) else "github"

    ssh_setup = ""
    if defaults.ssh_public_key:
        key = defaults.ssh_public_key.strip()
        ssh_setup = f"""\
mkdir -p /home/runner/.ssh
echo "{key}" >> /home/runner/.ssh/authorized_keys
chown -R runner:runner /home/runner/.ssh
chmod 700 /home/runner/.ssh
chmod 600 /home/runner/.ssh/authorized_keys
"""

    # If the bootstrap payload provides a base64-encoded installer template,
    # decode it into the expected startup script path so we can run it.
    installer_b64 = None
    try:
        installer_b64 = (
            bootstrap.extra_specs.get("runner_install_template")
            if bootstrap.extra_specs
            else None
        )
    except Exception:
        installer_b64 = None

    install_snippet = ""
    if installer_b64:
        # create scripts dir, write the base64 content into the startup script
        # and make it executable. Use a heredoc with a quoted delimiter to avoid
        # variable expansion in the payload.
        install_snippet = f"""mkdir -p /opt/garm/scripts
cat > /opt/garm/scripts/startup-linux.sh <<'__GARM_INSTALL__'
{installer_b64}
__GARM_INSTALL__
chmod +x /opt/garm/scripts/startup-linux.sh

"""

    return f"""#!/bin/bash
set -euo pipefail

{ssh_setup}{install_snippet}export METADATA_URL="{bootstrap.metadata_url.rstrip("/")}"
export CALLBACK_URL="{bootstrap.callback_url}"
export BEARER_TOKEN="{bootstrap.instance_token}"
export REPO_URL="{bootstrap.repo_url}"
export RUNNER_NAME="{bootstrap.name}"
export RUNNER_LABELS="{labels}"
export FORGE_TYPE="{forge_type}"
export PROVIDER_ID="{provider_id}"

bash /opt/garm/scripts/startup-linux.sh
"""


# ---------------------------------------------------------------------------
# Windows scripts — runner binary pre-installed by Packer image
# Executed via QEMU Guest Agent.
# ---------------------------------------------------------------------------


def _render_windows_userdata(
    bootstrap: BootstrapInstance,
    provider_id: str,
) -> str:
    """Render a cloudbase-init PowerShell script for Windows."""
    labels = ",".join(bootstrap.labels) if bootstrap.labels else bootstrap.pool_id
    forge_type = "gitea" if _is_gitea(bootstrap) else "github"

    return f"""\
#ps1_sysnative
$ErrorActionPreference = 'Stop'

$env:METADATA_URL = "{bootstrap.metadata_url.rstrip("/")}"
$env:CALLBACK_URL = "{bootstrap.callback_url}"
$env:BEARER_TOKEN = "{bootstrap.instance_token}"
$env:REPO_URL = "{bootstrap.repo_url}"
$env:RUNNER_NAME = "{bootstrap.name}"
$env:RUNNER_LABELS = "{labels}"
$env:FORGE_TYPE = "{forge_type}"
$env:PROVIDER_ID = "{provider_id}"

& C:\\garm\\scripts\\startup-windows.ps1
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_userdata(
    bootstrap: BootstrapInstance,
    provider_id: str,
    defaults: ClusterConfig,
) -> str:
    """Return the appropriate user-data document for the bootstrap's OS type."""
    if bootstrap.os_type == "windows":
        return _render_windows_userdata(bootstrap, provider_id)
    return _render_linux_userdata(bootstrap, provider_id, defaults)
