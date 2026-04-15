"""Proxmox setup and configuration utilities."""

from __future__ import annotations

import sys
import urllib.parse

import click
import urllib3
from proxmoxer import ProxmoxAPI
from proxmoxer.core import ResourceException

from .config import ConfigError, load_config


def lint_config(config_path: str) -> None:
    """Load and validate the provider configuration file."""
    try:
        cfg = load_config(config_path)
        click.echo(f"OK: Configuration at '{config_path}' is valid.")

        # Additional logical checks
        click.echo("\n--- Configuration Details ---")
        click.echo(f"Proxmox Host : {cfg.pve.host}")
        click.echo(f"Proxmox User : {cfg.pve.user}")
        click.echo(f"Target Node  : {cfg.cluster.node}")
        click.echo(f"Target Pool  : {cfg.cluster.pool or '(None)'}")
        click.echo(f"Storage      : {cfg.cluster.storage}")

        if cfg.flavors:
            click.echo("Flavors:")
            for flavor_name, flavor in cfg.flavors.items():
                click.echo(
                    f"  - {flavor_name}: {flavor.cores} cores, {flavor.memory_mb} MB RAM, {flavor.disk_gb} GB disk"
                )
        if cfg.images:
            click.echo("Images:")
            for image_name, image in cfg.images.items():
                click.echo(f"  - {image_name}: Type {image.type.upper()}")

    except ConfigError as exc:
        click.echo(f"FAIL: Configuration error: {exc}", err=True)
        sys.exit(1)
    except Exception as exc:
        click.echo(f"FAIL: Unexpected error while loading config: {exc}", err=True)
        sys.exit(1)


def create_garm_environment(
    host: str,
    root_user: str,
    root_password: str,
    verify_ssl: bool,
    garm_user: str = "garm@pve",
    garm_token_name: str = "garm",
    garm_role: str = "GarmAdmin",
    garm_pool: str = "garm",
) -> None:
    """Create Proxmox user, role, permissions, and pool for GARM."""
    if not verify_ssl:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    parsed = (
        urllib.parse.urlparse(host)
        if "://" in host
        else urllib.parse.urlparse(f"https://{host}")
    )
    pve_host = parsed.hostname or host
    pve_port = parsed.port or 8006

    prox = ProxmoxAPI(
        pve_host,
        port=pve_port,
        user=root_user,
        password=root_password,
        verify_ssl=verify_ssl,
    )

    click.echo(f"Connected to Proxmox VE at {pve_host}:{pve_port}")

    # 1. Create Role
    # These are the minimum privileges required to clone templates, configure cloud-init/LXC, and manage power state.
    privileges = (
        "VM.Allocate VM.Audit VM.Clone VM.Config.CDROM VM.Config.Cloudinit "
        "VM.Config.CPU VM.Config.Disk VM.Config.HWType VM.Config.Memory "
        "VM.Config.Network VM.Config.Options VM.Console VM.Migrate "
        "VM.PowerMgmt Pool.Allocate Datastore.AllocateSpace Datastore.Audit "
        "SDN.Use"
    )
    try:
        prox.access.roles.post(roleid=garm_role, privs=privileges)
        click.echo(f"[*] Created role '{garm_role}'")
    except ResourceException as e:
        if "already exists" in str(e) or e.status_code == 400:
            click.echo(f"[*] Role '{garm_role}' already exists. Updating privileges...")
            prox.access.roles(garm_role).put(privs=privileges)
        else:
            click.echo(f"[!] Failed to create role '{garm_role}': {e}", err=True)
            sys.exit(1)

    # 2. Create User
    try:
        prox.access.users.post(userid=garm_user, comment="GARM Provider User")
        click.echo(f"[*] Created user '{garm_user}'")
    except ResourceException as e:
        if "already exists" in str(e) or e.status_code == 400:
            click.echo(f"[*] User '{garm_user}' already exists.")
        else:
            click.echo(f"[!] Failed to create user '{garm_user}': {e}", err=True)
            sys.exit(1)

    # 3. Create Pool
    try:
        prox.pools.post(poolid=garm_pool, comment="GARM Resource Pool")
        click.echo(f"[*] Created pool '{garm_pool}'")
    except ResourceException as e:
        if "already exists" in str(e) or e.status_code == 400:
            click.echo(f"[*] Pool '{garm_pool}' already exists.")
        else:
            click.echo(f"[!] Failed to create pool '{garm_pool}': {e}", err=True)

    # 4. Assign Permissions
    try:
        # Pool access
        prox.access.acl.put(path=f"/pool/{garm_pool}", roles=garm_role, users=garm_user)
        # Global storage access (needed for cloud-init snippets and allocating disks)
        prox.access.acl.put(path="/storage", roles=garm_role, users=garm_user)
        # Global VM access (needed to read/clone from templates outside the pool)
        prox.access.acl.put(path="/vms", roles=garm_role, users=garm_user)
        # Global SDN access (needed if using SDN zones)
        prox.access.acl.put(path="/sdn/zones", roles=garm_role, users=garm_user)
        click.echo(
            f"[*] Assigned ACL permissions for '{garm_user}' with role '{garm_role}'."
        )
    except Exception as e:
        click.echo(f"[!] Failed to assign permissions: {e}", err=True)

    # 5. Create Token
    try:
        # privsep=0 means the token inherits all permissions from the user
        response = prox.access.users(garm_user).token(garm_token_name).post(privsep=0)
        token_value = response.get("value") if response else None

        click.echo("\n" + "=" * 60)
        click.echo("SUCCESS: GARM Proxmox environment configured.")
        click.echo("=" * 60)
        click.echo("Please add the following to your GARM provider config.toml:\n")
        click.echo("[pve]")
        click.echo(f'host = "{pve_host}:{pve_port}"')
        click.echo(f'user = "{garm_user}"')
        click.echo(f'token_name = "{garm_token_name}"')
        click.echo(f'token_value = "{token_value}"')
        click.echo(f"verify_ssl = {'true' if verify_ssl else 'false'}")
        click.echo("\n[cluster]")
        click.echo('node = "YOUR_NODE_NAME"')
        click.echo(f'pool = "{garm_pool}"')
        click.echo("=" * 60 + "\n")

    except ResourceException as e:
        if "already exists" in str(e) or e.status_code == 400:
            click.echo(
                f"\n[!] Token '{garm_token_name}' for user '{garm_user}' already exists.\n"
                "Proxmox API only reveals the token value upon initial creation.\n"
                "You must delete the existing token and re-run this command, or manually provide the known token.",
                err=True,
            )
        else:
            click.echo(f"[!] Failed to create token '{garm_token_name}': {e}", err=True)
