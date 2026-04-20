# CLI reference

This document describes the package CLI: how to invoke it, the available commands, the legacy `GARM_COMMAND` dispatch mode, relevant environment variables (including logging configuration), and examples.

The console script is installed as `garm-proxmox-provider` (entrypoint defined by the package). The CLI is implemented with `click` and supports both direct subcommand invocation and a legacy environment-based dispatch mode used by GARM.

---

## Quick invocation

- Run a subcommand directly (recommended):

    garm-proxmox-provider --help

- Use legacy GARM-style dispatch (the executable inspects `GARM_COMMAND` and forwards to the matching subcommand; see below for details):

    GARM_COMMAND=CreateInstance garm-proxmox-provider

- Provide an alternative configuration file path:

    garm-proxmox-provider --config /path/to/garm-provider-proxmox.toml <subcommand>

If you installed the package into a uv-managed environment, run the binary inside that environment (for example with `uv run ...` if you use `uv`).

---

## How legacy dispatch works

The CLI supports two modes of invocation:

1. Direct subcommands (preferred)
2. Legacy env dispatch — when no subcommand is provided, the CLI reads the `GARM_COMMAND` environment variable and maps it to a subcommand. The mapping is:

- `CreateInstance` → `create-instance`
- `DeleteInstance` → `delete-instance`
- `GetInstance` → `get-instance`
- `ListInstances` → `list-instances`
- `RemoveAllInstances` → `remove-all-instances`
- `Start` → `start`
- `Stop` → `stop`

When legacy dispatch is used, any click options that expose an `envvar` will be populated from the corresponding environment variable if present. This lets the provider be executed exactly the same way external GARM expects: set `GARM_COMMAND` and required environment variables and pipe JSON to stdin where relevant.

Important behavior notes:

- If neither a subcommand nor `GARM_COMMAND` are present, the CLI exits with status code 1 and prints an error describing valid legacy commands.
- For `CreateInstance` (and therefore `create-instance`) the CLI requires a bootstrap JSON payload on stdin; absence of stdin data will cause an error and exit code 1.

---

## Config file

By default the CLI looks for a provider config at `garm-provider-proxmox.toml`. You can override this file path in two ways:

- Use the `--config` option:
      garm-proxmox-provider --config /etc/garm/proxmox.toml list-instances

- Or set the `GARM_PROVIDER_CONFIG_FILE` environment variable:
      export GARM_PROVIDER_CONFIG_FILE=/etc/garm/proxmox.toml

The config loader expects a TOML file that contains the `[pve]` connection settings and `[defaults]` used when creating VMs/containers. See the project README and `docs/architecture.md` for config shape and examples.

---

## Logging and runtime environment variables

The CLI configures logging from environment variables. These variables control verbosity, file output and optional JSON formatting:

- `GARM_LOG_LEVEL` — explicit log level (e.g. `DEBUG`, `INFO`, `WARNING`, `ERROR`). If set, this takes priority.
- `GARM_DEBUG` — legacy flag; if set to any non-empty value and `GARM_LOG_LEVEL` is not set, logging level becomes `DEBUG`.
- `GARM_LOG_FILE` — if set to a path, a rotating file handler is created (10 MB per file, 5 backups). The CLI will attempt to create parent directories if necessary.
- `GARM_LOG_JSON` — when set to `1`, `true`, or `yes` (case-insensitive), the CLI attempts to format logs as JSON using `pythonjsonlogger`. If that package is not present, plain text formatting is used.

The logger is initialized early every time the CLI runs.

---

## Commands

This section lists available commands with purpose, behavior and usage examples.

Note: `create-instance` reads bootstrap JSON from stdin and returns an Instance JSON object to stdout on success. This is how GARM's external provider contract is implemented.

### create-instance

Create a new runner instance (VM or LXC depending on configuration / pool).

- Description: Reads the GARM bootstrap JSON from stdin, provisions a VM/LXC on Proxmox (clone or create with cloud-init), bootstraps the runner using the rendered cloud-init userdata, and returns Instance JSON to stdout.
- Usage (direct):

    garm-proxmox-provider create-instance < <bootstrap.json

- Usage (legacy):

    GARM_COMMAND=CreateInstance garm-proxmox-provider < <bootstrap.json

- Important: If stdin is empty, the command fails.

Example (stdin JSON is supplied from a file):

    cat bootstrap.json | garm-proxmox-provider create-instance

### delete-instance

Delete and clean up a previously created instance.

- Options / envvars:
  - `--instance-id` (or `GARM_INSTANCE_ID`) — VMID / instance identifier (required)
- Usage:

    garm-proxmox-provider delete-instance --instance-id 105

- Legacy example:

    export GARM_COMMAND=DeleteInstance
    export GARM_INSTANCE_ID=105
    garm-proxmox-provider

Behavior: Stops the VM if running, removes the VM and associated disks. If the instance is already missing, the semantics treat it as success (idempotent delete).

### get-instance

Get details for a single instance.

- Options / envvars:
  - `--instance-id` (or `GARM_INSTANCE_ID`) — VMID / instance identifier (required)
- Usage:

    garm-proxmox-provider get-instance --instance-id 105

Output: prints instance status and discovered IP(s), as JSON-like CLI output from the handler.

### list-instances

List instances for a pool.

- Options / envvars:
  - `--pool-id` (or `GARM_POOL_ID`) — pool identifier (required)
- Usage:

    garm-proxmox-provider list-instances --pool-id my-pool

Behavior: Filters VMs by tags/notes that identify the pool.

### remove-all-instances

Delete all instances created by a controller.

- Options / envvars:
  - `--controller-id` (or `GARM_CONTROLLER_ID`) — controller identifier (required)
- Usage:

    garm-proxmox-provider remove-all-instances --controller-id my-controller

Behavior: Scans VMs tagged with the controller ID and deletes them. Intended for emergency cleanup / testing.

### start

Start a stopped instance.

- Options / envvars:
  - `--instance-id` (or `GARM_INSTANCE_ID`) — VMID / instance identifier (required)
- Usage:

    garm-proxmox-provider start --instance-id 105

### stop

Stop (shutdown) an instance.

- Options / envvars:
  - `--instance-id` (or `GARM_INSTANCE_ID`) — VMID / instance identifier (required)
- Usage:

    garm-proxmox-provider stop --instance-id 105

### test-connection

Quick check to validate credentials and connectivity to the Proxmox VE API.

- Usage:

    garm-proxmox-provider test-connection

This command attempts to connect using the provider config and prints Proxmox version on success. Exit code is non-zero on failure.

### list-templates

List available templates (both QEMU and LXC template resources).

- Usage:

    garm-proxmox-provider list-templates

Output: a human-readable table of `VMID`, `TYPE`, `NAME`, `NODE`.

### lint-config

Validate the provider TOML configuration.

- Usage:

    garm-proxmox-provider lint-config

This performs static checks on the config shape and required fields.

### setup-proxmox

Interactive helper to create recommended users/roles/pools on a Proxmox host.

- Usage (interactive):

    garm-proxmox-provider setup-proxmox --host <https://pve.example:8006> --root-user root@pam

This command will prompt for the root password and create a user, role and token suitable for GARM usage. See `--help` for all options.

---

## Examples

Create an instance using a bootstrap file:

    # direct subcommand
    cat bootstrap.json | garm-proxmox-provider create-instance

    # legacy dispatch (identical behavior)
    export GARM_COMMAND=CreateInstance
    cat bootstrap.json | garm-proxmox-provider

Delete an instance:

    garm-proxmox-provider delete-instance --instance-id 120

List instances for a pool:

    garm-proxmox-provider list-instances --pool-id runners

Run a fast connection test:

    garm-proxmox-provider test-connection

Run the CLI inside a uv-managed development environment (if you use `uv` for dev deps):

    uv sync --dev
    uv run garm-proxmox-provider --help

Or build docs / run other tasks inside the uv environment before invoking the CLI.

---

## Behavior & error handling

- Exit codes:
  - `0` on success,
  - `1` on common errors (invalid args, connection errors, missing stdin for create),
  - other codes may be used when subcomponents raise SystemExit with custom codes.
- Idempotency:
  - `delete-instance` and `remove-all-instances` should be safe to re-run; deleting a missing VM is treated as success.
- Autodetection:
  - The CLI attempts to read parameters from environment variables for backward compatibility when invoked via legacy dispatch. When running direct subcommands, prefer passing explicit options.

---

## Tips for maintainers

- Keep invocation compatible with the `GARM_COMMAND` legacy mapping so external GARM controllers can invoke the provider without subcommand-level changes.
- When changing click option names or environment variable names, preserve old envvars (or support both) whenever possible to avoid breaking existing automation.
- The logger setup is centralized in the CLI bootstrap function; extending the logging configuration (e.g., custom handlers, syslog) is straightforward by modifying `_setup_logging` in `src/garm_proxmox_provider/cli.py`.

---

If you'd like, I can:

- add short sample bootstrap JSON to this file,
- include a small ASCII flow diagram illustrating legacy dispatch → subcommand mapping,
- or convert this document into MyST-flavored Markdown with inline admonitions and richer examples.
