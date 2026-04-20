# Configuration

This document describes the TOML configuration schema used by the `garm-proxmox-provider`
and shows common examples. The provider reads a TOML file (default `garm-provider-proxmox.toml`)
— the path can be changed with the CLI or with the `GARM_PROVIDER_CONFIG_FILE` environment variable.

Contents

- Overview
- How the config is loaded
- Top-level schema and field descriptions
- Example: minimal config
- Example: full config with per-pool overrides
- LXC vs QEMU notes
- Validation, logging and environment variables
- Common pitfalls and troubleshooting

Overview

The provider expects connection credentials for a Proxmox VE API token (or user+password),
global defaults for VM/container creation, and optional per-pool overrides so different
runner pools can use different sizes or storage. The provider also supports a few cluster-scoped
feature flags (e.g., SSH fallback when the QEMU Guest Agent is unavailable).

How the config is loaded

- CLI default: the CLI looks for a config file path from the `--config` option or the
  `GARM_PROVIDER_CONFIG_FILE` environment variable. If unspecified the CLI defaults to
  `garm-provider-proxmox.toml`.
- The `lint-config` subcommand (`garm-proxmox-provider lint-config`) validates the TOML and key
  semantics (see the "Validation" section).
- When building docs or running tests via the uv-managed environment, the package modules are
  imported from `src/` so the code can parse the config as in normal runtime.

Top-level schema

The file is TOML and should contain at least a `[pve]` table plus a `[defaults]` table. A suggested
schema is shown below. Comments explain the meaning and types.

- `[pve]` — Proxmox API connection
  - `host` (string, required): URL of the Proxmox API endpoint (e.g. `https://pve.example:8006`)
  - `user` (string, required): username including realm (e.g. `garm@pve` or `root@pam`)
  - `token_name` (string, optional): API token name (preferred to password)
  - `token_value` (string, optional): API token value
  - `password` (string, optional): plain password (less recommended)
  - `verify_ssl` (boolean, default true): whether to verify TLS certificates

- `[defaults]` — provider defaults applied to created instances
  - `node` (string): default Proxmox node name to create VMs/containers on
  - `storage` (string): default storage identifier for disks/cloud-init
  - `pool` (string): default GARM pool id/tag to set on created resources
  - `template_vmid` (integer, optional): template VMID to clone from (if present provider clones)
  - `cores` (integer): default CPU count for new VMs
  - `memory_mb` (integer): default RAM size in MiB
  - `disk_gb` (integer): default disk size for freshly created VM (when not cloning)
  - `bridge` (string): default bridge to attach nic to (e.g. `vmbr0`)
  - `ssh_public_key` (string, optional): SSH public key to add to cloud-init user
  - `cloud_init_user` (string, default `runner`): username created by cloud-init scripts

- `[[pools]]` or `extra_specs` — per-pool overrides
  - One approach is a `[[pools]]` array of tables with `name` and overrides, or a `defaults.extra_specs`
    mapping keyed by pool id. The provider accepts per-pool overrides used to override the
    `[defaults]` for a specific GARM pool (e.g. `linux-x64`, `windows-2019`).

- `[cluster]` (optional) — cluster-level settings
  - `qm_ssh_fallback` (boolean, default false): when true, if the QEMU Guest Agent commands fail,
    the provider will try to execute bootstrap commands via an SSH connection to the Proxmox host
    using `qm guest exec` (requires SSH key/permissions).
  - `qm_ssh_user` (string, optional): user to use for the SSH fallback `qm` invocation.
  - `qm_ssh_identity_file` (string, optional): private key file path for SSH fallback.

- `[logging]` (optional)
  - `file` (string): path to a rotating log file (if set, the CLI will attempt to create and write)
  - `level` (string): default log level (e.g. `INFO`, `DEBUG`)

Note: the exact table / field names used by your runtime loader are documented above. If your
project's `config.py` expects a slightly different layout you may need to adapt; the examples below
match the schema used across the project documentation and CLI.

Minimal example

A minimal config that uses an API token to authenticate and provides a few sensible defaults:

```/dev/null/minimal-config.toml#L1-40
# Minimal provider config
[pve]
host = "https://pve.example.com:8006"
user = "garm@pve"
token_name = "garm-token"
token_value = "XXXXXXXXXXXXXXXXXXXXXXXXXXXX"
verify_ssl = true

[defaults]
node = "pve-node-1"
storage = "local-lvm"
pool = "garm"
cores = 2
memory_mb = 4096
disk_gb = 20
bridge = "vmbr0"
cloud_init_user = "runner"
# ssh_public_key = "ssh-ed25519 AAAA..."   # optional
```

Full example with per-pool overrides

This example shows cloning from a QEMU template and providing per-pool overrides using a `[[pools]]`
table array. Replace values with your environment specifics.

```/dev/null/full-config.toml#L1-160
# Full example with per-pool override tables
[pve]
host = "https://pve.example.com:8006"
user = "garm@pve"
token_name = "garm-token"
token_value = "XXXXXXXXXXXXXXXXXXXXXXXXXXXX"
verify_ssl = true

[defaults]
node = "pve-node-1"
storage = "local-lvm"
pool = "garm"
# If template_vmid is set, the provider will attempt to clone the template VM for new instances.
# Otherwise it will create a new VM with a cloud-init drive.
template_vmid = 9000
cores = 2
memory_mb = 4096
disk_gb = 20
bridge = "vmbr0"
cloud_init_user = "runner"
ssh_public_key = "ssh-ed25519 AAAA... user@example.com"

# Per-pool overrides using an array of tables
[[pools]]
name = "linux-small"
cores = 1
memory_mb = 2048
disk_gb = 10
node = "pve-node-2"

[[pools]]
name = "linux-large"
cores = 4
memory_mb = 8192
disk_gb = 40
node = "pve-node-1"

# Cluster-wide feature flags
[cluster]
qm_ssh_fallback = true
qm_ssh_user = "root"
qm_ssh_identity_file = "/root/.ssh/id_rsa"

[logging]
file = "/var/log/garm-proxmox-provider.log"
level = "INFO"
```

Alternative per-pool mapping

If you prefer a mapping keyed by pool id, you can use `defaults.extra_specs` (a TOML table)
instead of `[[pools]]`. This approach is easy to look up at runtime:

```/dev/null/per-pool-mapping.toml#L1-80
[pve]
host = "https://pve.example.com:8006"
user = "garm@pve"
token_name = "garm-token"
token_value = "XXXXXXXXXXXX"
verify_ssl = true

[defaults]
node = "pve-node-1"
storage = "local-lvm"
pool = "garm"
cores = 2
memory_mb = 4096
disk_gb = 20
bridge = "vmbr0"

[defaults.extra_specs.linux-small]
cores = 1
memory_mb = 2048

[defaults.extra_specs.linux-large]
cores = 4
memory_mb = 8192
```

Cloud-init and runner bootstrap

- If `template_vmid` is configured, the provider will attempt to clone that VM and then configure
  a cloud-init drive and metadata to bootstrap the runner. The cloud-init userdata is rendered by
  the `cloud_init.py` templating code and includes the GARM bootstrap payload.
- If a template is not available the provider can create a VM with a cloud-init disk; ensure the
  configured `storage` supports cloud-init volumes.
- For LXC containers the provider injects the bootstrap script via the LXC exec API or through
  container-specific cloud-init metadata when available.

LXC vs QEMU specifics

- QEMU (recommended when you need full VM isolation or guest agent features):
  - Use `template_vmid` to clone golden images.
  - QEMU Guest Agent (QGA) is preferred for running commands inside the VM (e.g. running the bootstrap script).
  - If QGA fails, `cluster.qm_ssh_fallback` allows falling back to host-based `qm guest exec` via SSH.

- LXC:
  - LXC containers are created using the Proxmox LXC API. The provider will try to inject and
    execute the bootstrap script via container exec.
  - Networking and UID/GID mappings differ from QEMU; ensure your LXC templates are prepared for
    the runner bootstrap process.

Validation and linting

- Use the CLI helper `garm-proxmox-provider lint-config --config path/to/config.toml`
  to run basic validation rules (presence of required keys, value types).
- The linter checks required `[pve]` settings and common defaults; it will also warn about
  missing `template_vmid` when using features that expect it.

Logging and environment variables

The CLI configures logging from environment variables and optionally from the TOML `logging` table.
The environment variables used are:

- `GARM_LOG_LEVEL` (e.g. `DEBUG`, `INFO`, `WARNING`, `ERROR`): takes precedence when set.
- `GARM_DEBUG` (legacy boolean): if set and `GARM_LOG_LEVEL` is not set, maps to DEBUG.
- `GARM_LOG_FILE` (path): when set the CLI will attempt to add a rotating file handler.
- `GARM_LOG_JSON` (1/true/yes): try to use JSON formatter (requires optional dependency).

CLI environment & legacy GARM compatibility

- `GARM_COMMAND`: legacy environment variable used by GARM to invoke specific provider actions
  (e.g. `CreateInstance`). The CLI maps legacy values to modern subcommands (see `cli.py`).
- `GARM_PROVIDER_CONFIG_FILE`: if set, the CLI uses this path as the TOML config file.
- `GARM_POOL_ID`, `GARM_CONTROLLER_ID`, `GARM_INSTANCE_ID`: standard environment variables used by
  the individual subcommands (e.g. `list-instances`, `remove-all-instances`, `delete-instance`).

How the provider uses pool/tag metadata

- Created VMs/containers are tagged/annotated with the `pool` and the `controller_id` so they can
  be filtered and removed by the `ListInstances` / `RemoveAllInstances` commands.
- The provider sets provider-specific metadata in VM notes or tags so that re-run/cleanup operations
  can match resources belonging to a particular GARM controller and pool.

Common pitfalls & troubleshooting

- TLS verification: if your Proxmox server uses self-signed certificates, either set `verify_ssl = false`
  (not recommended in production) or configure system/trusted CA so the client can verify.
- Permissions: ensure the token or user has the necessary permissions (datacenter, node, storage, pool).
- Template cloning: when `template_vmid` is used, the template must be present on the node/storage where cloning is attempted.
- Cloud-init support: some storage backends or image templates do not support cloud-init drives; verify your template supports cloud-init.
- SSH fallback: if `cluster.qm_ssh_fallback` is enabled, ensure the SSH identity used has permission on the Proxmox host to run `qm` and that the host accepts the key.

Where to go next

- See `docs/architecture.md` for diagrams showing how the CLI, command handlers, and `PVEClient` interact.
- Use `garm-proxmox-provider --help` (or `garm-proxmox-provider <subcommand> --help`) to explore CLI options.
- If you want a sample config committed to the repo, copy the minimal example above to
  `garm-provider-proxmox.toml` and replace the placeholder values with your environment settings.

If you need me to:

- Add a validated example config file to the repository,
- Convert the examples into a config template generator, or
- Add CI checks that validate `lint-config` when PRs change docs/config,

tell me which you'd like and I will prepare the change.
