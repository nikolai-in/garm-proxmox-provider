"""GARM command handlers — one function per supported GARM_COMMAND."""

from __future__ import annotations

import io
import json
import logging
import os
import sys
from typing import Any

from .client import PVEClient
from .cloud_init import render_lxc_env_vars, render_userdata
from .config import ConfigError, load_config
from .models import BootstrapInstance, Instance, InstanceStatus

logger = logging.getLogger(__name__)


def _get_env(name: str) -> str:
    """Return env var *name* or exit with a descriptive error."""
    value = os.environ.get(name, "")
    if not value:
        _fatal(f"Required environment variable {name!r} is not set")
    return value


def _get_config() -> Any:
    """Load provider config from GARM_PROVIDER_CONFIG_FILE."""
    config_path = _get_env("GARM_PROVIDER_CONFIG_FILE")
    try:
        return load_config(config_path)
    except ConfigError as exc:
        _fatal(str(exc))


def _fatal(msg: str, exit_code: int = 1) -> None:  # type: ignore[return]
    print(msg, file=sys.stderr)
    sys.exit(exit_code)


def _print_instance(instance: Instance) -> None:
    print(instance.to_json())


def _apply_extra_specs(bootstrap: BootstrapInstance, cfg: Any) -> dict[str, Any]:
    """Merge defaults with extra_specs overrides from the bootstrap payload."""
    d = cfg.defaults
    overrides: dict[str, Any] = {}
    es = bootstrap.extra_specs

    overrides["cores"] = int(es.get("cores", d.cores))
    overrides["memory_mb"] = int(es.get("memory_mb", d.memory_mb))
    overrides["node"] = es.get("node", d.node)

    # Allow per-instance template override via extra_specs
    tmpl_raw = es.get("template_vmid")
    overrides["template_vmid"] = int(tmpl_raw) if tmpl_raw is not None else None
    return overrides


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def cmd_create_instance() -> None:
    """CreateInstance: read bootstrap JSON from stdin, create VM, print Instance."""
    cfg = _get_config()
    raw = sys.stdin.read()
    if not raw.strip():
        _fatal("CreateInstance requires bootstrap JSON on stdin")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        _fatal(f"Invalid bootstrap JSON: {exc}")

    bootstrap = BootstrapInstance.from_dict(data)
    overrides = _apply_extra_specs(bootstrap, cfg)

    if cfg.defaults.instance_type == "lxc":
        lxc_env_vars: dict[str, Any] | None = render_lxc_env_vars(
            bootstrap=bootstrap,
            provider_id="PLACEHOLDER",
        )
        userdata = ""
    else:
        lxc_env_vars = None
        userdata = render_userdata(
            bootstrap=bootstrap,
            provider_id="PLACEHOLDER",  # real VMID not known yet
            defaults=cfg.defaults,
        )

    client = PVEClient(cfg)
    try:
        instance = client.create_instance(
            name=bootstrap.name,
            controller_id=bootstrap.controller_id,
            pool_id=bootstrap.pool_id,
            userdata=userdata,
            os_type=bootstrap.os_type,
            os_arch=bootstrap.os_arch,
            cores=overrides["cores"],
            memory_mb=overrides["memory_mb"],
            node=overrides["node"],
            template_vmid=overrides["template_vmid"],
            lxc_env_vars=lxc_env_vars,
            image=bootstrap.image,
        )
        # Re-render user-data with real provider_id for the snippet (QEMU only)
        if cfg.defaults.snippets_storage and cfg.defaults.instance_type != "lxc":
            userdata_final = render_userdata(
                bootstrap=bootstrap,
                provider_id=instance.provider_id,
                defaults=cfg.defaults,
            )
            # Update snippet in place
            snippet_name = f"garm-{instance.provider_id}.yml"
            node = overrides["node"]
            try:
                client._prox.nodes(node).storage(
                    cfg.defaults.snippets_storage
                ).upload.post(
                    content="snippets",
                    filename=snippet_name,
                    file=io.BytesIO(userdata_final.encode("utf-8")),
                )
            except Exception as exc:
                logger.warning(
                    "Failed to update cloud-init snippet with real VMID: %s", exc
                )
    except Exception as exc:
        err_instance = Instance(
            provider_id="",
            name=bootstrap.name,
            status=InstanceStatus.ERROR,
            pool_id=bootstrap.pool_id,
            provider_fault=str(exc),
        )
        print(err_instance.to_json())
        sys.exit(1)

    _print_instance(instance)


def cmd_delete_instance() -> None:
    """DeleteInstance: stop and destroy VM; no-op if missing."""
    cfg = _get_config()
    vmid = _get_env("GARM_INSTANCE_ID")
    client = PVEClient(cfg)
    try:
        client.delete_instance(vmid)
    except Exception as exc:
        _fatal(f"DeleteInstance failed: {exc}")


def cmd_get_instance() -> None:
    """GetInstance: return current state of the VM as Instance JSON."""
    cfg = _get_config()
    vmid = _get_env("GARM_INSTANCE_ID")
    client = PVEClient(cfg)
    try:
        instance = client.get_instance(vmid)
    except Exception as exc:
        _fatal(f"GetInstance failed: {exc}")
    _print_instance(instance)


def cmd_list_instances() -> None:
    """ListInstances: return JSON array of Instance for the pool."""
    cfg = _get_config()
    pool_id = _get_env("GARM_POOL_ID")
    client = PVEClient(cfg)
    try:
        instances = client.list_instances(pool_id)
    except Exception as exc:
        _fatal(f"ListInstances failed: {exc}")
    print(json.dumps([i.to_dict() for i in instances]))


def cmd_remove_all_instances() -> None:
    """RemoveAllInstances: delete all VMs belonging to this controller."""
    cfg = _get_config()
    controller_id = _get_env("GARM_CONTROLLER_ID")
    client = PVEClient(cfg)
    try:
        client.remove_all_instances(controller_id)
    except Exception as exc:
        _fatal(f"RemoveAllInstances failed: {exc}")


def cmd_start() -> None:
    """Start: power on VM."""
    cfg = _get_config()
    vmid = _get_env("GARM_INSTANCE_ID")
    client = PVEClient(cfg)
    try:
        instance = client.start_instance(vmid)
    except Exception as exc:
        _fatal(f"Start failed: {exc}")
    _print_instance(instance)


def cmd_stop() -> None:
    """Stop: ACPI shutdown of VM."""
    cfg = _get_config()
    vmid = _get_env("GARM_INSTANCE_ID")
    client = PVEClient(cfg)
    try:
        instance = client.stop_instance(vmid)
    except Exception as exc:
        _fatal(f"Stop failed: {exc}")
    _print_instance(instance)


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

COMMANDS: dict[str, Any] = {
    "CreateInstance": cmd_create_instance,
    "DeleteInstance": cmd_delete_instance,
    "GetInstance": cmd_get_instance,
    "ListInstances": cmd_list_instances,
    "RemoveAllInstances": cmd_remove_all_instances,
    "Start": cmd_start,
    "Stop": cmd_stop,
}
