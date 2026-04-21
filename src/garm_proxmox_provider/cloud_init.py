"""Cloud-init / cloudbase-init user-data renderer for GARM runner bootstrap.

Bootstrap scripts are provided entirely by GARM via ``runner_install_template``
in ``extra_specs`` (base64-encoded).  No baked-in fallback paths are used;
the rendered script only exports environment variables when no install template
is present.  GARM images must pick up those env vars themselves.

SSH public keys can be provided via:
  - ``[cluster].ssh_public_key`` in the provider TOML config, or
  - ``extra_specs.ssh_public_key`` per pool in GARM (takes precedence).
"""

from __future__ import annotations

import base64
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import ClusterConfig
    from .models import BootstrapInstance

logger = logging.getLogger(__name__)

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
# Linux scripts
# ---------------------------------------------------------------------------


def _render_linux_userdata(
    bootstrap: BootstrapInstance,
    provider_id: str,
    defaults: ClusterConfig,
) -> str:
    """Render a bash script for Linux (works with cloud-init and LXC exec).

    The bootstrap body is sourced exclusively from GARM's
    ``runner_install_template`` extra_spec (base64-encoded).  When that key is
    absent the script still exports all required env vars so that a GARM image
    can pick them up via its own mechanism.
    """
    labels = ",".join(bootstrap.labels) if bootstrap.labels else bootstrap.pool_id
    forge_type = "gitea" if _is_gitea(bootstrap) else "github"

    # SSH key: extra_specs override takes precedence over cluster config.
    ssh_key = (bootstrap.extra_specs or {}).get("ssh_public_key") or defaults.ssh_public_key
    ssh_setup = ""
    if ssh_key:
        key = ssh_key.strip()
        ssh_setup = f"""\
mkdir -p /home/runner/.ssh
echo "{key}" >> /home/runner/.ssh/authorized_keys
chown -R runner:runner /home/runner/.ssh
chmod 700 /home/runner/.ssh
chmod 600 /home/runner/.ssh/authorized_keys
"""

    env_block = f"""\
export METADATA_URL="{bootstrap.metadata_url.rstrip("/")}"
export CALLBACK_URL="{bootstrap.callback_url}"
export BEARER_TOKEN="{bootstrap.instance_token}"
export REPO_URL="{bootstrap.repo_url}"
export RUNNER_NAME="{bootstrap.name}"
export RUNNER_LABELS="{labels}"
export FORGE_TYPE="{forge_type}"
export PROVIDER_ID="{provider_id}"
"""

    # GARM-rendered install template from extra_specs (base64-encoded).
    # This IS the full bootstrap body; when absent, only env vars are exported.
    body = ""
    installer_b64 = (bootstrap.extra_specs or {}).get("runner_install_template")
    if installer_b64:
        try:
            body = base64.b64decode(installer_b64).decode(errors="replace")
        except Exception as exc:
            logger.warning(
                "Failed to decode runner_install_template for %s: %s",
                bootstrap.name,
                exc,
            )

    return f"""#!/bin/bash
set -euo pipefail

{ssh_setup}{env_block}
{body}"""


# ---------------------------------------------------------------------------
# Windows scripts — executed via QEMU Guest Agent.
# ---------------------------------------------------------------------------


def _render_windows_userdata(
    bootstrap: BootstrapInstance,
    provider_id: str,
) -> str:
    """Render a cloudbase-init PowerShell script for Windows.

    The bootstrap body is sourced from GARM's ``runner_install_template``
    extra_spec (base64-encoded).  When absent, only env vars are set.
    """
    labels = ",".join(bootstrap.labels) if bootstrap.labels else bootstrap.pool_id
    forge_type = "gitea" if _is_gitea(bootstrap) else "github"

    env_block = f"""\
$env:METADATA_URL = "{bootstrap.metadata_url.rstrip("/")}"
$env:CALLBACK_URL = "{bootstrap.callback_url}"
$env:BEARER_TOKEN = "{bootstrap.instance_token}"
$env:REPO_URL = "{bootstrap.repo_url}"
$env:RUNNER_NAME = "{bootstrap.name}"
$env:RUNNER_LABELS = "{labels}"
$env:FORGE_TYPE = "{forge_type}"
$env:PROVIDER_ID = "{provider_id}"
"""

    body = ""
    installer_b64 = (bootstrap.extra_specs or {}).get("runner_install_template")
    if installer_b64:
        try:
            body = base64.b64decode(installer_b64).decode(errors="replace")
        except Exception as exc:
            logger.warning(
                "Failed to decode runner_install_template for %s: %s",
                bootstrap.name,
                exc,
            )

    return f"""\
#ps1_sysnative
$ErrorActionPreference = 'Stop'

{env_block}
{body}"""


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
