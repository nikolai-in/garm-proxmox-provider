"""Proxmox VE client wrapper using proxmoxer."""

from __future__ import annotations

import io
import json
import logging
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from proxmoxer import ProxmoxAPI

from .models import Address, Instance, InstanceStatus

if TYPE_CHECKING:
    from .config import Config

logger = logging.getLogger(__name__)

# JSON key written into the VM description field to mark GARM-managed VMs.
_GARM_META_MARKER = "__garm__"


def _parse_garm_meta(description: str | None) -> dict[str, str] | None:
    """Extract the GARM metadata dict from a VM description field, or None."""
    if not description:
        return None
    for line in description.splitlines():
        line = line.strip()
        if line.startswith("{") and _GARM_META_MARKER in line:
            try:
                data = json.loads(line)
                if isinstance(data, dict) and _GARM_META_MARKER in data:
                    return data
            except json.JSONDecodeError:
                continue
    return None


def _build_garm_meta(
    controller_id: str,
    pool_id: str,
    instance_name: str,
) -> str:
    """Serialise GARM metadata to a single-line JSON string for VM description."""
    return json.dumps(
        {
            _GARM_META_MARKER: True,
            "garm_controller_id": controller_id,
            "garm_pool_id": pool_id,
            "garm_instance_name": instance_name,
        },
        separators=(",", ":"),
    )


def _pve_status_to_garm(pve_status: str) -> InstanceStatus:
    mapping = {
        "running": InstanceStatus.RUNNING,
        "stopped": InstanceStatus.STOPPED,
        "paused": InstanceStatus.STOPPED,
    }
    return mapping.get(pve_status, InstanceStatus.UNKNOWN)


class PVEClient:
    """Thin wrapper around proxmoxer.ProxmoxAPI providing GARM-oriented operations."""

    def __init__(self, cfg: Config) -> None:
        parsed = urlparse(cfg.pve.host)
        host = parsed.hostname or cfg.pve.host
        port = parsed.port or 8006

        self._prox = ProxmoxAPI(
            host,
            port=port,
            user=cfg.pve.user,
            token_name=cfg.pve.token_name,
            token_value=cfg.pve.token_value,
            verify_ssl=cfg.pve.verify_ssl,
            service="PVE",
        )
        self._defaults = cfg.defaults

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _wait_task(self, node: str, upid: str, timeout: int = 300) -> None:
        """Block until the PVE task identified by *upid* finishes."""
        if not upid or not str(upid).startswith("UPID:"):
            return
        interval = 2
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self._prox.nodes(node).tasks(upid).status.get()
            if status.get("status") == "stopped":
                exitstatus = status.get("exitstatus", "UNKNOWN")
                if exitstatus != "OK":
                    raise RuntimeError(f"PVE task {upid} failed: {exitstatus}")
                return
            time.sleep(interval)
        raise TimeoutError(f"PVE task {upid} timed out after {timeout}s")

    def _find_vm(self, vmid: int | str) -> tuple[str, dict[str, Any]] | None:
        """Return (node, resource_dict) for a VMID or None if not found."""
        vmid_int = int(vmid)
        try:
            resources = self._prox.cluster.resources.get(type="vm")
        except Exception as exc:
            logger.warning("Failed to query cluster resources: %s", exc)
            return None
        for res in resources:
            if res.get("vmid") == vmid_int:
                return res.get("node", self._defaults.node), res
        return None

    def _vm_config(self, node: str, vmid: int) -> dict[str, Any]:
        return self._prox.nodes(node).qemu(vmid).config.get()

    def _get_ips(self, node: str, vmid: int) -> list[Address]:
        """Try to fetch IPs via QEMU guest agent; return empty list on failure."""
        try:
            result = self._prox.nodes(node).qemu(vmid).agent.get(
                "network-get-interfaces"
            )
            addresses: list[Address] = []
            for iface in result.get("result", []):
                if iface.get("name") == "lo":
                    continue
                for ip_info in iface.get("ip-addresses", []):
                    ip = ip_info.get("ip-address", "")
                    ip_type = ip_info.get("ip-address-type", "ipv4")
                    if ip and not ip.startswith("169.254") and ip != "::1":
                        addresses.append(Address(address=ip, type=ip_type))
            return addresses
        except Exception:
            return []

    def _next_vmid(self) -> int:
        return int(self._prox.cluster.nextid.get())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_instances(self, pool_id: str) -> list[Instance]:
        """Return all GARM instances belonging to *pool_id*."""
        try:
            resources = self._prox.cluster.resources.get(type="vm")
        except Exception as exc:
            raise RuntimeError(f"Failed to list VMs: {exc}") from exc

        instances: list[Instance] = []
        for res in resources:
            vmid = res.get("vmid")
            node = res.get("node", self._defaults.node)
            try:
                config = self._vm_config(node, vmid)
            except Exception:
                continue
            meta = _parse_garm_meta(config.get("description"))
            if meta is None:
                continue
            if meta.get("garm_pool_id") != pool_id:
                continue
            instances.append(
                Instance(
                    provider_id=str(vmid),
                    name=meta.get("garm_instance_name", config.get("name", "")),
                    os_type="linux",
                    os_arch="amd64",
                    status=_pve_status_to_garm(res.get("status", "")),
                    pool_id=pool_id,
                )
            )
        return instances

    def get_instance(self, vmid: str | int) -> Instance:
        """Return Instance for *vmid*; raises RuntimeError if not found."""
        found = self._find_vm(vmid)
        if found is None:
            raise RuntimeError(f"VM {vmid} not found")
        node, res = found
        config = self._vm_config(node, int(vmid))
        meta = _parse_garm_meta(config.get("description")) or {}
        addresses = self._get_ips(node, int(vmid))
        return Instance(
            provider_id=str(vmid),
            name=meta.get("garm_instance_name", config.get("name", "")),
            os_type="linux",
            os_arch="amd64",
            status=_pve_status_to_garm(res.get("status", "")),
            pool_id=meta.get("garm_pool_id", ""),
            addresses=addresses,
        )

    def create_instance(
        self,
        name: str,
        controller_id: str,
        pool_id: str,
        userdata: str,
        *,
        cores: int | None = None,
        memory_mb: int | None = None,
        disk_gb: int | None = None,
        node: str | None = None,
    ) -> Instance:
        """Clone template, inject cloud-init, start VM; return Instance."""
        d = self._defaults
        node = node or d.node
        cores = cores or d.cores
        memory_mb = memory_mb or d.memory_mb
        disk_gb = disk_gb or d.disk_gb

        if d.template_vmid is None:
            raise RuntimeError(
                "defaults.template_vmid must be set in the provider config "
                "to create instances"
            )

        vmid = self._next_vmid()
        logger.info("Cloning template %d -> VMID %d (%s)", d.template_vmid, vmid, name)

        # Clone the template (full clone)
        upid = self._prox.nodes(node).qemu(d.template_vmid).clone.post(
            newid=vmid,
            name=name,
            full=1,
            storage=d.storage,
            **({"pool": d.pool} if d.pool else {}),
        )
        self._wait_task(node, upid)

        # Build VM config update dict
        config_update: dict[str, Any] = {
            "cores": cores,
            "memory": memory_mb,
            "description": _build_garm_meta(controller_id, pool_id, name),
        }
        if d.ssh_public_key:
            from urllib.parse import quote

            config_update["sshkeys"] = quote(d.ssh_public_key.strip(), safe="")

        # Upload cloud-init snippet and attach if snippets_storage is configured
        if d.snippets_storage:
            snippet_name = f"garm-{vmid}.yml"
            userdata_bytes = userdata.encode("utf-8")
            try:
                self._prox.nodes(node).storage(d.snippets_storage).upload.post(
                    content="snippets",
                    filename=snippet_name,
                    file=io.BytesIO(userdata_bytes),
                )
            except Exception as exc:
                logger.warning("Failed to upload cloud-init snippet: %s", exc)
            else:
                config_update["cicustom"] = (
                    f"user={d.snippets_storage}:snippets/{snippet_name}"
                )

        self._prox.nodes(node).qemu(vmid).config.post(**config_update)

        # Start the VM
        logger.info("Starting VM %d", vmid)
        upid = self._prox.nodes(node).qemu(vmid).status.start.post()
        self._wait_task(node, upid)

        return Instance(
            provider_id=str(vmid),
            name=name,
            os_type="linux",
            os_arch="amd64",
            status=InstanceStatus.RUNNING,
            pool_id=pool_id,
        )

    def delete_instance(self, vmid: str | int) -> None:
        """Stop and destroy VM *vmid*; no-op if the VM does not exist."""
        found = self._find_vm(vmid)
        if found is None:
            logger.info("VM %s not found; treating delete as no-op", vmid)
            return
        node, res = found
        vmid_int = int(vmid)

        # Stop gracefully if running
        if res.get("status") == "running":
            logger.info("Stopping VM %d before deletion", vmid_int)
            try:
                upid = self._prox.nodes(node).qemu(vmid_int).status.stop.post()
                self._wait_task(node, upid, timeout=120)
            except Exception as exc:
                logger.warning("Failed to stop VM %d: %s; proceeding to delete", vmid_int, exc)

        # Delete VM and its disks
        upid = self._prox.nodes(node).qemu(vmid_int).delete(purge=1, **{"destroy-unreferenced-disks": 1})
        self._wait_task(node, upid)
        logger.info("VM %d deleted", vmid_int)

        # Remove cloud-init snippet if it exists
        d = self._defaults
        if d.snippets_storage:
            snippet_name = f"garm-{vmid_int}.yml"
            try:
                self._prox.nodes(node).storage(d.snippets_storage).content(
                    f"snippets/{snippet_name}"
                ).delete()
            except Exception:
                pass

    def start_instance(self, vmid: str | int) -> Instance:
        """Power on VM *vmid*."""
        found = self._find_vm(vmid)
        if found is None:
            raise RuntimeError(f"VM {vmid} not found")
        node, _ = found
        upid = self._prox.nodes(node).qemu(int(vmid)).status.start.post()
        self._wait_task(node, upid)
        return self.get_instance(vmid)

    def stop_instance(self, vmid: str | int) -> Instance:
        """Power off VM *vmid* (ACPI shutdown)."""
        found = self._find_vm(vmid)
        if found is None:
            raise RuntimeError(f"VM {vmid} not found")
        node, _ = found
        upid = self._prox.nodes(node).qemu(int(vmid)).status.shutdown.post()
        self._wait_task(node, upid, timeout=120)
        return self.get_instance(vmid)

    def remove_all_instances(self, controller_id: str) -> None:
        """Delete all VMs tagged with *controller_id*."""
        try:
            resources = self._prox.cluster.resources.get(type="vm")
        except Exception as exc:
            raise RuntimeError(f"Failed to list VMs: {exc}") from exc

        for res in resources:
            vmid = res.get("vmid")
            node = res.get("node", self._defaults.node)
            try:
                config = self._vm_config(node, vmid)
            except Exception:
                continue
            meta = _parse_garm_meta(config.get("description"))
            if meta and meta.get("garm_controller_id") == controller_id:
                logger.info("RemoveAll: deleting VM %d", vmid)
                try:
                    self.delete_instance(vmid)
                except Exception as exc:
                    logger.error("Failed to delete VM %d: %s", vmid, exc)
