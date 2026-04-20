"""Click-based CLI entrypoint and GARM_COMMAND dispatcher."""

from __future__ import annotations

import logging
import os
import sys

import click

from . import commands

LEGACY_COMMAND_MAP = {
    "CreateInstance": "create-instance",
    "DeleteInstance": "delete-instance",
    "GetInstance": "get-instance",
    "ListInstances": "list-instances",
    "RemoveAllInstances": "remove-all-instances",
    "Start": "start",
    "Stop": "stop",
}


def _setup_logging() -> None:
    """Configure root logger from environment variables.

    Supported env vars:
      GARM_LOG_LEVEL  — e.g. DEBUG / INFO / WARNING / ERROR (takes priority)
      GARM_DEBUG      — legacy flag; if set (and GARM_LOG_LEVEL absent) maps to DEBUG
      GARM_LOG_FILE   — optional full path; enables a rotating file handler (10 MB × 5)
      GARM_LOG_JSON   — optional boolean (1/true/yes); tries pythonjsonlogger, falls back
    """
    from logging.handlers import RotatingFileHandler

    # Determine log level
    level_name = os.environ.get("GARM_LOG_LEVEL")
    if not level_name:
        level = logging.DEBUG if os.environ.get("GARM_DEBUG") else logging.WARNING
    else:
        level = getattr(logging, level_name.upper(), logging.INFO)

    # Build formatter (JSON optional)
    use_json = os.environ.get("GARM_LOG_JSON", "").lower() in ("1", "true", "yes")
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    if use_json:
        try:
            from pythonjsonlogger import jsonlogger  # type: ignore[import]

            formatter: logging.Formatter = jsonlogger.JsonFormatter(fmt)
        except Exception:
            formatter = logging.Formatter(fmt)
    else:
        formatter = logging.Formatter(fmt)

    root = logging.getLogger()
    if not root.handlers:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(formatter)
        root.addHandler(sh)

        log_file = os.environ.get("GARM_LOG_FILE")
        if log_file:
            try:
                dirname = os.path.dirname(log_file)
                if dirname:
                    os.makedirs(dirname, exist_ok=True)
            except Exception:
                pass
            fh = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=5)
            fh.setFormatter(formatter)
            root.addHandler(fh)

    root.setLevel(level)


@click.group(
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.option(
    "--config",
    envvar="GARM_PROVIDER_CONFIG_FILE",
    default="garm-provider-proxmox.toml",
    help="Path to provider TOML config.",
)
@click.pass_context
def cli(ctx: click.Context, config: str) -> None:
    ctx.ensure_object(dict)
    ctx.obj["config"] = config

    """GARM external provider for Proxmox VE.

    Can be invoked with explicit subcommands or via the GARM_COMMAND
    environment variable for legacy compatibility.
    """
    _setup_logging()

    if ctx.invoked_subcommand is not None:
        return

    garm_cmd = os.environ.get("GARM_COMMAND", "").strip()
    if not garm_cmd:
        click.echo(
            "Error: No subcommand provided and GARM_COMMAND environment variable is not set.\n"
            f"Valid legacy commands: {', '.join(sorted(LEGACY_COMMAND_MAP))}",
            err=True,
        )
        sys.exit(1)

    subcommand_name = LEGACY_COMMAND_MAP.get(garm_cmd)
    if not subcommand_name:
        click.echo(
            f"Error: Unknown GARM_COMMAND {garm_cmd!r}.\n"
            f"Valid legacy commands: {', '.join(sorted(LEGACY_COMMAND_MAP))}",
            err=True,
        )
        sys.exit(1)

    subcommand = cli.commands.get(subcommand_name)
    if subcommand:
        kwargs = {}
        for param in subcommand.params:
            if param.name and getattr(param, "envvar", None):
                envvar = param.envvar
                if isinstance(envvar, (list, tuple)):
                    envvar = envvar[0]
                if envvar and isinstance(envvar, str):
                    val = os.environ.get(envvar)
                    if val is not None:
                        kwargs[param.name] = val
        ctx.invoke(subcommand, **kwargs)


@cli.command(name="create-instance")
@click.pass_context
def create_instance_cmd(ctx: click.Context):
    config = ctx.obj["config"]
    """Create a new runner instance."""
    bootstrap_data = sys.stdin.read()
    if not bootstrap_data.strip():
        click.echo("Error: CreateInstance requires bootstrap JSON on stdin", err=True)
        sys.exit(1)
    commands.create_instance(config, bootstrap_data)


@cli.command(name="delete-instance")
@click.option(
    "--instance-id",
    envvar="GARM_INSTANCE_ID",
    required=True,
    help="Instance ID to delete.",
)
@click.pass_context
def delete_instance_cmd(ctx: click.Context, instance_id: str):
    config = ctx.obj["config"]
    """Delete a runner instance."""
    commands.delete_instance(config, instance_id)


@cli.command(name="get-instance")
@click.option(
    "--instance-id",
    envvar="GARM_INSTANCE_ID",
    required=True,
    help="Instance ID to get.",
)
@click.pass_context
def get_instance_cmd(ctx: click.Context, instance_id: str):
    config = ctx.obj["config"]
    """Get details of a runner instance."""
    commands.get_instance(config, instance_id)


@cli.command(name="list-instances")
@click.option(
    "--pool-id",
    envvar="GARM_POOL_ID",
    required=True,
    help="Pool ID to list instances for.",
)
@click.pass_context
def list_instances_cmd(ctx: click.Context, pool_id: str):
    config = ctx.obj["config"]
    """List all runner instances in a pool."""
    commands.list_instances(config, pool_id)


@cli.command(name="remove-all-instances")
@click.option(
    "--controller-id",
    envvar="GARM_CONTROLLER_ID",
    required=True,
    help="Controller ID to remove instances for.",
)
@click.pass_context
def remove_all_instances_cmd(ctx: click.Context, controller_id: str):
    config = ctx.obj["config"]
    """Remove all runner instances for a controller."""
    commands.remove_all_instances(config, controller_id)


@cli.command(name="start")
@click.option(
    "--instance-id",
    envvar="GARM_INSTANCE_ID",
    required=True,
    help="Instance ID to start.",
)
@click.pass_context
def start_cmd(ctx: click.Context, instance_id: str):
    config = ctx.obj["config"]
    """Start a runner instance."""
    commands.start(config, instance_id)


@cli.command(name="stop")
@click.option(
    "--instance-id",
    envvar="GARM_INSTANCE_ID",
    required=True,
    help="Instance ID to stop.",
)
@click.pass_context
def stop_cmd(ctx: click.Context, instance_id: str):
    config = ctx.obj["config"]
    """Stop a runner instance."""
    commands.stop(config, instance_id)


@cli.command(name="test-connection")
@click.pass_context
def test_connection_cmd(ctx: click.Context):
    config = ctx.obj["config"]
    """Test connection to the Proxmox VE API."""
    from .client import PVEClient
    from .config import load_config

    try:
        cfg = load_config(config)
        client = PVEClient(cfg)
        version = client._prox.version.get() or {}
        click.echo(
            f"Connection successful! Proxmox VE version: {version.get('version')}"
        )
    except Exception as exc:
        click.echo(f"Connection failed: {exc}", err=True)
        sys.exit(1)


@cli.command(name="list-templates")
@click.pass_context
def list_templates_cmd(ctx: click.Context):
    config = ctx.obj["config"]
    """List all available templates (QEMU and LXC)."""
    from .client import PVEClient
    from .config import load_config

    try:
        cfg = load_config(config)
        client = PVEClient(cfg)
        # Proxmox API 'type=vm' returns both qemu and lxc resources
        resources = client._prox.cluster.resources.get(type="vm") or []
        templates = [r for r in resources if str(r.get("template", "0")) == "1"]

        if not templates:
            click.echo("No templates found.")
            return

        click.echo(f"{'VMID':<10} {'TYPE':<10} {'NAME':<30} {'NODE':<20}")
        click.echo("-" * 72)
        for t in sorted(templates, key=lambda x: x.get("vmid", 0)):
            click.echo(
                f"{t.get('vmid', ''):<10} {t.get('type', ''):<10} {t.get('name', ''):<30} {t.get('node', ''):<20}"
            )
    except Exception as exc:
        click.echo(f"Failed to list templates: {exc}", err=True)
        sys.exit(1)


@cli.command(name="lint-config")
@click.pass_context
def lint_config_cmd(ctx: click.Context):
    config = ctx.obj["config"]
    """Validate the provider configuration file."""
    from .setup import lint_config

    lint_config(config)


@cli.command(name="setup-proxmox")
@click.option("--host", required=True, help="Proxmox host URL (e.g. https://pve:8006)")
@click.option("--root-user", required=True, help="Root user (e.g. root@pam)")
@click.option("--root-password", prompt=True, hide_input=True, help="Root password")
@click.option(
    "--verify-ssl/--no-verify-ssl", default=True, help="Verify SSL certificate"
)
@click.option("--garm-user", default="garm@pve", help="User to create for GARM")
@click.option("--garm-token-name", default="garm", help="Token name to create")
@click.option("--garm-role", default="GarmAdmin", help="Role to create")
@click.option("--garm-pool", default="garm", help="Pool to create")
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
