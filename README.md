# GARM Proxmox Provider

`garm-proxmox-provider` is an external GARM provider for Proxmox VE. It creates runners as either QEMU VMs or LXC containers and supports the standard GARM lifecycle commands.

## Quick Start

1. Install the package with `uv`.

```bash
git clone https://github.com/nikolai-in/garm-proxmox-provider.git
cd garm-proxmox-provider
uv sync
```

2. Create a TOML config file and point the provider at it with `--config` or `GARM_PROVIDER_CONFIG_FILE`.

```toml
[pve]
host = "https://pve.example.com:8006"
user = "garm@pve"
token_name = "garm"
token_value = "REPLACE_ME"
verify_ssl = true

[cluster]
node = "pve-node1"
storage = "local-lvm"
pool = "garm"
bridge = "vmbr0"

[flavors.default]
cores = 2
memory_mb = 4096
```

3. Make sure the Proxmox template you want to use exists and is marked as a template. The bootstrap `image` value must match that template name.

4. Run the provider directly or through GARM.

```bash
garm-proxmox-provider --config /path/to/garm-provider-proxmox.toml --help

GARM_COMMAND=ListInstances GARM_PROVIDER_CONFIG_FILE=/path/to/garm-provider-proxmox.toml \
  garm-proxmox-provider
```

For `CreateInstance`, pipe the GARM bootstrap JSON to stdin.

## What the Provider Needs

- A Proxmox API token with permission to manage the target node, storage, and pool.
- A Proxmox template or container image that matches the bootstrap `image` field.
- For QEMU VMs, cloud-init and the QEMU Guest Agent are recommended.
- For LXC containers, the base image should include the tools your bootstrap script needs.

## Configuration

See [docs/configuration.md](docs/configuration.md) for the exact TOML schema and [docs/cli.md](docs/cli.md) for the supported commands.

## GARM Integration

Register the binary as an external provider and set `config_file` to your TOML path. The binary supports the legacy `GARM_COMMAND` flow used by GARM controllers.

## Development

- `uv sync` for dependencies
- `uv run pytest` for tests
- `uv run sphinx-build -b html docs docs/_build/html` for docs
