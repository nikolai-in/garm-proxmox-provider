# CLI reference

The package installs a single command: `garm-proxmox-provider`.

## Invocation

Use a direct subcommand when you can:

```bash
garm-proxmox-provider --config /path/to/garm-provider-proxmox.toml --help
```

The binary also supports the legacy GARM flow. When no subcommand is provided, it reads `GARM_COMMAND` and maps it to a subcommand.

```bash
GARM_COMMAND=ListInstances GARM_PROVIDER_CONFIG_FILE=/path/to/garm-provider-proxmox.toml \
  garm-proxmox-provider
```

## Config and environment

- `--config` or `GARM_PROVIDER_CONFIG_FILE` selects the TOML config file.
- `GARM_LOG_LEVEL`, `GARM_DEBUG`, `GARM_LOG_FILE`, and `GARM_LOG_JSON` control logging.

## Supported commands

### `create-instance`

Reads bootstrap JSON from stdin, creates the instance, and prints Instance JSON to stdout.

```bash
cat bootstrap.json | garm-proxmox-provider create-instance
```

Legacy GARM usage:

```bash
GARM_COMMAND=CreateInstance cat bootstrap.json | garm-proxmox-provider
```

### `delete-instance`

Deletes an instance by VMID.

```bash
garm-proxmox-provider delete-instance --instance-id 105
```

Legacy env var: `GARM_INSTANCE_ID`

### `get-instance`

Returns the current state of one instance.

```bash
garm-proxmox-provider get-instance --instance-id 105
```

Legacy env var: `GARM_INSTANCE_ID`

### `list-instances`

Lists instances for a pool.

```bash
garm-proxmox-provider list-instances --pool-id runners
```

Legacy env var: `GARM_POOL_ID`

### `remove-all-instances`

Deletes all instances created by a controller.

```bash
garm-proxmox-provider remove-all-instances --controller-id ctrl-123
```

Legacy env var: `GARM_CONTROLLER_ID`

### `start`

Starts an instance.

```bash
garm-proxmox-provider start --instance-id 105
```

Legacy env var: `GARM_INSTANCE_ID`

### `stop`

Stops an instance.

```bash
garm-proxmox-provider stop --instance-id 105
```

Legacy env var: `GARM_INSTANCE_ID`

## Notes

- `create-instance` requires bootstrap JSON on stdin.
- `delete-instance` is idempotent when the VM is already missing.
- If you run in `uv`, use `uv run garm-proxmox-provider ...`.
