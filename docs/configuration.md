# Configuration

This provider reads a TOML file, usually named `garm-provider-proxmox.toml`. Pass it with `--config` or set `GARM_PROVIDER_CONFIG_FILE`.

## Required sections

Use these sections to get started:

- `[pve]` for Proxmox connectivity.
- `[cluster]` for the target node, storage, pool, and bridge.
- `[flavors.default]` for a default VM size.

The provider does not use an `[images]` section. The bootstrap `image` value must match a Proxmox template name.

### Minimal example

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

## Field guide

### `[pve]`

- `host` required: Proxmox API URL.
- `user` required: Proxmox username with realm, for example `garm@pve`.
- `token_name` required: API token name.
- `token_value` required: API token value.
- `verify_ssl` optional, default `true`.

### `[cluster]`

- `node` required: Proxmox node to create instances on.
- `storage` optional, default `local-lvm`.
- `pool` optional: pool name to tag created instances with.
- `bridge` optional, default `vmbr0`.
- `snippets_storage` optional: storage for snippets/cloud-init files.
- `ssh_public_key` optional: injected into the runner user when supported by the bootstrap.
- `lxc_unprivileged` optional, default `true`.

### `[flavors]`

Define one or more named flavors. GARM can request a flavor per pool; if none is set, `default` is used.

```toml
[flavors.default]
cores = 2
memory_mb = 4096

[flavors.large]
cores = 4
memory_mb = 8192
```

## Optional settings

### `[logging]`

The CLI can read optional logging defaults from TOML:

- `level` such as `INFO` or `DEBUG`
- `file` for a rotating log file
- `json` to request JSON log output
- `debug_dump` for extra startup diagnostics

### `[vmid_range]`

Optional range used when the provider chooses random VMIDs:

```toml
[vmid_range]
min = 1100
max = 1999
```

## Bootstrap input

`create-instance` receives a GARM bootstrap JSON object on stdin. The provider uses the following fields from that payload:

- `name`
- `controller_id`
- `pool_id`
- `flavor`
- `image`
- `os_type`
- `os_arch`
- `extra_specs`

The `image` field should point to the Proxmox template name you want to clone or use.

## Next step

See [cli.md](cli.md) for the command list and [setup.md](setup.md) for a short Proxmox preparation checklist.

If you need me to:

- Add a validated example config file to the repository,
- Convert the examples into a config template generator, or
- Add CI checks that validate `lint-config` when PRs change docs/config,

tell me which you'd like and I will prepare the change.
