```{toctree}
:caption: Contents:
:maxdepth: 2

architecture.md
setup.md
testing_templates.md
api.md
configuration.md
cli.md
```

# garm-proxmox-provider

## Overview

`garm-proxmox-provider` is an external GARM provider that provisions ephemeral runners on Proxmox VE. It supports creating runners as either:

- QEMU virtual machines (cloud-init + QEMU Guest Agent)
- LXC containers (exec-injection)

This index focuses on the practical steps to prepare the Proxmox environment, validate templates, and run quick smoke tests.

---

## Quick checklist

- [ ] Ensure a Proxmox API token (or user+password) with sufficient privileges is available.
- [ ] Confirm the template image exists and is marked as `template` on the correct node/storage.
- [ ] Verify cloud-init and/or QEMU Guest Agent are installed in QEMU templates.
- [ ] Verify LXC templates include required runtime tools (bash, curl, tar).
- [ ] Prepare a minimal GARM bootstrap JSON payload for test provisioning.

---

## Minimal provider config (TOML)

Place a TOML config file and reference it with `--config <path>` or via the `GARM_PROVIDER_CONFIG_FILE` environment variable.

```toml
[pve]
host = "https://pve.example.local:8006"
user = "garm@pve"
token_name = "garm"
token_value = "REPLACE_WITH_TOKEN"
verify_ssl = true

[defaults]
node = "pve-node-1"
storage = "local-lvm"
pool = "garm"
template_vmid = 9000     # optional: set to clone from existing template
cores = 2
memory_mb = 2048
disk_gb = 10
bridge = "vmbr0"
ssh_public_key = ""      # optional: cloud-init key
```

---

## Validate connectivity and discover templates

- Test API connectivity:

```bash
garm-proxmox-provider --config ./garm-provider-proxmox.toml test-connection
# or, if using a uv-managed environment:
uv run garm-proxmox-provider --config ./garm-provider-proxmox.toml test-connection
```

- List templates reported by the cluster:

```bash
garm-proxmox-provider --config ./garm-provider-proxmox.toml list-templates
```

Expected output: a table containing `VMID`, `TYPE`, `NAME`, and `NODE`. Verify the template VMID you intend to clone is present and reachable on the node you plan to use.

---

## How to test a QEMU template (cloning flow)

1. Confirm the template VMID exists and is a valid cloud-init template (if you intend to use cloud-init).
2. Prepare a minimal GARM bootstrap JSON (this is what the provider expects on stdin for `create-instance`).

Example minimal bootstrap payload (replace placeholders as needed):

```json
{
  "labels": {
    "runner-controller-id": "ctl-123",
    "runner-pool-id": "pool-1"
  },
  "os_type": "linux",
  "runner_name": "test-runner-01",
  "bootstrap_url": "https://example.com/bootstrap.sh",
  "bootstrap_token": "TOKEN"
}
```

Invoke the provider to create an instance (direct subcommand style):

```bash
cat bootstrap.json | garm-proxmox-provider --config ./garm-provider-proxmox.toml create-instance
```

Or using legacy GARM dispatch:

```bash
export GARM_COMMAND=CreateInstance
cat bootstrap.json | garm-proxmox-provider --config ./garm-provider-proxmox.toml
```

After issuing the create, confirm:

- The VM was cloned/created on the expected node/storage.
- The VM started.
- The provider returned Instance JSON containing `provider_id` (VMID).

---

## How to test an LXC template (exec-injection flow)

For LXC templates, the provider injects and executes the bootstrap script inside the container. Use similar steps to QEMU but validate exec semantics rather than cloud-init.

Create an instance (same provider command):

```bash
cat bootstrap.json | garm-proxmox-provider --config ./garm-provider-proxmox.toml create-instance
```

If the provider fails to inject or execute the bootstrap, validate manually on the Proxmox host:

```bash
# Replace <CTID> with the container ID
pct exec <CTID> -- /bin/bash -c 'echo hello'
```

Ensure:

- The container template contains necessary tools (bash, curl/wget, tar).
- Network allows outbound access to bootstrap URLs.

---

## Verifying guest-agent / cloud-init behavior (QEMU)

The provider prefers the QEMU Guest Agent (QGA) to run bootstrap payloads. Test agent and cloud-init manually:

```bash
# From the Proxmox host or using provider helper commands:
garm-proxmox-provider --config ./garm-provider-proxmox.toml get-instance --instance-id <VMID>

# On the Proxmox host (quick check)
ssh root@pve-host "qm agent <VMID> ping"

# Inside a Linux guest, verify services:
systemctl status qemu-guest-agent
tail -n 100 /var/log/cloud-init.log /var/log/cloud-init-output.log
```

If the guest agent is not responding:

- Confirm the template includes the guest agent package and the service is enabled.
- Check firewall and network settings.
- If QGA is not reliable in your environment, consider enabling SSH fallback in the `[cluster]` config only after evaluating security implications.

---

## Example quick tests (separate QEMU and LXC examples)

QEMU flow (simple sequence):

```bash
# Create
cat bootstrap.json | garm-proxmox-provider --config ./garm-provider-proxmox.toml create-instance

# Check (replace with returned VMID)
garm-proxmox-provider --config ./garm-provider-proxmox.toml get-instance --instance-id <VMID>

# Delete
garm-proxmox-provider --config ./garm-provider-proxmox.toml delete-instance --instance-id <VMID>
```

LXC flow (simple sequence):

```bash
# Create
cat bootstrap.json | garm-proxmox-provider --config ./garm-provider-proxmox.toml create-instance

# Check (replace with returned CTID)
garm-proxmox-provider --config ./garm-provider-proxmox.toml get-instance --instance-id <CTID>

# If bootstrap fails, debug with:
pct exec <CTID> -- /bin/bash -c 'ls -la /'
```

---

## Template testing checklist

- Template readiness
  - [ ] VM/LXC exists and marked as `template`.
  - [ ] Template is present on the node you will clone/create on.
  - [ ] Template supports cloud-init (for QEMU) or has required user tools (for LXC).
  - [ ] QEMU Guest Agent installed & enabled (for QEMU flows).

- Network & bootstrap
  - [ ] New instance can reach bootstrap URLs (runner downloads).
  - [ ] If using callback tokens, ensure temporary network/metadata access is allowed.
  - [ ] If relying on SSH fallback: Proxmox host is reachable via SSH and key configured.

- Provider behavior
  - [ ] `test-connection` returns Proxmox version successfully.
  - [ ] `list-templates` shows the expected template.
  - [ ] `create-instance` returns an Instance JSON with `provider_id` (VMID/CTID).
  - [ ] `get-instance` shows discovered IP(s) when the agent or cloud-init registers networking.

---

## Troubleshooting common errors

- Unknown template or clone failure:
  - Verify `template_vmid` and node/storage. Cloning may fail if the template is on a different storage type incompatible with the chosen target.
- Cloud-init not executing:
  - Ensure the image supports cloud-init and that cloud-init userdata is injected correctly.
- QGA timeouts:
  - Confirm guest agent package, enablement, kernel support, and that Proxmox agent is allowed in VM config.
- Network isolation prevents bootstrap downloads:
  - Test that a newly spawned VM/container can reach the bootstrap URL (you may need to create a debug image with a simple run script that writes a marker file).

---

## Next steps & recommended experiments

- Create a small disposable template that writes a known file to disk on first boot (fast verification of cloud-init).
- Run `create-instance` and immediately `get-instance` to validate the provider's IP discovery pipeline.
- Test `remove-all-instances` on a test controller ID to validate cleanup semantics.

For detailed setup steps, template validation commands, and architecture diagrams see the linked pages in the Table of Contents above.
