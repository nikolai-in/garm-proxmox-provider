# Testing Templates: QGA, cloud-init and cloning (guide)

This page explains how to prepare and verify Proxmox templates and test the most important behaviors for the provider:

- QEMU Guest Agent (QGA) availability and execution
- cloud-init userdata application and SSH key injection
- cloning behavior and cloud-init metadata on the cloned VM

Use these steps when building or validating golden VM templates you plan to clone from (recommended for QEMU-based runners). The document includes practical host-level and in-guest checks. Where appropriate there are OS-specific checks in tabs.

Prerequisites

- Access to the Proxmox host (SSH or web UI) with permissions to manage VMs (or a user with equivalent API token).
- A candidate VM (the golden image) prepared for templating:
  - For Linux guests: `cloud-init` installed and configured; QEMU Guest Agent installed and enabled (`qemu-guest-agent` package).
  - For Windows guests: use Cloudbase-Init / the QEMU GA Windows service; ensure the image supports cloud-init / cloudbase-init.
- The Proxmox `qm` CLI (or API) available on the Proxmox host you will operate from.

Section 1 — Prepare a golden template (Linux)

1. Boot a clean VM in Proxmox and configure the guest:
   - Install updates, `cloud-init`, and QEMU Guest Agent.
   - Remove persistent udev rules or hostname-specific configuration so each clone gets a unique identity on first boot.
   - Ensure your cloud-init configuration will use SSH key(s) from metadata (do not hardcode secrets).

2. Inside the Linux guest confirm packages and services:

```/dev/null/testing_templates.md#L1-10
# On the guest (Linux)
sudo apt update
sudo apt install -y cloud-init qemu-guest-agent
sudo systemctl enable --now qemu-guest-agent
sudo systemctl enable --now cloud-init
```

1. Clean the image before templating (zero out logs, remove SSH host keys, remove persistent metadata):

```/dev/null/testing_templates.md#L11-24
# On the guest (Linux) as root before shutting down for template creation
sudo cloud-init clean --logs
sudo rm -f /var/lib/cloud/instances/* /var/lib/cloud/instance/* || true
sudo rm -f /etc/ssh/ssh_host_* || true
sudo truncate -s 0 /var/log/* || true
```

1. On the Proxmox host convert the VM to a template:

```/dev/null/testing_templates.md#L25-30
# On the Proxmox host
qm shutdown <TEMPLATE_VMID>       # ensure VM is stopped
qm template <TEMPLATE_VMID>       # make template
```

Section 2 — Clone template and set cloud-init metadata
Clone the template to a new VMID, configure cloud-init options and start it. Verify that the clone gets a cloud-init drive and that metadata can be applied.

1. Clone the template (select `--full` if you want a full clone rather than linked):

```/dev/null/testing_templates.md#L31-40
# On the Proxmox host
qm clone <TEMPLATE_VMID> <NEW_VMID> --name runner-clone-1 --node <NODE> --full 1
```

1. Set cloud-init options on the cloned VM:

```/dev/null/testing_templates.md#L41-48
# On the Proxmox host
qm set <NEW_VMID> --ciuser runner --cipassword 'changeme' --sshkey "$(cat ~/.ssh/id_rsa.pub)" --ipconfig0 ip=dhcp
```

1. Inspect the VM config to confirm cloud-init drive and options:

```/dev/null/testing_templates.md#L49-54
# On the Proxmox host
qm config <NEW_VMID>
# Look for cloud-init drive (ide2: ... or cloudinit: ...)
```

Section 3 — Validate cloud-init execution and SSH key injection
Start the cloned VM and verify cloud-init ran and the SSH key is present.

1. Start the cloned VM:

```/dev/null/testing_templates.md#L55-58
# On the Proxmox host
qm start <NEW_VMID>
```

1. Wait for the VM to boot and for cloud-init to finish. Then check inside the guest.

Tabbed guest checks (Linux / Windows)

- Use the tabs to pick the commands appropriate to the guest OS.

### Linux

Linux (inside guest — as root or using sudo)

Check cloud-init status

```console
cloud-init status --wait
cloud-init status --long
```

Check cloud-init logs

```console
sudo journalctl -u cloud-init --no-pager --since "5 minutes ago"
sudo tail -n +1 /var/log/cloud-init.log /var/log/cloud-init-output.log
```

# Confirm SSH key presence for 'runner' user (example)

sudo cat /home/runner/.ssh/authorized_keys

# Confirm cloud-init metadata applied (hostname, users, etc.)

### Windows

Windows (PowerShell inside guest)

Check Cloudbase-Init (Windows cloud-init equivalent):

```powershell
# Confirm service status
Get-Service -Name CloudbaseInit -ErrorAction SilentlyContinue

# Cloudbase-init logs (common locations)
Get-Content 'C:\Program Files\Cloudbase Solutions\Cloudbase-Init\log\cloudbase-init.log' -Tail 200

# Verify user / public key presence (example depends on how cloudbase-init configured)

3. If cloud-init did not run or did not set the SSH key:
   - Confirm `qm config <vmid>` shows the cloud-init drive and `sshkey` setting.
   - Reboot and watch the cloud-init logs.
   - Check that cloud-init package is installed and not disabled.

Section 4 — Verify QEMU Guest Agent (QGA) availability and exec
QGA is used by the provider to run commands inside the VM (for bootstrap). Validate the guest agent is responsive from the Proxmox host.

1. Quick in-guest check (Linux):
```/dev/null/testing_templates.md#L99-106
# Inside Linux guest
systemctl status qemu-guest-agent
sudo ss -lptn | grep qemu-guest-agent || true
```

1. Quick in-guest check (Windows - PowerShell):

```/dev/null/testing_templates.md#L107-114
# In PowerShell on Windows
Get-Service -Name "QEMU Guest Agent" -ErrorAction SilentlyContinue
# Or check task manager / services.msc for qemu-ga
```

1. From the Proxmox host attempt a QGA exec (this verifies the Proxmox host can reach and instruct the guest via QGA):

```/dev/null/testing_templates.md#L115-124
# On the Proxmox host — run a simple command inside the VM via guest agent
qm guest exec <NEW_VMID> -- /bin/echo hello-from-qga
# Example verifying command returned (monitor/cli will show exit/pid info)
```

Notes:

- `qm guest exec` requires the guest agent to be present and the VM must be running. If `qm guest exec` fails but the agent service is running inside the guest, check firewall rules or QGA logs in the guest.
- If `qm guest exec` fails consistently, you can test QGA availability via API:
  - `pvesh get /nodes/<node>/qemu/<vmid>/agent/ping` (returns agent response when available).
  - Your provider uses the API `agent.ping` to detect QGA readiness; mirror this during manual testing.

Section 5 — QGA fallback strategy (SSH host-based exec)
If your cluster config enables `qm_ssh_fallback` (see provider config), the provider will attempt to execute bootstrap via SSH on the Proxmox host:

Example fallback command (host executes `qm guest exec` on behalf of the guest):

```/dev/null/testing_templates.md#L125-132
# On the Proxmox host (example)
ssh -i /root/.ssh/id_rsa root@pve.example.com qm guest exec <vmid> -- /bin/bash -lc 'echo hello-from-host-fallback'
```

Ensure:

- The SSH identity is accepted by the Proxmox host.
- The SSH user has permission to run `qm` and access the cluster.

Section 6 — Validate cloning semantics (unique MACs, cloud-init metadata per clone)

- After cloning, confirm the new VM has unique network identifiers and updated metadata.

```/dev/null/testing_templates.md#L133-142
# On Proxmox host, verify the cloned VM NICs and Cloud-Init settings
qm config <NEW_VMID> | egrep 'net|ipconfig|sshkey|ciuser|ci|ide2'
# Compare MAC address vs template: ensure NIC MAC differs
```

- On the guest confirm a new SSH host key (Linux) or SID (Windows) was generated.

Section 7 — Test sequence examples (end-to-end)
A concise test flow to validate a template is ready for production runner clones:

1. Prepare golden VM (install cloud-init, qemu-guest-agent), clean with `cloud-init clean`, convert to template.
2. Clone the template to `NEW_VMID` and set the desired cloud-init SSH key (`qm set ... --sshkey "..."`).
3. Start the cloned VM: `qm start <NEW_VMID>`.
4. From the host attempt `qm guest exec <NEW_VMID> -- /bin/echo hello` — success indicates QGA is responding.
5. SSH into the cloned VM using the injected key. Verify user created and runner bootstrap token or metadata present.
6. Check cloud-init logs to confirm scripts ran successfully.
7. Stop and delete the cloned VM and repeat with different cloud-init options to validate idempotency and per-pool overrides.

Section 8 — Troubleshooting checklist

- If `cloud-init` never runs:
  - Confirm cloud-init package/service exists and is enabled in the template.
  - Confirm the cloud-init drive is present in `qm config` for the VM.
  - Inspect `cloud-init` logs in the guest.

- If `qm guest exec` returns an error:
  - Confirm QGA service is running in the guest.
  - Confirm the VM is using a compatible VirtIO/QEMU guest agent version.
  - Check Proxmox host logs (`/var/log/syslog` or `journalctl` on node) for agent errors.

- If SSH key not present:
  - Confirm `qm set <vmid> --sshkey "..."` was applied before starting the VM.
  - Check `qm config <vmid>` for `sshkey` line.
  - Check cloud-init logs for user creation errors.

- If clones retain template-specific persistent data:
  - Ensure you ran `cloud-init clean` and removed machine-specific files (SSH hosts, persistent network rules) in the golden image prior to templating.

Appendix — Quick reference commands (host)

```/dev/null/testing_templates.md#L143-170
# List VMs with template flag
pvesh get /cluster/resources --type vm | jq '.[] | select(.template==1) | {vmid:.vmid, name:.name, node:.node}'

# Show a VM config
qm config <vmid>

# Clone a template (full clone)
qm clone <template_vmid> <new_vmid> --name <name> --node <node> --full 1

# Set cloud-init options
qm set <new_vmid> --ciuser runner --sshkey "$(cat ~/.ssh/id_rsa.pub)" --ipconfig0 ip=dhcp

# Start VM
qm start <new_vmid>

# Exec inside VM via QGA
qm guest exec <new_vmid> -- /bin/echo "hello"
```

Final notes

- For best results, automate the above tests in a small CI job or local script that:
  - Creates a clone, sets cloud-init metadata, starts it, waits for QGA via repeated `agent.ping` or `qm guest exec`, verifies SSH access, then cleans up.
- Keep templates minimal and stateless; ensure cloud-init performs all runtime configuration so clones are reproducible.
- If you want, I can provide a small test script (bash) that implements the end-to-end flow (clone, set sshkey, start, poll QGA, ssh test, cleanup) — tell me your preferred target environment and I will write that script.

References

- sphinx-inline-tabs usage (for in-doc examples and authoring tabbed OS commands): <https://github.com/pradyunsg/sphinx-inline-tabs/raw/refs/heads/main/docs/usage.md>
- Proxmox CLI: `qm` and `pvesh` manpages / Proxmox VE documentation
