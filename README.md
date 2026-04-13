# GARM Proxmox Provider (draft)

External provider for [GARM](https://github.com/cloudbase/garm) that provisions GitHub/Gitea runners on Proxmox VE. This document captures the initial plan and scaffolding for implementation.

## Goals (MVP)

- Implement the external provider contract described in `doc/external_provider.md` from GARM:
  - Commands: `CreateInstance`, `DeleteInstance`, `GetInstance`, `ListInstances`, `RemoveAllInstances`, `Start`, `Stop`.
  - Read provider config from `GARM_PROVIDER_CONFIG_FILE`, respond via stdout/exit codes as required.
- Target Proxmox VE via API using [`proxmoxer`](https://pypi.org/project/proxmoxer/).
- Provide a simple CLI entrypoint (`garm-proxmox-provider`, built with `click`) that dispatches the commands.
- Package with `uv` (per `pyproject.toml`) and follow `.versionrc` commit tagging style.

## Non-goals (initially)

- Advanced networking (VLAN tagging, bridges beyond a single configured one).
- Cloud-init templating beyond a minimal user-data for runner bootstrap.
- Proxmox cluster scheduling policies beyond “pick a node by name/round-robin”.
- Windows guests.

## High-level design

- **Executable**: `garm-proxmox-provider` (console script).
- **Command dispatcher**:
  - Reads `GARM_COMMAND` to select handler.
  - Loads config path from `GARM_PROVIDER_CONFIG_FILE`.
  - Reads stdin JSON for `CreateInstance` (bootstrap payload).
- **Config**: TOML file describing Proxmox API endpoint, credentials, defaults.
- **Proxmox API**:
  - Use `proxmoxer.ProxmoxAPI` to talk to PVE.
  - Create VMs from a template/clone (recommended) or from an ISO/cloud-init image.
  - Tag VMs with `GARM_CONTROLLER_ID` and `GARM_POOL_ID` via `tags` or notes.
- **Instance identity**:
  - Return `provider_id` = PVE VMID (string).
  - Store runner name in `name`.
- **User data**:
  - Render cloud-init snippet to install GitHub/Gitea runner using the bootstrap metadata provided by GARM (callback URL, token, labels).
  - Minimal dependencies: `curl`, `tar`, `systemd` service enablement for the runner.

## Proposed config file (TOML)

```toml
# Example: /etc/garm/garm-provider-proxmox.toml
[pve]
host = "https://pve.example.com:8006/api2/json"
user = "root@pam"
token_name = "garm"
token_value = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
verify_ssl = true

[defaults]
node = "pve-node1"
storage = "local-lvm"
pool = "garm"
template_vmid = 9000           # optional: use template clone if set
cores = 2
memory_mb = 4096
disk_gb = 20
bridge = "vmbr0"
ssh_public_key = "ssh-ed25519 AAAA... user@example"
```

## Command behaviors (MVP)

- `CreateInstance`
  - Parse bootstrap JSON from stdin.
  - Clone from `template_vmid` if present; otherwise create VM with cloud-init drive.
  - Set CPU/mem/disk from defaults or `extra_specs`.
  - Attach NIC to configured bridge.
  - Inject cloud-init with runner install script.
  - Start VM; return JSON `Instance` with `status="running"` (or `"building"` if start async).
- `DeleteInstance`
  - Stop and destroy VMID; ignore if missing.
- `GetInstance`
  - Return status, IPs, tags.
- `ListInstances`
  - Filter by `GARM_POOL_ID` tag.
- `RemoveAllInstances`
  - Delete all VMs tagged with `GARM_CONTROLLER_ID`.
- `Start` / `Stop`
  - Power control by VMID.

## Runner bootstrap sketch (cloud-init user-data)

- Create `runner` user.
- Download runner tarball for `os_type`/`arch` from bootstrap payload.
- Configure systemd service to run `./run.sh --startuptype service`.
- Call back to `metadata-url`/`callback-url` with provided token.
- Labels include `runner-controller-id` and `runner-pool-id`.

## Roadmap

- **Phase 0**: Docs & stubs
  - Draft README (this file) and `AGENTS.md` plan.
  - Add CLI skeleton with `click` and command dispatcher.
- **Phase 1**: Proxmox connection & listing
  - Implement config loading, connect via `proxmoxer`, implement `GetInstance`/`ListInstances`.
- **Phase 2**: Creation & deletion
  - Implement `CreateInstance` (clone/template, cloud-init), `DeleteInstance`.
- **Phase 3**: Lifecycle polish
  - Implement `Start`/`Stop`, tag handling, IP discovery.
- **Phase 4**: Packaging & CI
  - Validate with `uv`, add linting (`ruff`, `mypy` optional), smoke tests against a dev PVE.

## Development

- Python `>=3.14`, managed with `uv`.
- Install: `uv sync`
- Run provider: `GARM_COMMAND=ListInstances garm-proxmox-provider`
- Add optional dev tools later: `ruff`, `mypy`, `pytest`.

## Container image (combined GARM + Proxmox provider)

Build and publish the combined image (bundles `garm` + `garm-proxmox-provider`):

```bash
docker build -t ghcr.io/your-org/garm-proxmox-combined:dev .
docker push ghcr.io/your-org/garm-proxmox-combined:dev
```

The image expects `/etc/garm/config.toml` and your provider config (e.g. `/etc/garm/garm-provider-proxmox.toml`) to be mounted at runtime.

## Versioning & commits

- Follow `.versionrc` (conventional commits, sections like `feat`, `fix`, `docs`, etc.).
- Keep commits small per milestone.

## Next steps

- Author `AGENTS.md` with the implementation plan and a checklist.
- Add CLI skeleton and config loader.
- Add minimal PVE client wrapper and data models for instances.