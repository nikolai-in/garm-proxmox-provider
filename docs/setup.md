# Provider setup & template testing

This guide explains how to prepare a Proxmox VE environment so the `garm-proxmox-provider`
can reliably provision ephemeral runners. It focuses on the practical steps you (or
your infra team) should take on Proxmox and within the golden images/templates so
cloud-init and the QEMU Guest Agent behave correctly.

Target audience

- Platform engineers preparing a Proxmox cluster for GARM runners.
- Developers who need to prepare/validate VM/LXC templates for provisioning.
- Testers who want reproducible steps to confirm templates will be usable by the provider.

Sections

- Preconditions and minimum privileges
- Create a Proxmox user, API token and role (principle of least privilege)
- Prepare and validate QEMU VM templates
- Prepare and validate LXC templates
- Quick validation and smoke tests
- Troubleshooting tips

---

## Preconditions and minimum privileges

Before creating user accounts or tokens, ensure:

- You have administrative access to the Proxmox datacenter or a user who can create
  users/roles/pools and set permissions.
- You have a node with the desired storage available for templates and cloud-init disks.
- The Proxmox cluster has networking configured for the expected VM/container networks.

Minimum concepts you need to know

- "Token" here refers to a Proxmox API token (preferred over password-based automation).
- "Role" is a Proxmox permission role (scoped capabilities). We grant a role to the service
  user/token on the datacenter or specific nodes/storage as appropriate.
- "Pool" is a Proxmox resource pool used to tag runner instances for easier filtering.

---

## Create a Proxmox user + API token + role

Recommended approach: create a dedicated service user and give it a minimal role that
allows it to perform VM/LXC lifecycle operations, manage cloud-init metadata, and operate
storage. Then create an API token for that user and use token auth in your provider config.

Conceptual steps (UI or API):

- Create a user such as `garm@pve`.
- Create a role (e.g., `GarmRunnerManager`) with only the capabilities needed for:
  - VM/CT creation, cloning, start/stop, status, migration as required.
  - editing VM config (to add cloud-init drive / metadata).
  - storage access for disk creation and deletion.
  - access to pool and permission assignment if provider automates pools.
- Create a pool `garm` (optional but recommended) for tagging the created instances.
- Assign the role to the user (or token) on a reasonable scope (datacenter, node, or storage),
  minimizing scope to reduce blast radius.
- Create an API token for the service user (token name `garm`, copy token value securely).

What to set in the provider config (example fields referenced by the provider)

```garm-proxmox-provider/docs/setup.md#L1-12
[pve]
host = "https://pve.example.local:8006"
user = "garm@pve"
token_name = "garm"
token_value = "<REDACTED_TOKEN_VALUE>"
verify_ssl = true

[defaults]
pool = "garm"
node = "pve-node1"
storage = "local-lvm"
```

Notes

- Prefer token auth to password if possible; tokens can be scoped and rotated more easily.
- If you must use a user/password for interactive setup, prefer short-lived credentials.
- Keep token values secret and rotate them periodically.

---

## Template preparation: QEMU (recommended for full isolation)

Goal: produce a golden QEMU VM template that supports cloud-init and the QEMU Guest Agent,
so the provider can clone the template, inject cloud-init metadata and reliably run
the bootstrap userdata inside the VM.

Checklist for a QEMU template image

- Install cloud-init inside the guest operating system.
- Install and enable the QEMU Guest Agent service in the guest:
  - Service name typically: `qemu-guest-agent` (systemd unit: `qemu-guest-agent.service`)
- Ensure the cloud-init datasource supports the metadata the provider will inject (NoCloud / ConfigDrive).
- Remove persistent data and SSH host keys before converting to template:
  - Remove old SSH host keys so new ones are generated on first boot, or ensure cloud-init regenerates them.
  - Clean cloud-init state (`cloud-init clean --logs --seed` or appropriate OS-specific command) so the first-boot flow runs correctly.
- Disable cloud-init network persistence that might interfere with Proxmox-provided network config.
- Test the VM boots, cloud-init runs, and QGA responds on a normal instance before converting to template.

Example cloud-init minimum snippet (userdata) you can use in testing:

```garm-proxmox-provider/docs/setup.md#L13-40
#cloud-config
users:
  - name: runner
    gecos: Runner User
    shell: /bin/bash
    sudo: ['ALL=(ALL) NOPASSWD:ALL']
    ssh_authorized_keys:
      - ssh-ed25519 AAAA...example-key

runcmd:
  - [ sh, -c, "echo 'cloud-init completed' > /var/log/cloud-init-complete" ]
```

Template conversion

- Once the VM is prepared and tested, use the Proxmox UI or API to convert the VM to a template.
- Ensure the template is present on the node/storage you intend to clone from (or make it available cluster-wide if needed).

QEMU template validation (pre-provider)

- Boot a non-template clone (manual clone) and verify:
  - cloud-init ran: check `/var/log/cloud-init.log` and `/var/log/cloud-init-output.log`.
  - QEMU Guest Agent is running: `systemctl status qemu-guest-agent` inside the guest.
  - From the Proxmox host, verify the agent responds (if you can, via `qm` or by using the Proxmox API).
- Verify the SSH public key you plan to bootstrap is installed by cloud-init (if applicable).
- Confirm the VM receives correct IP via DHCP and that the QGA reports guest network information if enabled.

QEMU-specific notes for GARM provider

- The provider prefers to use the QEMU Guest Agent to run the bootstrap commands (safer & faster).
- If QGA fails in your environment, you can enable SSH fallback at the cluster/provider config level (see configuration for `qm_ssh_fallback`); this requires that the Proxmox host can SSH into the VM as `qm guest exec` wrapper. Use that only when necessary.

---

## Template preparation: LXC (lightweight, faster startup)

Goal: prepare an LXC template with an appropriate base environment and a reliable mechanism for injecting
and running the bootstrap script (the provider uses exec injection for LXC).

Checklist for an LXC template

- Use a minimal template that provides an unprivileged or privileged container as your ops require.
- Ensure required runtime tools exist (bash, curl/wget, tar, systemd or appropriate init for the distro).
- If your LXC environment supports cloud-init for containers, configure cloud-init or ensure the provider's injection method (exec-based) will work (some images accept cloud-init-like metadata).
- Clean any persistent state before templating (remove SSH host keys, caches, etc.).
- Test exec-injection by starting a container from the template and running a simple command via the Proxmox LXC exec API (or via `pct exec` on the Proxmox host).

LXC template validation (pre-provider)

- Create a test container from the template and:
  - Confirm container starts and network comes up.
  - Use an exec mechanism to run test commands inside the container (e.g., a small script that writes to a temporary file).
  - Ensure the container has all tools needed to bootstrap the runner (e.g., ability to download the runner tarball, create a service, etc.).

LXC-specific notes for GARM provider

- LXC exec injection is different per template and sometimes requires privileged capabilities; test thoroughly.
- Network namespaces and capabilities might differ from QEMU, so ensure the bootstrap flow still completes.

---

## Testing templates with the provider (smoke tests)

Once you have prepared templates and a service token, perform these quick smoke tests.

1) Basic connectivity

- Verify the provider can connect to your Proxmox host using the configured token:

```garm-proxmox-provider/docs/setup.md#L41-48
garm-proxmox-provider test-connection --config /path/to/garm-provider-proxmox.toml
# or, if you use uv (dev env):
uv run garm-proxmox-provider test-connection --config /path/to/garm-provider-proxmox.toml
```

Expected: prints Proxmox VE version or otherwise indicates a successful connection.

1) List available templates (sanity)

- Ensure the template you prepared appears in the template listing the provider uses:

```garm-proxmox-provider/docs/setup.md#L49-56
garm-proxmox-provider list-templates --config /path/to/garm-provider-proxmox.toml
```

Expected: your QEMU/LXC template should be listed (templates are cluster resources with `template=1`).

1) Manual create-from-template test (recommended before full automation)

- Clone the template manually via the Proxmox UI or API and power it on; confirm cloud-init runs (QEMU) or exec-injection works (LXC).
- Confirm the guest has expected network access and login keys.

1) Provider create-instance smoke test (full flow)

- The provider expects the GARM bootstrap payload JSON on stdin for the `CreateInstance` legacy flow or can be driven via its subcommand. Use a minimal known-good bootstrap JSON (your bootstrap/runner system may require certain fields).
- Example invocation (conceptual):

```garm-proxmox-provider/docs/setup.md#L57-64
cat bootstrap.json | garm-proxmox-provider create-instance --config /path/to/garm-provider-proxmox.toml
# or legacy dispatch:
cat bootstrap.json | GARM_COMMAND=CreateInstance garm-proxmox-provider --config /path/to/garm-provider-proxmox.toml
```

Expected: The provider clones/creates the instance, runs the bootstrap, and returns Instance JSON to stdout. Inspect logs (stdout/stderr) and the guest console if bootstrap fails.

1) Verify bootstrap inside guest

- QEMU: check `/var/log/cloud-init.log`, confirm `qemu-guest-agent` is active, and that the expected artifacts (files, services) created by the bootstrap exist.
- LXC: check the injected bootstrap script output (where your script logs) and confirm runner installed and/or services enabled.

---

## Tabbed quick-reference: QEMU vs LXC template prep

```{tab} QEMU template (preferred)
- Install `cloud-init` in the guest OS.
- Install and enable `qemu-guest-agent` (systemd: `qemu-guest-agent.service`).
- Clean cloud-init state and SSH host keys before templating (so each clone initialises cleanly).
- Ensure the template is convertible to a Proxmox template and stored on the expected node/storage.
- Test: clone the template, start the VM, check cloud-init logs and qga status in the guest.
```

### LXC template

- Use a minimal container template appropriate for your workloads (Debian, Alpine, CentOS, etc.).
- Ensure required runtime tools are present in the template (shell, `tar`, `curl`/`wget`, etc.) so the bootstrap script can run.
- Clean persistent state before templating (remove SSH host keys, caches, and other machine-specific artifacts).
- Test the template by creating a test container and running an injected command (for example via `pct exec`) to verify exec-based bootstrap works as expected.

---

## Troubleshooting common failures

- "Template not found" in provider logs:
  - Confirm the template VMID/resource exists and is marked as a template on the node the provider expects.
  - Confirm the provider's `defaults.node` and `defaults.storage` are correct for where the template resides.

- Cloud-init does not run on clones:
  - Ensure `cloud-init` is installed and the template has been cleaned (`cloud-init clean --logs` or distro-specific).
  - Confirm the cloud-init datasource is compatible (NoCloud vs ConfigDrive) and Proxmox will provide metadata correctly.

- QEMU Guest Agent commands fail:
  - Confirm `qemu-guest-agent` is installed and running inside the guest.
  - Confirm the Proxmox template/VM has the QGA channel enabled (`agent` option in the VM config).
  - If QGA is flaky in your environment, test or consider `qm_ssh_fallback` options after evaluating security implications.

- Bootstrap succeeds but runner not connecting:
  - Inspect the runner service logs inside the guest.
  - Confirm network access to the GitHub/Gitea runner registration endpoint and that tokens/labels are correct.
  - Ensure the cloud-init user-data included service creation enabling the runner at boot.

---

## Operational recommendations

- Automated testing: keep a small "canary" pool and a lightweight smoke-test harness that:
  - Clones a template, boots it, validates runner registration or basic connectivity, then destroys the instance.
- Template lifecycle: periodically rebuild templates with updated OS packages and runner binary versions.
- Secrets: do not commit token values; use a secrets manager or environment-based retrieval in production.
- Permissions: minimize token scope and use separate tokens for CI and production controllers where appropriate.
- Monitoring: configure logs and alerts for repeated provisioning failures (e.g., many bootstrap failures in a short window).

---

If you want, next steps I can do for you:

- Provide concrete role capability lists and sample API/CLI commands for token creation and permission assignment (I can include exact commands for the Proxmox CLI/API once you confirm the Proxmox edition and preferred method — Web UI, `pveum`/`pvesh`, or direct API).
- Produce a small, tested cloud-init userdata template that includes the GARM runner bootstrap script for your chosen OS family (Ubuntu, Debian, Fedora, etc.).
- Add a minimal smoke-test script you can run in CI that:
  - Uses the prepared token to create a temporary instance, verifies it bootstraps, then destroys it.

If you'd like any of those, tell me which OS images you plan to use for templates (e.g., Ubuntu 22.04 cloud image, Debian 12, CentOS Stream, or an LXC variant) and whether you prefer Web UI or CLI/API examples for user/token/role creation.
