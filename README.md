# GARM Proxmox Provider

A fully compliant external provider for [GARM](https://github.com/cloudbase/garm) that provisions GitHub/Gitea runners on Proxmox VE.

This provider supports creating runners as either **QEMU Virtual Machines** (via cloud-init) or **LXC Containers** (via environment variables), and seamlessly integrates with GARM's lifecycle management.

## Features

- **Full GARM Lifecycle Support**: Implements `CreateInstance`, `DeleteInstance`, `GetInstance`, `ListInstances`, `RemoveAllInstances`, `Start`, and `Stop`.
- **Dual Runtime Modes**: Support for both QEMU VMs (`instance_type = "vm"`) and unprivileged LXC containers (`instance_type = "lxc"`).
- **Per-OS Templates**: Dynamically route runner creation to specific Proxmox templates based on OS and Architecture (e.g., `linux/amd64` vs `windows/amd64`).
- **Automated Setup Utility**: Built-in CLI tool to automatically configure Proxmox roles, users, pools, and API tokens with the principle of least privilege.
- **CLI Debugging Tools**: Explicit subcommands for linting configuration, testing API connections, and listing cluster templates.

## Installation

This project is built and managed with [`uv`](https://docs.astral.sh/uv/).

```bash
# Clone the repository
git clone https://github.com/nikolai-in/garm-proxmox-provider.git
cd garm-proxmox-provider

# Sync dependencies and install the CLI
uv sync
```

Once installed, the `garm-proxmox-provider` binary will be available in your environment.

## Proxmox Setup & Bootstrapping

Before using the provider, Proxmox needs a dedicated user, role, and resource pool. We provide a fully automated setup utility that creates these with the exact minimum privileges required.

```bash
garm-proxmox-provider admin setup-proxmox \
    --host "https://pve.example.com:8006" \
    --root-user "root@pam" \
    --garm-user "garm@pve" \
    --garm-pool "garm"
```

The utility will prompt for your Proxmox root password, configure the cluster, and output a ready-to-use snippet for your `config.toml` containing the newly generated API token.

## Configuration

The provider expects a TOML configuration file. The path to this file is passed via the `GARM_PROVIDER_CONFIG_FILE` environment variable or the `--config` CLI flag.

```toml
# Example: /etc/garm/garm-provider-proxmox.toml

[pve]
host = "https://pve.example.com:8006"
user = "garm@pve"
token_name = "garm"
token_value = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
verify_ssl = true

[cluster]
node = "pve-node1"           # Target Proxmox node
pool = "garm"                # Resource pool to place runners into
storage = "local-lvm"        # Storage for clone disks

[flavors.default]
cores = 4
memory_mb = 4096

# Map GARM image names to Proxmox templates.
# 'template' is the Proxmox VMID of the template to clone.
[images.ubuntu-slim-runner]
template = 9100
type = "lxc"                 # "vm" for QEMU, "lxc" for containers
lxc_unprivileged = true      # Recommended for LXC

[images.windows-runner]
template = 9200
type = "vm"
```

## CLI Utilities

While GARM interacts with the provider via environment variables, the binary exposes utility sub-commands for humans.

### Debugging

```bash
# Validate your config.toml
garm-proxmox-provider --config /path/to/config.toml debug lint-config

# Verify connectivity to the Proxmox API
garm-proxmox-provider --config /path/to/config.toml debug test-connection

# List all available VM/LXC templates on the cluster
garm-proxmox-provider --config /path/to/config.toml debug list-templates
```

### Manual lifecycle operations

You can trigger any GARM operation directly for testing:

```bash
# Get details of a specific runner
garm-proxmox-provider --config ./config.toml get-instance --instance-id 105

# List all instances in a specific GARM pool
garm-proxmox-provider --config ./config.toml list-instances --pool-id "pool-uuid-here"

# Stop an instance
garm-proxmox-provider --config ./config.toml stop --instance-id 105
```

### Proxmox cluster setup

```bash
garm-proxmox-provider admin setup-proxmox \
    --host "https://pve.example.com:8006" \
    --root-user "root@pam" \
    --garm-user "garm@pve" \
    --garm-pool "garm"
```

## GARM Integration

To use this provider with GARM, register it in your GARM `config.toml`:

```toml
[[providers]]
name = "proxmox"
description = "Proxmox VE Provider"
provider_type = "external"
setup_script = "/path/to/garm-proxmox-provider"
config_file = "/etc/garm/garm-provider-proxmox.toml"
```

The provider maintains 100% compatibility with GARM's `GARM_COMMAND` environment variable dispatch model. When GARM invokes the binary without CLI arguments, the provider will seamlessly read `GARM_COMMAND` and route it to the correct internal handler.

## Development

- Python `>=3.14`, managed with `uv`.
- **Linting & Formatting:** `uv run ruff check` and `uv run ruff format`
- **Type Checking:** `uv run mypy src tests`
- **Testing:** `uv run pytest`

All core logic is decoupled from environment variables, making it highly testable and extensible.

## Logging

The provider logs to **stderr** by default (safe for Docker). Log behaviour is controlled entirely through environment variables so no config-file changes are needed.

| Variable         | Default   | Description                                                                                                                       |
| ---------------- | --------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `GARM_LOG_LEVEL` | `WARNING` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR`                                                                                    |
| `GARM_DEBUG`     | _(unset)_ | Legacy flag — if set (any value) and `GARM_LOG_LEVEL` is absent, maps to `DEBUG`                                                  |
| `GARM_LOG_FILE`  | _(unset)_ | Full path for a rotating log file (10 MB × 5 backups). Directory is created automatically.                                        |
| `GARM_LOG_JSON`  | _(unset)_ | Set to `1`, `true`, or `yes` to emit JSON-formatted log lines (requires `python-json-logger`). Falls back to text if unavailable. |

**Docker recommendation** — rely on container stdout/stderr for log capture. Only set `GARM_LOG_FILE` when you mount a host directory for persistent logs:

```bash
# Debug in a container (logs go to stderr / Docker log driver):
docker run -e GARM_LOG_LEVEL=DEBUG ...

# Persist logs to a mounted host path:
docker run -e GARM_LOG_FILE=/var/log/garm/provider.log \
           -v /host/logs:/var/log/garm ...
```

## QGA SSH Fallback (opt-in)

By default the provider executes the bootstrap script inside QEMU VMs via the **QEMU Guest Agent** (`agent.exec`). When the guest agent is unavailable you can enable an opt-in fallback that runs `qm guest exec` on the Proxmox host over SSH.

Add these optional keys to your `[cluster]` section:

```toml
[cluster]
# ... existing keys ...

# Optional: fall back to qm guest exec over SSH when QGA fails
qm_ssh_fallback      = true         # default: false
qm_ssh_user          = "root"       # default: "root"
qm_ssh_identity_file = "/home/garm/.ssh/id_ed25519"  # optional
```

> **Security note** — See the [Security](#security) section below before enabling this.

## Testing

Run the standard unit tests:

```bash
pytest
```

Run the local end-to-end tests (hermetic — no network required):

```bash
pytest -m local
```

Run a single test:

```bash
pytest tests/test_e2e_local.py::test_create_instance_qemu_executes_userdata_via_qga -v
```

## Documentation

Build the HTML documentation locally:

```bash
# Sync development dependencies (this installs Sphinx, the theme and extensions via uv)
uv sync --dev

# Build the HTML documentation using the uv-managed environment
uv run sphinx-build -b html docs docs/_build/html
```

Then open `docs/_build/html/index.html`. The docs use the
[furo](https://github.com/pradyunsg/furo) theme and include Mermaid architecture
diagrams.

## Security

### `qm_ssh_fallback`

Enabling `qm_ssh_fallback = true` grants the provider SSH access to your Proxmox
host with enough privilege to run `qm`. This carries the following implications:

- The SSH key must have access to a user that can execute `qm` on the Proxmox node.
  Running as `root@pam` is the simplest option but also the broadest privilege level.
- Keep the private key file readable only by the service account running the provider
  (`chmod 600`).
- Anyone who gains access to the key can execute arbitrary commands as the configured
  SSH user on the Proxmox host.
- Consider restricting the key to `command="qm guest exec ..."` in
  `~/.ssh/authorized_keys` on the Proxmox node to limit blast radius.
- This fallback is entirely **opt-in** and disabled by default.
