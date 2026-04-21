"""Click-based CLI entrypoint and GARM_COMMAND dispatcher.

GARM communicates with the provider by setting environment variables and
calling this binary.  The ``GARM_COMMAND`` variable selects the operation;
additional variables carry operation-specific data.  The GARM dispatch path
is the *primary* use-case.

Use ``make_cli(provider_type)`` to obtain a fully-wired Click application for
either the QEMU-VM provider or the LXC provider.  Module-level convenience
objects are provided:

  ``cli``       — VM provider (backward-compatible alias)
  ``cli_vm``    — QEMU VM provider
  ``cli_lxc``   — LXC container provider

Recommended console scripts:
  garm-proxmox-vm-provider  → garm_proxmox_provider.cli:cli_vm
  garm-proxmox-lxc-provider → garm_proxmox_provider.cli:cli_lxc
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

import click

from . import commands

_LOG_FILE_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_LOG_FILE_BACKUP_COUNT = 5

GARM_COMMAND_MAP = {
    "CreateInstance": "create-instance",
    "DeleteInstance": "delete-instance",
    "GetInstance": "get-instance",
    "ListInstances": "list-instances",
    "RemoveAllInstances": "remove-all-instances",
    "Start": "start",
    "Stop": "stop",
}


def _setup_logging(config_path: str | None = None) -> None:
    """Configure the root logger.

    Preference order:
    1. ``GARM_LOG_LEVEL`` / ``GARM_LOG_FILE`` environment variables
    2. ``[logging]`` section in the TOML provider config
    3. Built-in defaults (WARNING level, stderr only)
    """
    # Load optional TOML logging config (best-effort; never raises).
    logging_cfg = None
    try:
        from .config import load_logging_from_toml

        toml_path = config_path or os.environ.get("GARM_PROVIDER_CONFIG_FILE")
        if toml_path:
            logging_cfg = load_logging_from_toml(toml_path)
    except Exception:
        pass

    # Determine log level.
    level_name = os.environ.get("GARM_LOG_LEVEL") or (
        logging_cfg.level if logging_cfg and logging_cfg.level else None
    )
    if level_name:
        level = getattr(logging, level_name.upper(), logging.INFO)
    elif os.environ.get("GARM_DEBUG"):
        level = logging.DEBUG
    else:
        level = logging.WARNING

    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    formatter = logging.Formatter(fmt)

    root = logging.getLogger()
    # Add a stderr handler only if none exists yet.
    if not any(
        isinstance(h, logging.StreamHandler)
        and getattr(h, "stream", None) is sys.stderr
        for h in root.handlers
    ):
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(formatter)
        root.addHandler(sh)

    # Optional rotating file handler.
    log_file = os.environ.get("GARM_LOG_FILE") or (
        logging_cfg.file if logging_cfg and logging_cfg.file else None
    )
    if log_file:
        try:
            parent = os.path.dirname(log_file)
            if parent:
                os.makedirs(parent, exist_ok=True)
            abs_path = os.path.abspath(log_file)
            if not any(
                getattr(h, "baseFilename", None)
                and os.path.abspath(h.baseFilename) == abs_path  # type: ignore[union-attr]
                for h in root.handlers
            ):
                fh = RotatingFileHandler(
                    log_file,
                    maxBytes=_LOG_FILE_MAX_BYTES,
                    backupCount=_LOG_FILE_BACKUP_COUNT,
                )
                fh.setFormatter(formatter)
                root.addHandler(fh)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Failed to set up log file %s: %s", log_file, exc
            )

    root.setLevel(level)


# ---------------------------------------------------------------------------
# CLI factory
# ---------------------------------------------------------------------------


def make_cli(provider_type: str) -> click.Group:
    """Return a fully-wired Click application for *provider_type* ("vm" or "lxc").

    Each call returns an independent Click group so that both
    ``garm-proxmox-vm-provider`` and ``garm-proxmox-lxc-provider`` can be
    registered as separate console scripts.
    """
    type_label = provider_type.upper()

    @click.group(
        invoke_without_command=True,
        context_settings={"help_option_names": ["-h", "--help"]},
    )
    @click.option(
        "--config",
        envvar="GARM_PROVIDER_CONFIG_FILE",
        default="garm-provider-proxmox.toml",
        show_default=True,
        help="Path to provider TOML config.",
    )
    @click.pass_context
    def _cli(ctx: click.Context, config: str) -> None:
        f"""GARM external provider for Proxmox VE ({type_label}).

        Manages {type_label} instances on a Proxmox VE cluster.
        Invoked directly by GARM via the GARM_COMMAND environment variable, or
        with explicit sub-commands for local management and debugging.
        """
        ctx.ensure_object(dict)
        ctx.obj["config"] = config
        ctx.obj["provider_type"] = provider_type
        _setup_logging(config)

        if ctx.invoked_subcommand is not None:
            return

        # GARM dispatch via environment variable.
        garm_cmd = os.environ.get("GARM_COMMAND", "").strip()
        if not garm_cmd:
            click.echo(
                "Error: No subcommand provided and GARM_COMMAND is not set.\n"
                f"Valid GARM commands: {', '.join(sorted(GARM_COMMAND_MAP))}",
                err=True,
            )
            sys.exit(1)

        subcommand_name = GARM_COMMAND_MAP.get(garm_cmd)
        if not subcommand_name:
            click.echo(
                f"Error: Unknown GARM_COMMAND {garm_cmd!r}.\n"
                f"Valid GARM commands: {', '.join(sorted(GARM_COMMAND_MAP))}",
                err=True,
            )
            sys.exit(1)

        subcommand = _cli.commands.get(subcommand_name)
        if subcommand:
            kwargs: dict[str, str] = {}
            for param in subcommand.params:
                envvar = getattr(param, "envvar", None)
                if param.name and envvar:
                    ev = envvar[0] if isinstance(envvar, (list, tuple)) else envvar
                    if ev and isinstance(ev, str):
                        val = os.environ.get(ev)
                        if val is not None:
                            kwargs[param.name] = val
            ctx.invoke(subcommand, **kwargs)

    # -----------------------------------------------------------------------
    # GARM lifecycle commands
    # -----------------------------------------------------------------------

    @_cli.command(name="create-instance")
    @click.pass_context
    def create_instance_cmd(ctx: click.Context) -> None:
        """Create a new runner instance (reads bootstrap JSON from stdin)."""
        config = ctx.obj["config"]
        ptype = ctx.obj["provider_type"]
        bootstrap_data = sys.stdin.read()
        if not bootstrap_data.strip():
            click.echo("Error: CreateInstance requires bootstrap JSON on stdin", err=True)
            sys.exit(1)
        commands.create_instance(config, bootstrap_data, provider_type=ptype)

    @_cli.command(name="delete-instance")
    @click.option(
        "--instance-id",
        envvar="GARM_INSTANCE_ID",
        required=True,
        help="Instance ID to delete.",
    )
    @click.pass_context
    def delete_instance_cmd(ctx: click.Context, instance_id: str) -> None:
        """Delete a runner instance."""
        commands.delete_instance(ctx.obj["config"], instance_id)

    @_cli.command(name="get-instance")
    @click.option(
        "--instance-id",
        envvar="GARM_INSTANCE_ID",
        required=True,
        help="Instance ID to retrieve.",
    )
    @click.pass_context
    def get_instance_cmd(ctx: click.Context, instance_id: str) -> None:
        """Get details of a runner instance."""
        commands.get_instance(ctx.obj["config"], instance_id)

    @_cli.command(name="list-instances")
    @click.option(
        "--pool-id",
        envvar="GARM_POOL_ID",
        required=True,
        help="Pool ID to list instances for.",
    )
    @click.pass_context
    def list_instances_cmd(ctx: click.Context, pool_id: str) -> None:
        """List all runner instances in a pool."""
        commands.list_instances(ctx.obj["config"], pool_id)

    @_cli.command(name="remove-all-instances")
    @click.option(
        "--controller-id",
        envvar="GARM_CONTROLLER_ID",
        required=True,
        help="Controller ID whose instances should be removed.",
    )
    @click.pass_context
    def remove_all_instances_cmd(ctx: click.Context, controller_id: str) -> None:
        """Remove all runner instances for a controller."""
        commands.remove_all_instances(ctx.obj["config"], controller_id)

    @_cli.command(name="start")
    @click.option(
        "--instance-id",
        envvar="GARM_INSTANCE_ID",
        required=True,
        help="Instance ID to start.",
    )
    @click.pass_context
    def start_cmd(ctx: click.Context, instance_id: str) -> None:
        """Start a runner instance."""
        commands.start(ctx.obj["config"], instance_id)

    @_cli.command(name="stop")
    @click.option(
        "--instance-id",
        envvar="GARM_INSTANCE_ID",
        required=True,
        help="Instance ID to stop.",
    )
    @click.pass_context
    def stop_cmd(ctx: click.Context, instance_id: str) -> None:
        """Stop a runner instance."""
        commands.stop(ctx.obj["config"], instance_id)

    # -----------------------------------------------------------------------
    # "debug" sub-group — local inspection and diagnostics
    # -----------------------------------------------------------------------

    @_cli.group(name="debug")
    @click.pass_context
    def debug_group(ctx: click.Context) -> None:
        """Local debugging and inspection utilities."""

    @debug_group.command(name="test-connection")
    @click.pass_context
    def test_connection_cmd(ctx: click.Context) -> None:
        """Test connection to the Proxmox VE API."""
        from .client import PVEClient
        from .config import load_config

        try:
            cfg = load_config(ctx.obj["config"])
            client = PVEClient(cfg)
            version = client._prox.version.get() or {}
            click.echo(
                f"Connection successful! Proxmox VE version: {version.get('version')}"
            )
        except Exception as exc:
            click.echo(f"Connection failed: {exc}", err=True)
            sys.exit(1)

    @debug_group.command(name="list-templates")
    @click.pass_context
    def list_templates_cmd(ctx: click.Context) -> None:
        """List available templates matching this provider's type on the cluster."""
        from .client import PVEClient
        from .config import load_config

        res_type = "lxc" if provider_type == "lxc" else "qemu"
        try:
            cfg = load_config(ctx.obj["config"])
            client = PVEClient(cfg)
            resources = client._prox.cluster.resources.get(type="vm") or []
            templates = [
                r
                for r in resources
                if str(r.get("template", "0")) == "1" and r.get("type") == res_type
            ]

            if not templates:
                click.echo(f"No {res_type.upper()} templates found.")
                return

            click.echo(f"{'VMID':<10} {'TYPE':<10} {'NAME':<30} {'NODE':<20}")
            click.echo("-" * 72)
            for t in sorted(templates, key=lambda x: x.get("vmid", 0)):
                click.echo(
                    f"{t.get('vmid', ''):<10} {t.get('type', ''):<10}"
                    f" {t.get('name', ''):<30} {t.get('node', ''):<20}"
                )
        except Exception as exc:
            click.echo(f"Failed to list templates: {exc}", err=True)
            sys.exit(1)

    @debug_group.command(name="lint-config")
    @click.pass_context
    def lint_config_cmd(ctx: click.Context) -> None:
        """Validate the provider configuration file."""
        from .setup import lint_config

        lint_config(ctx.obj["config"])

    # -----------------------------------------------------------------------
    # "admin" sub-group — Proxmox cluster provisioning
    # -----------------------------------------------------------------------

    @_cli.group(name="admin")
    @click.pass_context
    def admin_group(ctx: click.Context) -> None:
        """Proxmox cluster administration utilities."""

    @admin_group.command(name="setup-proxmox")
    @click.option("--host", required=True, help="Proxmox host URL (e.g. https://pve:8006)")
    @click.option("--root-user", required=True, help="Root user (e.g. root@pam)")
    @click.option("--root-password", prompt=True, hide_input=True, help="Root password")
    @click.option(
        "--verify-ssl/--no-verify-ssl", default=True, help="Verify SSL certificate"
    )
    @click.option("--garm-user", default="garm@pve", help="User to create for GARM")
    @click.option("--garm-token-name", default="garm", help="Token name to create")
    @click.option("--garm-role", default="GarmAdmin", help="Role to create")
    @click.option("--garm-pool", default="garm", help="Resource pool to create")
    def setup_proxmox_cmd(
        host: str,
        root_user: str,
        root_password: str,
        verify_ssl: bool,
        garm_user: str,
        garm_token_name: str,
        garm_role: str,
        garm_pool: str,
    ) -> None:
        """Create Proxmox user, role, permissions, and pool for GARM."""
        from .setup import create_garm_environment

        create_garm_environment(
            host=host,
            root_user=root_user,
            root_password=root_password,
            verify_ssl=verify_ssl,
            garm_user=garm_user,
            garm_token_name=garm_token_name,
            garm_role=garm_role,
            garm_pool=garm_pool,
        )

    return _cli


# ---------------------------------------------------------------------------
# Module-level CLI objects (used as console script entrypoints)
# ---------------------------------------------------------------------------

#: QEMU VM provider — use as ``garm-proxmox-vm-provider``
cli_vm = make_cli("vm")

#: LXC container provider — use as ``garm-proxmox-lxc-provider``
cli_lxc = make_cli("lxc")

#: Backward-compatible alias for the VM provider
cli = cli_vm
