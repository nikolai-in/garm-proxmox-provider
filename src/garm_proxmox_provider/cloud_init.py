"""Cloud-init user-data renderer for GARM runner bootstrap."""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import DefaultsConfig
    from .models import BootstrapInstance

# Template for the runner bootstrap runcmd script.
# Uses str.format() placeholders.
_RUNNER_SCRIPT_TEMPLATE = """\
#!/bin/bash
set -euo pipefail

export HOME=/home/runner
RUNNER_HOME=/home/runner/actions-runner

# Create runner user and home dir
id runner &>/dev/null || useradd -m -s /bin/bash runner
mkdir -p "$RUNNER_HOME"
chown runner:runner "$RUNNER_HOME"

# Install dependencies
if command -v apt-get &>/dev/null; then
    apt-get install -y -q curl tar jq git
elif command -v dnf &>/dev/null; then
    dnf install -y curl tar jq git
elif command -v yum &>/dev/null; then
    yum install -y curl tar jq git
fi

cd "$RUNNER_HOME"

# Download runner tarball
RUNNER_FILENAME="{filename}"
RUNNER_DOWNLOAD_URL="{download_url}"
curl -fsSL -o "$RUNNER_FILENAME" "$RUNNER_DOWNLOAD_URL"
{checksum_check}
tar xzf "$RUNNER_FILENAME"

# Fetch runner registration token from GARM metadata
RUNNER_TOKEN=$(curl -fsSL \
    -H "Authorization: Bearer {instance_token}" \
    "{metadata_url}/runner-registration-token" | tr -d '"')

# Configure runner
su -s /bin/bash runner -c \
    "./config.sh \
        --url '{repo_url}' \
        --token '${{RUNNER_TOKEN}}' \
        --name '{name}' \
        --labels '{labels}' \
        --unattended \
        --replace \
        --ephemeral"

# Install and start as a systemd service
./svc.sh install runner
./svc.sh start

# Notify GARM that the instance is running
curl -fsSL -X POST \
    -H "Authorization: Bearer {instance_token}" \
    -H "Content-Type: application/json" \
    "{callback_url}" \
    -d '{{"provider_id":"{provider_id}","name":"{name}","status":"running"}}'
"""

_CHECKSUM_CHECK_TEMPLATE = """\
echo "{sha256}  {filename}" | sha256sum -c - || \\
    {{ echo "Runner checksum mismatch" >&2; exit 1; }}"""


def render_userdata(
    bootstrap: BootstrapInstance,
    provider_id: str,
    defaults: DefaultsConfig,
) -> str:
    """Return a cloud-config YAML document that bootstraps the GARM runner."""
    tool = bootstrap.get_tool()
    download_url = tool.download_url if tool else ""
    filename = tool.filename if tool else "runner.tar.gz"
    sha256 = tool.sha256_checksum if tool else ""

    checksum_check = (
        _CHECKSUM_CHECK_TEMPLATE.format(sha256=sha256, filename=filename)
        if sha256
        else ""
    )

    labels = ",".join(bootstrap.labels) if bootstrap.labels else bootstrap.pool_id

    script = _RUNNER_SCRIPT_TEMPLATE.format(
        filename=filename,
        download_url=download_url,
        checksum_check=checksum_check,
        instance_token=bootstrap.instance_token,
        metadata_url=bootstrap.metadata_url.rstrip("/"),
        repo_url=bootstrap.repo_url,
        name=bootstrap.name,
        labels=labels,
        callback_url=bootstrap.callback_url,
        provider_id=provider_id,
    )

    # Build ssh_authorized_keys list for cloud-config
    ssh_keys: list[str] = []
    if defaults.ssh_public_key:
        ssh_keys.append(defaults.ssh_public_key.strip())

    ssh_block = ""
    if ssh_keys:
        keys_yaml = "\n".join(f"      - {k!r}" for k in ssh_keys)
        ssh_block = f"    ssh_authorized_keys:\n{keys_yaml}\n"

    # Escape the script for embedding in YAML (indent 4 spaces)
    script_indented = textwrap.indent(script.rstrip(), "      ")

    cloud_config = f"""\
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
    return cloud_config
