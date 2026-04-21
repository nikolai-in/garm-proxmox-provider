"""Click-based CLI entrypoint and GARM_COMMAND dispatcher."""

from __future__ import annotations

import logging
import os
import sys

import click

from . import commands

# Rotating file handler defaults (overridable via env vars in a future enhancement)
_LOG_FILE_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_LOG_FILE_BACKUP_COUNT = 5

LEGACY_COMMAND_MAP = {
    "CreateInstance": "create-instance",
    "DeleteInstance": "delete-instance",
    "GetInstance": "get-instance",
    "ListInstances": "list-instances",
    "RemoveAllInstances": "remove-all-instances",
    "Start": "start",
    "Stop": "stop",
}


def _setup_logging(config_path: str | None = None) -> None:
    """Configure root logger from environment variables, falling back to TOML config.

    Preference order:
      1. Explicit environment variables (GARM_LOG_LEVEL, GARM_LOG_FILE, GARM_LOG_JSON)
      2. Optional [logging] section in the TOML provider config (if present)
      3. Built-in defaults

    The TOML loader function used is `load_logging_from_toml()` from `config.py`.
    """
    from logging.handlers import RotatingFileHandler

    # Try to load logging defaults from TOML if available
    logging_cfg = None
    # Ensure toml_path is always defined so later diagnostics can safely reference it.
    toml_path = None
    try:
        from .config import load_logging_from_toml

        toml_path = config_path or os.environ.get(
            "GARM_PROVIDER_CONFIG_FILE", "garm-provider-proxmox.toml"
        )
        logging_cfg = load_logging_from_toml(toml_path)
    except Exception:
        logging_cfg = None

    # Determine log level (env var takes precedence over TOML)
    level_name = os.environ.get("GARM_LOG_LEVEL") or (
        logging_cfg.level if logging_cfg and logging_cfg.level else None
    )
    if not level_name:
        level = logging.DEBUG if os.environ.get("GARM_DEBUG") else logging.WARNING
    else:
        level = getattr(logging, level_name.upper(), logging.INFO)

    # Build formatter (JSON optional - env var preferred, then TOML)
    use_json_env = os.environ.get("GARM_LOG_JSON", "").lower() in ("1", "true", "yes")
    use_json = use_json_env or (logging_cfg.json if logging_cfg else False)
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

    # Ensure a StreamHandler to stderr exists (don't rely on root.handlers being empty)
    has_stderr = False
    for h in root.handlers:
        if isinstance(h, logging.StreamHandler):
            # Some StreamHandlers may target stdout; only treat sys.stderr as the provider stream
            if getattr(h, "stream", None) is sys.stderr:
                has_stderr = True
                break

    if not has_stderr:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(formatter)
        root.addHandler(sh)

    # Always add a rotating file handler when requested; prefer env var, then TOML
    log_file = os.environ.get("GARM_LOG_FILE") or (
        logging_cfg.file if logging_cfg and logging_cfg.file else None
    )
    if log_file:
        try:
            dirname = os.path.dirname(log_file)
            if dirname:
                os.makedirs(dirname, exist_ok=True)
        except Exception:
            # Best-effort directory creation; if it fails, continue without raising
            pass

        # Check existing handlers for one already writing to the same file (avoid duplicates)
        abs_log_file = os.path.abspath(log_file)
        existing_file = False
        for h in root.handlers:
            try:
                base = getattr(h, "baseFilename", None)
                if base and os.path.abspath(base) == abs_log_file:
                    existing_file = True
                    break
            except Exception:
                # Some handlers may not have baseFilename; ignore them
                continue

        # Aggressive create: if file is missing, attempt to create it with permissive mode
        # This helps when the controller clears env and the host-mounted file wasn't pre-created.
        try:
            if not os.path.exists(log_file):
                parent = os.path.dirname(log_file) or "."
                try:
                    os.makedirs(parent, exist_ok=True)
                except Exception:
                    pass
                try:
                    # Try to create atomically with desired mode
                    fd = os.open(log_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
                    os.close(fd)
                except FileExistsError:
                    # created by another process in the meantime
                    pass
                except Exception:
                    # Fallback: open and chmod
                    try:
                        with open(log_file, "a"):
                            pass
                        try:
                            os.chmod(log_file, 0o666)
                        except Exception:
                            pass
                    except Exception:
                        # ignore failures; handler creation may still fail later and be logged
                        pass
        except Exception:
            # best-effort only
            pass

        if not existing_file:
            fh = RotatingFileHandler(
                log_file,
                maxBytes=_LOG_FILE_MAX_BYTES,
                backupCount=_LOG_FILE_BACKUP_COUNT,
            )
            fh.setFormatter(formatter)
            root.addHandler(fh)

    root.setLevel(level)

    # --- Startup diagnostics: help debug why file logging may be silent ---
    # This emits debug-level diagnostic messages (to configured handlers) about the
    # logging-related environment and the filesystem state of GARM_LOG_FILE.
    try:
        diag = logging.getLogger("garm_proxmox_provider.startup_diag")
        # Use debug so it respects GARM_LOG_LEVEL and handlers
        env_snapshot = {
            "GARM_LOG_LEVEL": os.environ.get("GARM_LOG_LEVEL"),
            "GARM_DEBUG": os.environ.get("GARM_DEBUG"),
            "GARM_LOG_FILE": os.environ.get("GARM_LOG_FILE")
            or (logging_cfg.file if logging_cfg else None),
            "GARM_LOG_JSON": os.environ.get("GARM_LOG_JSON"),
            "TOML_logging": {
                "level": logging_cfg.level if logging_cfg else None,
                "file": logging_cfg.file if logging_cfg else None,
                "json": logging_cfg.json if logging_cfg else None,
                "debug_dump": logging_cfg.debug_dump if logging_cfg else None,
            },
        }
        diag.debug("Startup logging environment: %s", env_snapshot)

        lf = os.environ.get("GARM_LOG_FILE") or (
            logging_cfg.file if logging_cfg and logging_cfg.file else None
        )

        # Probe file state and writability via the handlers (if any) and direct stat/open
        if lf:
            try:
                st = os.stat(lf)
                mode_oct = st.st_mode & 0o777
                diag.debug(
                    "Log file exists: %s (mode=%03o uid=%s gid=%s size=%d)",
                    lf,
                    mode_oct,
                    getattr(st, "st_uid", None),
                    getattr(st, "st_gid", None),
                    getattr(st, "st_size", 0),
                )
            except FileNotFoundError:
                diag.debug("Log file does not exist yet: %s", lf)
            except Exception as exc:
                diag.debug("Failed to stat log file %s: %s", lf, exc)

            # Try opening for append to check writability (no-op write)
            try:
                # Ensure parent dir exists before trying to open, best-effort
                parent_dir = os.path.dirname(lf) or "."
                if parent_dir and not os.path.exists(parent_dir):
                    os.makedirs(parent_dir, exist_ok=True)
                with open(lf, "a") as f:
                    f.write("")  # no-op
                diag.debug("Successfully opened log file for append: %s", lf)
            except Exception as exc:
                # Use exception logging so we get stack trace in whatever handlers are present
                diag.exception("Unable to open/write to log file %s: %s", lf, exc)

        # --- Forced startup dump (guaranteed write) ---
        # If debugging via TOML or env was requested, produce a deterministic
        # diagnostic file under /tmp so we capture the provider environment and
        # permissions even when logging handlers are silent or env vars are cleared.
        try:
            debug_dump_enabled = False
            garm_debug_env = os.environ.get("GARM_DEBUG_DUMP")
            if garm_debug_env is not None and garm_debug_env != "":
                if str(garm_debug_env).lower() in ("1", "true", "yes"):
                    debug_dump_enabled = True
            elif logging_cfg and getattr(logging_cfg, "debug_dump", False):
                debug_dump_enabled = True

            if debug_dump_enabled:
                try:
                    import json as _json

                    report = {
                        "pid": os.getpid(),
                        "uid": getattr(os, "getuid", lambda: None)(),
                        "gid": getattr(os, "getgid", lambda: None)(),
                        "cwd": os.getcwd(),
                        "env": {
                            "GARM_LOG_LEVEL": os.environ.get("GARM_LOG_LEVEL"),
                            "GARM_LOG_FILE": os.environ.get("GARM_LOG_FILE")
                            or (logging_cfg.file if logging_cfg else None),
                            "GARM_LOG_JSON": os.environ.get("GARM_LOG_JSON"),
                            "GARM_COMMAND": os.environ.get("GARM_COMMAND"),
                            "GARM_PROVIDER_CONFIG_FILE": os.environ.get(
                                "GARM_PROVIDER_CONFIG_FILE"
                            ),
                        },
                        "toml_probe": {
                            "path": toml_path if "toml_path" in locals() else None,
                            "logging": {
                                "level": logging_cfg.level if logging_cfg else None,
                                "file": logging_cfg.file if logging_cfg else None,
                                "json": logging_cfg.json if logging_cfg else None,
                                "debug_dump": logging_cfg.debug_dump
                                if logging_cfg
                                else None,
                            }
                            if logging_cfg
                            else None,
                        },
                    }

                    # If lf is present, add stat info and try a direct append with a single marker line
                    if lf:
                        try:
                            st = os.stat(lf)
                            report["log_file_stat"] = {
                                "exists": True,
                                "uid": getattr(st, "st_uid", None),
                                "gid": getattr(st, "st_gid", None),
                                "mode": oct(st.st_mode & 0o777),
                                "size": getattr(st, "st_size", 0),
                            }
                        except FileNotFoundError:
                            report["log_file_stat"] = {"exists": False}
                        except Exception as exc:
                            report["log_file_stat"] = {"error": str(exc)}

                        # Attempt to append a safe marker so the host-mounted file is touched
                        try:
                            with open(lf, "a") as _lfh:
                                _lfh.write(f"[startup-dump] pid={os.getpid()}\\n")
                            report["log_file_write_attempt"] = "ok"
                        except Exception as exc:
                            report["log_file_write_attempt"] = f"failed: {exc}"

                    # Write the JSON report to /tmp (best-effort)
                    try:
                        with open("/tmp/garm_provider_startup.txt", "w") as rpt:
                            rpt.write(_json.dumps(report, indent=2))
                        # Ensure permissive read for convenience
                        try:
                            os.chmod("/tmp/garm_provider_startup.txt", 0o644)
                        except Exception:
                            pass
                    except Exception as exc:
                        diag.exception(
                            "Failed to write /tmp/garm_provider_startup.txt: %s", exc
                        )

                except Exception as exc:
                    diag.exception("Startup forced dump failed: %s", exc)
        except Exception:
            # Never allow diagnostics to break startup
            pass

    except Exception:
        # Diagnostics must never crash provider startup; swallow errors but log them.
        try:
            logging.getLogger(__name__).exception("Startup diagnostics failed")
        except Exception:
            # Last-resort silent ignore to avoid raising during import/startup
            pass


# Ensure logging is configured on import so the controller (which may import or
# spawn the provider and clear environment variables) still gets TOML-based
# logging configured. This runs once at module import time.
_setup_logging()


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
    _setup_logging(config)

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
