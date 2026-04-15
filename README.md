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
garm-proxmox-provider setup-proxmox \
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
disk_gb = 20

# Map GARM image names to Proxmox templates
[images.ubuntu-slim-runner]
template = 9100
type = "lxc"                 # "vm" for QEMU, "lxc" for containers
lxc_unprivileged = true      # Recommended for LXC

[images.windows-runner]
template = 9200
type = "vm"
```

## CLI Utilities

While GARM interacts with the provider via environment variables, the binary includes explicit subcommands to help you manage and debug your setup locally.

### 1. Lint Configuration
Validates your `config.toml` file to catch missing fields, syntax errors, or logical misconfigurations.
```bash
garm-proxmox-provider lint-config --config /path/to/config.toml
```

### 2. Test Connection
Verifies that the provider can successfully authenticate and communicate with the Proxmox API.
```bash
garm-proxmox-provider test-connection --config /path/to/config.toml
```

### 3. List Templates
Scans the Proxmox cluster and lists all available templates that match your configured `instance_type` (VM or LXC).
```bash
garm-proxmox-provider list-templates --config /path/to/config.toml
```

### 4. Manual Operations
You can manually trigger any GARM operation for testing purposes:
```bash
# Get details of a specific runner
garm-proxmox-provider get-instance --config ./config.toml --instance-id 105

# List all instances in a specific GARM pool
garm-proxmox-provider list-instances --config ./config.toml --pool-id "pool-uuid-here"

# Stop an instance
garm-proxmox-provider stop --config ./config.toml --instance-id 105
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