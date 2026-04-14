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
    os_type: str = "linux",
    os_arch: str = "amd64",
) -> str:
    """Serialise GARM metadata to a single-line JSON string for VM description."""
    return json.dumps(
        {
            _GARM_META_MARKER: True,
            "garm_controller_id": controller_id,
            "garm_pool_id": pool_id,
            "garm_instance_name": instance_name,
            "garm_os_type": os_type,
            "garm_os_arch": os_arch,
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

    def _wait_task(self, node: str, upid: Any, timeout: int = 300) -> None:
        """Block until the PVE task identified by *upid* finishes."""
        if not upid or not str(upid).startswith("UPID:"):
            return
        interval = 2
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self._prox.nodes(node).tasks(upid).status.get()
            if status and status.get("status") == "stopped":
                exitstatus = status.get("exitstatus", "UNKNOWN")
                if exitstatus != "OK":
                    raise RuntimeError(f"PVE task {upid} failed: {exitstatus}")
                return
            time.sleep(interval)
        raise TimeoutError(f"PVE task {upid} timed out after {timeout}s")

    def _find_instance(self, vmid: int | str) -> tuple[str, dict[str, Any], str] | None:
        """Return (node, resource_dict, res_type) for a VMID or None if not found.

        *res_type* is ``"qemu"`` for QEMU VMs or ``"lxc"`` for LXC containers.
        The ``cluster/resources?type=vm`` endpoint returns both types; each
        resource carries a ``type`` field indicating which it is.
        """
        vmid_int = None
        try:
            vmid_int = int(vmid)
        except (ValueError, TypeError):
            pass

        try:
            resources = self._prox.cluster.resources.get(type="vm") or []
        except Exception as exc:
            logger.warning("Failed to query cluster resources: %s", exc)
            return None
        for res in resources:
            if not res:
                continue
            res_vmid = res.get("vmid")
            if (
                vmid_int is not None
                and res_vmid is not None
                and int(res_vmid) == vmid_int
            ) or (vmid_int is None and res.get("name") == vmid):
                node = res.get("node", self._defaults.node)
                res_type = res.get("type", "qemu")
                return node, res, res_type
        return None

    def _vm_config(self, node: str, vmid: int) -> dict[str, Any]:
        return self._prox.nodes(node).qemu(vmid).config.get() or {}

    def _get_ips(self, node: str, vmid: int) -> list[Address]:
        """Try to fetch IPs via QEMU guest agent; return empty list on failure."""
        try:
            result = (
                self._prox.nodes(node).qemu(vmid).agent.get("network-get-interfaces")
            )
            addresses: list[Address] = []
            if result:
                for iface in result.get("result", []):
                    if iface.get("name") == "lo":
                        continue
                    for ip_info in iface.get("ip-addresses", []):
                        ip = ip_info.get("ip-address", "")
                        ip_type = ip_info.get("ip-address-type", "ipv4")
                        addresses.append(Address(address=ip, type=ip_type))
            return addresses
        except Exception:
            return []

    def _lxc_config(self, node: str, vmid: int) -> dict[str, Any]:
        return self._prox.nodes(node).lxc(vmid).config.get() or {}

    def _get_config_for(self, node: str, vmid: int, res_type: str) -> dict[str, Any]:
        """Return the config dict for either a QEMU VM or LXC container."""
        if res_type == "lxc":
            return self._lxc_config(node, vmid)
        return self._vm_config(node, vmid)

    def _lxc_get_ips(self, node: str, vmid: int) -> list[Address]:
        """Fetch IPs from an LXC container's network interfaces."""
        try:
            result = self._prox.nodes(node).lxc(vmid).interfaces.get() or []
            addresses: list[Address] = []
            if result:
                for iface in result:
                    if iface.get("name") == "lo":
                        continue
                    ip4 = iface.get("inet", "")
                    ip6 = iface.get("inet6", "")
                    if ip4:
                        ip = ip4.split("/")[0]
                        if not ip.startswith("169.254"):
                            addresses.append(Address(address=ip, type="ipv4"))
                    if ip6:
                        ip = ip6.split("/")[0]
                        if ip != "::1":
                            addresses.append(Address(address=ip, type="ipv6"))
            return addresses
        except Exception:
            return []

    def _get_ips_for(self, node: str, vmid: int, res_type: str) -> list[Address]:
        """Return IP addresses for either a QEMU VM or LXC container."""
        if res_type == "lxc":
            return self._lxc_get_ips(node, vmid)
        return self._get_ips(node, vmid)

    def _next_vmid(self) -> int:
        next_id = self._prox.cluster.nextid.get()
        return int(next_id) if next_id is not None else 0

    def _resolve_template_vmid(
        self,
        os_type: str,
        os_arch: str,
        override: int | None = None,
        image: str = "",
    ) -> int:
        """Return the template VMID to clone for the given OS type/arch or image.

        Priority:
        1. ``override`` (per-instance ``extra_specs.template_vmid``)
        2. ``pool_templates[image]``
        3. ``pool_templates["os_type/os_arch"]``
        4. ``defaults.template_vmid`` (fallback)
        """
        if override is not None:
            return override
        if image and image in self._defaults.pool_templates:
            return self._defaults.pool_templates[image]
        key = f"{os_type}/{os_arch}"
        if key in self._defaults.pool_templates:
            return self._defaults.pool_templates[key]
        if self._defaults.template_vmid is not None:
            return self._defaults.template_vmid
        raise RuntimeError(
            f"No template_vmid configured for image {image!r} or {key!r}. "
            "Set [defaults].template_vmid or add an entry to [pool_templates]."
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_instances(self, pool_id: str) -> list[Instance]:
        """Return all GARM instances belonging to *pool_id* (VMs and containers)."""
        try:
            resources = self._prox.cluster.resources.get(type="vm") or []
        except Exception as exc:
            raise RuntimeError(f"Failed to list instances: {exc}") from exc

        instances: list[Instance] = []
        for res in resources:
            vmid = res.get("vmid")
            node = res.get("node", self._defaults.node)
            res_type = res.get("type", "qemu")
            try:
                config = self._get_config_for(node, vmid, res_type)
            except Exception:
                continue
            if not config:
                continue
            meta = _parse_garm_meta(config.get("description"))
            if meta is None:
                continue
            if meta.get("garm_pool_id") != pool_id:
                continue
            instances.append(
                Instance(
                    provider_id=str(vmid),
                    name=meta.get("garm_instance_name", config.get("name", "")) or "",
                    os_type=meta.get("garm_os_type", "linux"),
                    os_arch=meta.get("garm_os_arch", "amd64"),
                    status=_pve_status_to_garm(res.get("status", "")),
                    pool_id=pool_id,
                )
            )
        return instances

    def get_instance(self, vmid: str | int) -> Instance:
        """Return Instance for *vmid*; raises RuntimeError if not found."""
        found = self._find_instance(vmid)
        if found is None:
            raise RuntimeError(f"Instance {vmid} not found")
        node, res, res_type = found
        vmid_raw = res.get("vmid")
        vmid_int = int(vmid_raw) if vmid_raw is not None else 0
        config = self._get_config_for(node, vmid_int, res_type) or {}
        meta = _parse_garm_meta(config.get("description")) or {}
        addresses = self._get_ips_for(node, vmid_int, res_type)
        return Instance(
            provider_id=str(vmid),
            name=meta.get("garm_instance_name", config.get("name", "")) or "",
            os_type=meta.get("garm_os_type", "linux"),
            os_arch=meta.get("garm_os_arch", "amd64"),
            status=_pve_status_to_garm(res.get("status", "")),
            pool_id=meta.get("garm_pool_id", ""),
            addresses=addresses,
        )

    def create_instance(
        self,
        name: str,
        controller_id: str,
        pool_id: str,
        userdata: str = "",
        os_type: str = "linux",
        os_arch: str = "amd64",
        *,
        cores: int | None = None,
        memory_mb: int | None = None,
        node: str | None = None,
        template_vmid: int | None = None,
        lxc_env_vars: dict[str, str] | None = None,
        image: str = "",
    ) -> Instance:
        """Clone template, configure and start instance; return Instance.

        For QEMU VMs (*instance_type* == ``"vm"``), injects a cloud-init
        snippet from *userdata*.  For LXC containers (*instance_type* ==
        ``"lxc"``), injects bootstrap configuration as LXC environment
        variables built from *lxc_env_vars*.
        """
        d = self._defaults
        node = node or d.node
        cores = cores or d.cores
        memory_mb = memory_mb or d.memory_mb

        tmpl_vmid = self._resolve_template_vmid(os_type, os_arch, template_vmid, image)
        garm_meta = _build_garm_meta(controller_id, pool_id, name, os_type, os_arch)

        found_tmpl = self._find_instance(tmpl_vmid)
        if found_tmpl is None:
            raise RuntimeError(f"Template VMID {tmpl_vmid} not found in cluster")
        _, _, res_type = found_tmpl

        for attempt in range(5):
            vmid = self._next_vmid()
            try:
                if res_type == "lxc":
                    return self._create_lxc(
                        vmid=vmid,
                        tmpl_vmid=tmpl_vmid,
                        name=name,
                        pool_id=pool_id,
                        garm_meta=garm_meta,
                        os_type=os_type,
                        os_arch=os_arch,
                        cores=cores,
                        memory_mb=memory_mb,
                        node=node,
                        lxc_env_vars=lxc_env_vars or {},
                    )

                return self._create_qemu(
                    vmid=vmid,
                    tmpl_vmid=tmpl_vmid,
                    name=name,
                    pool_id=pool_id,
                    garm_meta=garm_meta,
                    userdata=userdata,
                    os_type=os_type,
                    os_arch=os_arch,
                    cores=cores,
                    memory_mb=memory_mb,
                    node=node,
                )
            except Exception as exc:
                if "File exists" in str(exc) and attempt < 4:
                    logger.warning("VMID collision (likely %d), retrying...", vmid)
                    import time

                    time.sleep(1)
                    continue
                raise
        raise RuntimeError(
            "Failed to create instance after retries due to VMID collisions"
        )

    def _create_qemu(
        self,
        *,
        vmid: int,
        tmpl_vmid: int,
        name: str,
        pool_id: str,
        garm_meta: str,
        userdata: str,
        os_type: str,
        os_arch: str,
        cores: int,
        memory_mb: int,
        node: str,
    ) -> Instance:
        """Clone a QEMU template, inject cloud-init, start and return Instance."""
        d = self._defaults
        logger.info("Cloning QEMU template %d -> VMID %d (%s)", tmpl_vmid, vmid, name)

        upid = (
            self._prox.nodes(node)
            .qemu(tmpl_vmid)
            .clone.post(
                newid=vmid,
                name=name,
                full=1,
                storage=d.storage,
                **({"pool": d.pool} if d.pool else {}),
            )
        )
        self._wait_task(node, upid)

        config_update: dict[str, Any] = {
            "cores": cores,
            "memory": memory_mb,
            "description": garm_meta,
        }
        if d.ssh_public_key:
            from urllib.parse import quote

            config_update["sshkeys"] = quote(d.ssh_public_key.strip(), safe="")

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

        logger.info("Starting QEMU VM %d", vmid)
        upid = self._prox.nodes(node).qemu(vmid).status.start.post()
        self._wait_task(node, upid)

        return Instance(
            provider_id=str(vmid),
            name=name,
            os_type=os_type,
            os_arch=os_arch,
            status=InstanceStatus.RUNNING,
            pool_id=pool_id,
        )

    def _create_lxc(
        self,
        *,
        vmid: int,
        tmpl_vmid: int,
        name: str,
        pool_id: str,
        garm_meta: str,
        os_type: str,
        os_arch: str,
        cores: int,
        memory_mb: int,
        node: str,
        lxc_env_vars: dict[str, str],
    ) -> Instance:
        """Clone an LXC template, inject env vars, start and return Instance."""
        d = self._defaults
        logger.info("Cloning LXC template %d -> VMID %d (%s)", tmpl_vmid, vmid, name)

        upid = (
            self._prox.nodes(node)
            .lxc(tmpl_vmid)
            .clone.post(
                newid=vmid,
                hostname=name,
                full=1,
                storage=d.storage,
                **({"pool": d.pool} if d.pool else {}),
            )
        )
        self._wait_task(node, upid)

        # Update GARM_PROVIDER_ID now that we know the real VMID
        env_vars = dict(lxc_env_vars)
        env_vars["GARM_PROVIDER_ID"] = str(vmid)

        # Build config — inject env vars as raw LXC config lines
        config_update: dict[str, Any] = {
            "cores": cores,
            "memory": memory_mb,
            "description": garm_meta,
            "unprivileged": int(d.lxc_unprivileged),
        }
        for i, (key, value) in enumerate(env_vars.items()):
            config_update[f"lxc[{i}]"] = f"lxc.environment: {key}={value}"

        self._prox.nodes(node).lxc(vmid).config.put(**config_update)

        logger.info("Starting LXC container %d", vmid)
        upid = self._prox.nodes(node).lxc(vmid).status.start.post()
        self._wait_task(node, upid)

        return Instance(
            provider_id=str(vmid),
            name=name,
            os_type=os_type,
            os_arch=os_arch,
            status=InstanceStatus.RUNNING,
            pool_id=pool_id,
        )

    def delete_instance(self, vmid: str | int) -> None:
        """Stop and destroy instance *vmid*; no-op if the instance does not exist."""
        found = self._find_instance(vmid)
        if found is None:
            logger.info("Instance %s not found; treating delete as no-op", vmid)
            return
        node, res, res_type = found
        vmid_raw = res.get("vmid")
        vmid_int = int(vmid_raw) if vmid_raw is not None else 0

        if res_type == "lxc":
            if res.get("status") == "running":
                logger.info("Stopping LXC container %d before deletion", vmid_int)
                try:
                    upid = self._prox.nodes(node).lxc(vmid_int).status.stop.post()
                    self._wait_task(node, upid, timeout=120)
                except Exception as exc:
                    logger.warning(
                        "Failed to stop LXC %d: %s; proceeding to delete", vmid_int, exc
                    )
            upid = self._prox.nodes(node).lxc(vmid_int).delete()
            self._wait_task(node, upid)
            logger.info("LXC container %d deleted", vmid_int)
            return

        # QEMU path
        if res.get("status") == "running":
            logger.info("Stopping VM %d before deletion", vmid_int)
            try:
                upid = self._prox.nodes(node).qemu(vmid_int).status.stop.post()
                self._wait_task(node, upid, timeout=120)
            except Exception as exc:
                logger.warning(
                    "Failed to stop VM %d: %s; proceeding to delete", vmid_int, exc
                )

        upid = (
            self._prox.nodes(node)
            .qemu(vmid_int)
            .delete(purge=1, **{"destroy-unreferenced-disks": 1})
        )
        self._wait_task(node, upid)
        logger.info("VM %d deleted", vmid_int)

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
        """Power on instance *vmid*."""
        found = self._find_instance(vmid)
        if found is None:
            raise RuntimeError(f"Instance {vmid} not found")
        node, res, res_type = found
        vmid_raw = res.get("vmid")
        vmid_int = int(vmid_raw) if vmid_raw is not None else 0
        if res_type == "lxc":
            upid = self._prox.nodes(node).lxc(vmid_int).status.start.post()
        else:
            upid = self._prox.nodes(node).qemu(vmid_int).status.start.post()
        self._wait_task(node, upid)
        return self.get_instance(vmid)

    def stop_instance(self, vmid: str | int) -> Instance:
        """ACPI shutdown of instance *vmid*."""
        found = self._find_instance(vmid)
        if found is None:
            raise RuntimeError(f"Instance {vmid} not found")
        node, res, res_type = found
        vmid_raw = res.get("vmid")
        vmid_int = int(vmid_raw) if vmid_raw is not None else 0
        if res_type == "lxc":
            upid = self._prox.nodes(node).lxc(vmid_int).status.shutdown.post()
        else:
            upid = self._prox.nodes(node).qemu(vmid_int).status.shutdown.post()
        self._wait_task(node, upid, timeout=120)
        return self.get_instance(vmid)

    def remove_all_instances(self, controller_id: str) -> None:
        """Delete all instances (VMs and containers) tagged with *controller_id*."""
        try:
            resources = self._prox.cluster.resources.get(type="vm") or []
        except Exception as exc:
            raise RuntimeError(f"Failed to list instances: {exc}") from exc

        for res in resources:
            vmid = res.get("vmid")
            node = res.get("node", self._defaults.node)
            res_type = res.get("type", "qemu")
            try:
                config = self._get_config_for(node, vmid, res_type)
            except Exception:
                continue
            if not config:
                continue
            meta = _parse_garm_meta(config.get("description"))
            if meta and meta.get("garm_controller_id") == controller_id:
                logger.info("RemoveAll: deleting instance %d (type=%s)", vmid, res_type)
                try:
                    self.delete_instance(vmid)
                except Exception as exc:
                    logger.error("Failed to delete instance %d: %s", vmid, exc)
