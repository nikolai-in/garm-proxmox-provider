"""GARM command handlers — core logic decoupled from environment variables."""

from __future__ import annotations

import io
import json
import logging
import sys
from typing import Any

from .client import PVEClient
from .cloud_init import render_lxc_env_vars, render_userdata
from .config import ConfigError, load_config
from .models import BootstrapInstance, Instance, InstanceStatus

logger = logging.getLogger(__name__)


def _get_config(config_path: str) -> Any:
    """Load provider config from the given path."""
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


def create_instance(config_path: str, bootstrap_data: str) -> None:
    """CreateInstance: create VM, print Instance."""
    cfg = _get_config(config_path)
    if not bootstrap_data.strip():
        _fatal("CreateInstance requires bootstrap JSON")
    try:
        data = json.loads(bootstrap_data)
    except json.JSONDecodeError as exc:
        _fatal(f"Invalid bootstrap JSON: {exc}")

    bootstrap = BootstrapInstance.from_dict(data)
    overrides = _apply_extra_specs(bootstrap, cfg)

    lxc_env_vars: dict[str, Any] | None = render_lxc_env_vars(
        bootstrap=bootstrap,
        provider_id="PLACEHOLDER",
    )
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


def delete_instance(config_path: str, instance_id: str) -> None:
    """DeleteInstance: stop and destroy VM; no-op if missing."""
    cfg = _get_config(config_path)
    client = PVEClient(cfg)
    try:
        client.delete_instance(instance_id)
    except Exception as exc:
        _fatal(f"DeleteInstance failed: {exc}")


def get_instance(config_path: str, instance_id: str) -> None:
    """GetInstance: return current state of the VM as Instance JSON."""
    cfg = _get_config(config_path)
    client = PVEClient(cfg)
    try:
        instance = client.get_instance(instance_id)
    except Exception as exc:
        _fatal(f"GetInstance failed: {exc}")
    _print_instance(instance)


def list_instances(config_path: str, pool_id: str) -> None:
    """ListInstances: return JSON array of Instance for the pool."""
    cfg = _get_config(config_path)
    client = PVEClient(cfg)
    try:
        instances = client.list_instances(pool_id)
    except Exception as exc:
        _fatal(f"ListInstances failed: {exc}")
    print(json.dumps([i.to_dict() for i in instances]))


def remove_all_instances(config_path: str, controller_id: str) -> None:
    """RemoveAllInstances: delete all VMs belonging to this controller."""
    cfg = _get_config(config_path)
    client = PVEClient(cfg)
    try:
        client.remove_all_instances(controller_id)
    except Exception as exc:
        _fatal(f"RemoveAllInstances failed: {exc}")


def start(config_path: str, instance_id: str) -> None:
    """Start: power on VM."""
    cfg = _get_config(config_path)
    client = PVEClient(cfg)
    try:
        client.start_instance(instance_id)
    except Exception as exc:
        _fatal(f"Start failed: {exc}")


def stop(config_path: str, instance_id: str) -> None:
    """Stop: ACPI shutdown of VM."""
    cfg = _get_config(config_path)
    client = PVEClient(cfg)
    try:
        client.stop_instance(instance_id)
    except Exception as exc:
        _fatal(f"Stop failed: {exc}")
