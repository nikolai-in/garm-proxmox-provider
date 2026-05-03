```{toctree}
:caption: Contents:
:maxdepth: 2

setup.md
configuration.md
cli.md
logging.md
```

# garm-proxmox-provider

## Overview

`garm-proxmox-provider` is an external GARM provider for Proxmox VE. It supports creating runners as either:

- QEMU virtual machines
- LXC containers

If you just want to get started, read [setup.md](setup.md) and [configuration.md](configuration.md), then use [cli.md](cli.md) for commands.

---

## Quick checklist

- [ ] Create a Proxmox API token with permission to manage the target node and storage.
- [ ] Prepare a Proxmox template or container image and mark it as a template.
- [ ] Put the provider config in TOML and set `GARM_PROVIDER_CONFIG_FILE` or `--config`.
- [ ] Make sure the bootstrap `image` value matches the Proxmox template name.

---

## Minimal provider config

Use the example below as a starting point.

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

---

## Run It

Direct subcommand usage is recommended:

```bash
garm-proxmox-provider --config ./garm-provider-proxmox.toml --help
```

Legacy GARM dispatch is also supported:

```bash
GARM_COMMAND=ListInstances GARM_PROVIDER_CONFIG_FILE=./garm-provider-proxmox.toml \
  garm-proxmox-provider
```

For `CreateInstance`, GARM pipes bootstrap JSON to stdin and the provider returns instance JSON on stdout.

---

## What to read next

- [setup.md](setup.md) for the shortest Proxmox preparation checklist.
- [configuration.md](configuration.md) for the exact TOML schema.
- [cli.md](cli.md) for command usage and `GARM_COMMAND` mapping.

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
