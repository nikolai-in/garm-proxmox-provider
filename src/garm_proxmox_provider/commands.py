"""GARM command handlers — core logic decoupled from environment variables."""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, NoReturn

from .client import PVEClient
from .cloud_init import render_userdata
from .config import ConfigError, load_config
from .models import BootstrapInstance, Instance, InstanceStatus

logger = logging.getLogger(__name__)


def _get_config(config_path: str) -> Any:
    """Load provider config from the given path."""
    try:
        return load_config(config_path)
    except ConfigError as exc:
        _fatal(str(exc))


def _fatal(msg: str, exit_code: int = 1) -> NoReturn:
    print(msg, file=sys.stderr)
    sys.exit(exit_code)


def _print_instance(instance: Instance) -> None:
    print(instance.to_json())


def _apply_extra_specs(bootstrap: BootstrapInstance, cfg: Any) -> dict[str, Any]:
    """Merge defaults with extra_specs overrides from the bootstrap payload."""
    c = cfg.cluster
    f = cfg.get_flavor(bootstrap.flavor)
    overrides: dict[str, Any] = {}
    es = bootstrap.extra_specs

    overrides["cores"] = int(es.get("cores", f.cores))
    overrides["memory_mb"] = int(es.get("memory_mb", f.memory_mb))
    overrides["node"] = es.get("node", c.node)
    overrides["lxc_unprivileged"] = bool(es.get("lxc_unprivileged", c.lxc_unprivileged))
    return overrides


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def create_instance(config_path: str, bootstrap_data: str, provider_type: str = "vm") -> None:
    """CreateInstance: create VM or LXC container, print Instance."""
    cfg = _get_config(config_path)
    if not bootstrap_data.strip():
        _fatal("CreateInstance requires bootstrap JSON")
    try:
        data = json.loads(bootstrap_data)
    except json.JSONDecodeError as exc:
        _fatal(f"Invalid bootstrap JSON: {exc}")

    logger.info("Bootstrap JSON: %s", bootstrap_data)

    bootstrap = BootstrapInstance.from_dict(data)
    logger.info(
        "Creating instance: name=%s, os_type=%s, image=%s, provider_type=%s",
        bootstrap.name,
        bootstrap.os_type,
        bootstrap.image,
        provider_type,
    )
    overrides = _apply_extra_specs(bootstrap, cfg)

    def factory(vid: str) -> str:
        return bootstrap.userdata or render_userdata(
            bootstrap=bootstrap,
            provider_id=vid,
            defaults=cfg.cluster,
        )

    client = PVEClient(cfg)
    try:
        instance = client.create_instance(
            name=bootstrap.name,
            controller_id=bootstrap.controller_id,
            pool_id=bootstrap.pool_id,
            provider_type=provider_type,
            userdata=bootstrap.userdata,
            userdata_factory=factory,
            os_type=bootstrap.os_type,
            os_arch=bootstrap.os_arch,
            cores=overrides["cores"],
            memory_mb=overrides["memory_mb"],
            node=overrides["node"],
            lxc_unprivileged=overrides["lxc_unprivileged"],
            image=bootstrap.image,
        )
    except Exception as exc:
        logger.exception("Failed to create instance %s", bootstrap.name)
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
    logger.info("Deleting instance: %s", instance_id)
    cfg = _get_config(config_path)
    client = PVEClient(cfg)
    try:
        client.delete_instance(instance_id)
    except Exception as exc:
        _fatal(f"DeleteInstance failed: {exc}")


def get_instance(config_path: str, instance_id: str) -> None:
    """GetInstance: return current state of the VM as Instance JSON."""
    logger.debug("Getting instance: %s", instance_id)
    cfg = _get_config(config_path)
    client = PVEClient(cfg)
    try:
        instance = client.get_instance(instance_id)
    except Exception as exc:
        _fatal(f"GetInstance failed: {exc}")
    _print_instance(instance)


def list_instances(config_path: str, pool_id: str) -> None:
    """ListInstances: return JSON array of Instance for the pool."""
    logger.debug("Listing instances for pool: %s", pool_id)
    cfg = _get_config(config_path)
    client = PVEClient(cfg)
    try:
        instances = client.list_instances(pool_id)
    except Exception as exc:
        _fatal(f"ListInstances failed: {exc}")
    print(json.dumps([i.to_dict() for i in instances]))


def remove_all_instances(config_path: str, controller_id: str) -> None:
    """RemoveAllInstances: delete all VMs belonging to this controller."""
    logger.info("Removing all instances for controller: %s", controller_id)
    cfg = _get_config(config_path)
    client = PVEClient(cfg)
    try:
        client.remove_all_instances(controller_id)
    except Exception as exc:
        _fatal(f"RemoveAllInstances failed: {exc}")


def start(config_path: str, instance_id: str) -> None:
    """Start: power on VM."""
    logger.info("Starting instance: %s", instance_id)
    cfg = _get_config(config_path)
    client = PVEClient(cfg)
    try:
        client.start_instance(instance_id)
    except Exception as exc:
        _fatal(f"Start failed: {exc}")


def stop(config_path: str, instance_id: str) -> None:
    """Stop: ACPI shutdown of VM."""
    logger.info("Stopping instance: %s", instance_id)
    cfg = _get_config(config_path)
    client = PVEClient(cfg)
    try:
        client.stop_instance(instance_id)
    except Exception as exc:
        _fatal(f"Stop failed: {exc}")
