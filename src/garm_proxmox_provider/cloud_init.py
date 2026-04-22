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
    """Render a bash script for Linux.

    Per policy, do not modify or inject into a customer-provided install template.
    If a base64-encoded `runner_install_template` is provided in extra_specs,
    decode and return it verbatim. If absent, return an empty string (no injection).
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
    # Return the decoded installer template verbatim, or empty string if none provided.
    return body


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

    return body


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
