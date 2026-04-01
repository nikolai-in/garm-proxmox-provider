"""Click-based CLI entrypoint and GARM_COMMAND dispatcher."""

from __future__ import annotations

import logging
import os
import sys

import click

from .commands import COMMANDS


def _setup_logging() -> None:
    level = logging.DEBUG if os.environ.get("GARM_DEBUG") else logging.WARNING
    logging.basicConfig(
        stream=sys.stderr,
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@click.command(
    context_settings={"help_option_names": ["-h", "--help"]},
    help=(
        "GARM external provider for Proxmox VE.\n\n"
        "Dispatches to the handler named by the GARM_COMMAND environment variable.\n"
        "Supported commands: "
        + ", ".join(sorted(COMMANDS))
        + ".\n\n"
        "Required environment variables:\n"
        "  GARM_COMMAND               Command to run.\n"
        "  GARM_PROVIDER_CONFIG_FILE  Path to provider TOML config.\n"
        "  GARM_INSTANCE_ID           VMID (required by Get/Delete/Start/Stop).\n"
        "  GARM_POOL_ID               Pool UUID (required by ListInstances, CreateInstance).\n"
        "  GARM_CONTROLLER_ID         Controller UUID (required by RemoveAllInstances).\n"
    ),
)
def cli() -> None:
    _setup_logging()
    command = os.environ.get("GARM_COMMAND", "").strip()
    if not command:
        click.echo(
            "Error: GARM_COMMAND environment variable is not set.\n"
            f"Valid commands: {', '.join(sorted(COMMANDS))}",
            err=True,
        )
        sys.exit(1)

    handler = COMMANDS.get(command)
    if handler is None:
        click.echo(
            f"Error: Unknown GARM_COMMAND {command!r}.\n"
            f"Valid commands: {', '.join(sorted(COMMANDS))}",
            err=True,
        )
        sys.exit(1)

    handler()


def main() -> None:
    cli()
