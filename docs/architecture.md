# Architecture

The provider follows a layered design: the CLI handles environment-variable dispatch
and routes each GARM command to a pure-Python handler in `commands.py`. Handlers
use `PVEClient` (a thin proxmoxer wrapper) to interact with the Proxmox VE API.

## Component diagram

```{mermaid}
   graph TB
     CLI["CLI (click)<br>cli.py"] --> Commands["Command handlers<br>commands.py"]
     Commands --> Client["PVEClient<br>client.py"]
     Client --> Proxmox["Proxmox VE<br>(REST API / QGA / qm)"]
     Commands --> CloudInit["cloud_init.py<br>userdata renderer"]
     Commands --> Config["config.py<br>TOML loader"]
```

## Bootstrap execution flow (QEMU)

```{mermaid}
   sequenceDiagram
     participant GARM
     participant CLI
     participant PVEClient
     participant PVE as Proxmox VE
     participant VM

     GARM->>CLI: GARM_COMMAND=CreateInstance (stdin: bootstrap JSON)
     CLI->>PVEClient: create_instance(...)
     PVEClient->>PVE: clone template VMID
     PVEClient->>PVE: configure VM (cores, memory, cloud-init meta)
     PVEClient->>PVE: status.start.post()
     loop Wait for QGA (up to 30 × 2 s)
       PVEClient->>VM: agent.ping.post()
       VM-->>PVEClient: pong (or timeout)
     end
     PVEClient->>VM: agent.exec.post([/bin/bash, -c, userdata])
     VM-->>PVEClient: {"pid": 1234}
     PVEClient-->>CLI: Instance JSON
     CLI-->>GARM: stdout Instance JSON
```

## QGA SSH fallback

When `cluster.qm_ssh_fallback = true` is set in the provider config **and** the
QEMU Guest Agent fails to respond (or `agent.exec` raises), the provider falls back
to executing the bootstrap script through the Proxmox host via SSH:

```sh
ssh [-i identity_file] <qm_ssh_user>@<pve_host> qm guest exec <vmid> -- /bin/bash -c '<userdata>'
```

This requires the Proxmox host to be SSH-accessible with a key that has permission
to run `qm`. See the *Security* section of the README for implications.
