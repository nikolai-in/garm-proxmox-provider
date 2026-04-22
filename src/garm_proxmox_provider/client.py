"""Proxmox VE client wrapper using proxmoxer."""

from __future__ import annotations

import base64
import json
import logging
import re
import threading
import time
import urllib.parse
from typing import TYPE_CHECKING, Any, Callable

import urllib3
from proxmoxer import ProxmoxAPI

from .models import Address, Instance, InstanceStatus

if TYPE_CHECKING:
    from .config import Config

logger = logging.getLogger(__name__)

# JSON key written into the VM description field to mark GARM-managed VMs.
_GARM_META_MARKER = "__garm__"

# QGA readiness poll parameters
_QGA_PING_ATTEMPTS = 30
_QGA_PING_INTERVAL = 2


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
        if not cfg.pve.verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        parsed = (
            urllib.parse.urlparse(cfg.pve.host)
            if "://" in cfg.pve.host
            else urllib.parse.urlparse(f"https://{cfg.pve.host}")
        )
        pve_host = parsed.hostname or cfg.pve.host
        pve_port = parsed.port or 8006

        self._prox = ProxmoxAPI(
            pve_host,
            port=pve_port,
            user=cfg.pve.user,
            token_name=cfg.pve.token_name,
            token_value=cfg.pve.token_value,
            verify_ssl=cfg.pve.verify_ssl,
            service="PVE",
        )
        self._config = cfg

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
        except ValueError, TypeError:
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
                node = res.get("node", self._config.cluster.node)
                res_type = res.get("type", "qemu")
                return node, res, res_type
        return None

    def _find_template_by_name(
        self, name: str, res_type: str
    ) -> tuple[str, dict[str, Any], str] | None:
        """Return (node, resource_dict, res_type) for a named template of *res_type*.

        Searches the cluster for a resource whose ``name`` matches *name*,
        whose ``type`` is *res_type* (``"qemu"`` or ``"lxc"``), and whose
        ``template`` flag is set to ``1``.
        """
        try:
            resources = self._prox.cluster.resources.get(type="vm") or []
        except Exception as exc:
            logger.warning("Failed to query cluster resources: %s", exc)
            return None
        for res in resources:
            if not res:
                continue
            if res.get("name") != name:
                continue
            if res.get("type") != res_type:
                continue
            if int(res.get("template", 0)) != 1:
                continue
            node = res.get("node", self._config.cluster.node)
            return node, res, res_type
        return None

    def _vm_config(self, node: str, vmid: int) -> dict[str, Any]:
        return self._prox.nodes(node).qemu(vmid).config.get() or {}

    def _get_ips(self, node: str, vmid: int) -> list[Address]:
        """Try to fetch IPs via QEMU guest agent and fallback to config."""
        addresses: list[Address] = []
        try:
            config = self._vm_config(node, vmid)
            for k, v in config.items():
                if k.startswith("ipconfig") or k.startswith("net"):
                    if not isinstance(v, str):
                        continue
                    m4 = re.search(r"ip=([0-9\.]+)(?:/[0-9]+)?", v)
                    if m4 and m4.group(1) != "dhcp":
                        addresses.append(Address(address=m4.group(1), type="ipv4"))
                    m6 = re.search(r"ip6=([a-fA-F0-9:]+)(?:/[0-9]+)?", v)
                    if m6 and m6.group(1) not in ("dhcp", "auto"):
                        addresses.append(Address(address=m6.group(1), type="ipv6"))
        except Exception:
            pass
        if addresses:
            return addresses
        try:
            result = (
                self._prox.nodes(node).qemu(vmid).agent.get("network-get-interfaces")
            )
            addresses = []
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
        """Fetch IPs from an LXC container's network interfaces and config."""
        addresses: list[Address] = []
        try:
            config = self._lxc_config(node, vmid)
            for k, v in config.items():
                if k.startswith("net"):
                    if not isinstance(v, str):
                        continue
                    m4 = re.search(r"ip=([0-9\.]+)(?:/[0-9]+)?", v)
                    if m4 and m4.group(1) != "dhcp":
                        addresses.append(Address(address=m4.group(1), type="ipv4"))
                    m6 = re.search(r"ip6=([a-fA-F0-9:]+)(?:/[0-9]+)?", v)
                    if m6 and m6.group(1) not in ("dhcp", "auto"):
                        addresses.append(Address(address=m6.group(1), type="ipv6"))
        except Exception:
            pass
        if addresses:
            return addresses
        try:
            result = self._prox.nodes(node).lxc(vmid).interfaces.get() or []
            addresses = []
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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_instances(self, pool_id: str) -> list[Instance]:
        """Return all GARM instances belonging to *pool_id* (VMs and containers)."""
        logger.debug("Listing instances for pool %s", pool_id)
        try:
            resources = self._prox.cluster.resources.get(type="vm") or []
        except Exception as exc:
            logger.error("Failed to list instances from Proxmox cluster: %s", exc)
            raise RuntimeError(f"Failed to list instances: {exc}") from exc

        instances: list[Instance] = []
        for res in resources:
            vmid = res.get("vmid")
            node = res.get("node", self._config.cluster.node)
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
        provider_type: str = "vm",
        userdata: str = "",
        os_type: str = "linux",
        os_arch: str = "amd64",
        *,
        cores: int | None = None,
        memory_mb: int | None = None,
        node: str | None = None,
        image: str = "",
        lxc_unprivileged: bool = True,
        userdata_factory: Callable[[str], str] | None = None,
    ) -> Instance:
        """Clone template, configure and start instance; return Instance.

        *provider_type* must be ``"vm"`` (QEMU) or ``"lxc"``.  The template is
        looked up by *image* name across the cluster; it must have ``template=1``
        and a type matching *provider_type*.

        For QEMU VMs the bootstrap script is injected via QEMU Guest Agent.
        For LXC containers it is executed via the Proxmox ``pct exec`` API.
        """
        res_type = "lxc" if provider_type == "lxc" else "qemu"

        node = node or self._config.cluster.node
        if cores is None or memory_mb is None:
            def_flavor = self._config.get_flavor("default")
            cores = cores or def_flavor.cores
            memory_mb = memory_mb or def_flavor.memory_mb

        garm_meta = _build_garm_meta(controller_id, pool_id, name, os_type, os_arch)

        found_tmpl = self._find_template_by_name(image, res_type)
        if found_tmpl is None:
            raise RuntimeError(
                f"Template '{image}' (type={res_type}) not found in cluster. "
                "Ensure a template with that name and template=1 exists."
            )
        _, res_tmpl, _ = found_tmpl
        real_tmpl_vmid = int(res_tmpl.get("vmid", 0))

        for attempt in range(5):
            vmid = self._next_vmid()
            try:
                if provider_type == "lxc":
                    return self._create_lxc(
                        vmid=vmid,
                        tmpl_vmid=real_tmpl_vmid,
                        name=name,
                        pool_id=pool_id,
                        garm_meta=garm_meta,
                        userdata=userdata_factory(str(vmid))
                        if userdata_factory
                        else userdata,
                        os_type=os_type,
                        os_arch=os_arch,
                        cores=cores,
                        memory_mb=memory_mb,
                        node=node,
                        lxc_unprivileged=lxc_unprivileged,
                    )

                return self._create_qemu(
                    vmid=vmid,
                    tmpl_vmid=real_tmpl_vmid,
                    name=name,
                    pool_id=pool_id,
                    garm_meta=garm_meta,
                    userdata=userdata_factory(str(vmid))
                    if userdata_factory
                    else userdata,
                    os_type=os_type,
                    os_arch=os_arch,
                    cores=cores,
                    memory_mb=memory_mb,
                    node=node,
                )
            except Exception as exc:
                if "File exists" in str(exc) and attempt < 4:
                    logger.warning("VMID collision (likely %d), retrying...", vmid)
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
        d = self._config.cluster
        logger.info("Cloning QEMU template %d -> VMID %d (%s)", tmpl_vmid, vmid, name)

        upid = (
            self._prox.nodes(node)
            .qemu(tmpl_vmid)
            .clone.post(
                newid=vmid,
                name=name,
                full=0,
                **({"pool": d.pool} if d.pool else {}),
            )
        )
        self._wait_task(node, upid)

        config_update: dict[str, Any] = {
            "cores": cores,
            "memory": memory_mb,
            "description": garm_meta,
            "ipconfig0": "ip=dhcp",
            "net0": f"virtio,bridge={d.bridge}",
            "agent": 1,
        }
        if os_type.lower() == "linux":
            config_update["ciuser"] = "runner"
            config_update["cipassword"] = "runner"
        if d.ssh_public_key:
            from urllib.parse import quote

            config_update["sshkeys"] = quote(d.ssh_public_key.strip(), safe="")

        self._prox.nodes(node).qemu(vmid).config.post(**config_update)

        logger.info("Starting QEMU VM %d", vmid)
        upid = self._prox.nodes(node).qemu(vmid).status.start.post()
        if userdata:
            self._run_userdata_qemu(node, vmid, userdata, os_type)

        # Try to collect IP addresses immediately so GARM sees them without waiting
        try:
            addresses = self._get_ips_for(node, vmid, "qemu")
        except Exception:
            addresses = []

        logger.info("Successfully created QEMU VM %d (%s)", vmid, name)
        return Instance(
            provider_id=str(vmid),
            name=name,
            os_type=os_type,
            os_arch=os_arch,
            status=InstanceStatus.RUNNING,
            pool_id=pool_id,
            addresses=addresses,
        )

    def _create_lxc(
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
        lxc_unprivileged: bool,
    ) -> Instance:
        """Clone an LXC template, inject env vars, start and return Instance."""
        d = self._config.cluster
        logger.info("Cloning LXC template %d -> VMID %d (%s)", tmpl_vmid, vmid, name)

        upid = (
            self._prox.nodes(node)
            .lxc(tmpl_vmid)
            .clone.post(
                newid=vmid,
                hostname=name,
                full=0,
                **({"pool": d.pool} if d.pool else {}),
            )
        )
        self._wait_task(node, upid)

        config_update: dict[str, Any] = {
            "cores": cores,
            "memory": memory_mb,
            "description": garm_meta,
            "net0": f"name=eth0,bridge={d.bridge},ip=dhcp",
            "unprivileged": int(lxc_unprivileged),
        }

        self._prox.nodes(node).lxc(vmid).config.put(**config_update)

        logger.info("Starting LXC container %d", vmid)
        upid = self._prox.nodes(node).lxc(vmid).status.start.post()
        if userdata:
            logger.info("Executing userdata script in LXC %d via pct exec", vmid)
            # Use the Proxmox pct exec API to run the bootstrap script.
            try:
                self._prox.nodes(node).lxc(vmid).exec.post(
                    command=["/bin/bash", "-c", userdata]
                )
            except Exception as exc:
                logger.warning("Failed to execute userdata in LXC %d: %s", vmid, exc)

        # Try to collect IP addresses immediately so GARM sees them without waiting
        try:
            addresses = self._get_ips_for(node, vmid, "lxc")
        except Exception:
            addresses = []

        logger.info("Successfully created LXC container %d (%s)", vmid, name)
        return Instance(
            provider_id=str(vmid),
            name=name,
            os_type=os_type,
            os_arch=os_arch,
            status=InstanceStatus.RUNNING,
            pool_id=pool_id,
            addresses=addresses,
        )

    def _wait_for_qga(self, node: str, vmid: int) -> bool:
        """Poll QGA ping until ready; return True on success, False on timeout."""
        for attempt in range(1, _QGA_PING_ATTEMPTS + 1):
            logger.info(
                "Waiting for QEMU Guest Agent in VM %d (attempt %d/%d)",
                vmid,
                attempt,
                _QGA_PING_ATTEMPTS,
            )
            try:
                self._prox.nodes(node).qemu(vmid).agent.ping.post()
                logger.info("QEMU Guest Agent ready in VM %d", vmid)
                return True
            except Exception:
                time.sleep(_QGA_PING_INTERVAL)
        logger.warning(
            "QEMU Guest Agent not ready in VM %d after %d attempts",
            vmid,
            _QGA_PING_ATTEMPTS,
        )
        return False

    def _run_userdata_qemu(
        self, node: str, vmid: int, userdata: str, os_type: str
    ) -> None:
        """Execute *userdata* inside the VM via QEMU Guest Agent without blocking.

        Historically this function polled the QGA exec-status until completion,
        which caused heavy API traffic and long create delays when the agent
        returned transient errors or missing PIDs. To avoid spamming the PVE
        API and blocking the provider, we now start the guest exec and return
        immediately. The guest-side process continues inside the VM; any
        post-creation checks (IPs, status) are performed elsewhere.
        """
        logger.info("Executing userdata script via QEMU Guest Agent in VM %d", vmid)

        if not self._wait_for_qga(node, vmid):
            logger.error(
                "QEMU Guest Agent not ready in VM %d; runner bootstrap was skipped",
                vmid,
            )
            return

        try:
            if os_type.lower() == "windows":
                # Use PowerShell's -EncodedCommand to reliably pass multi-line scripts
                # and avoid quoting/escaping issues over QGA. -EncodedCommand expects
                # a base64-encoded UTF-16LE string.
                try:
                    encoded_cmd = base64.b64encode(
                        userdata.encode("utf-16-le")
                    ).decode()
                    cmd = [
                        "powershell.exe",
                        "-NonInteractive",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-EncodedCommand",
                        encoded_cmd,
                    ]
                except Exception:
                    # Fallback to -Command if encoding unexpectedly fails.
                    cmd = [
                        "powershell.exe",
                        "-NonInteractive",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-Command",
                        userdata,
                    ]
                res = self._prox.nodes(node).qemu(vmid).agent.exec.post(command=cmd)
            else:
                res = (
                    self._prox.nodes(node)
                    .qemu(vmid)
                    .agent.exec.post(command=["/bin/bash", "-c", userdata])
                )

            logger.debug("QGA exec returned: %s", res)

            pid = None
            if isinstance(res, dict):
                pid = res.get("pid")
                if pid is None:
                    ret = res.get("return")
                    if isinstance(ret, dict):
                        pid = ret.get("pid")

            if not pid:
                logger.warning(
                    "No pid returned from QGA exec for VM %d; unable to track status",
                    vmid,
                )
                # We don't block waiting for exec-status; just return and let other
                # parts of the system observe instance health later.
                return

            # Do NOT poll exec-status synchronously here. Polling creates high-frequency API calls
            # and can result in long delays if the QGA responds with transient errors.
            # Instead, start a background watcher thread that will attempt a few
            # post-facto checks and then return immediately so create() is not blocked.
            logger.info(
                "QGA exec launched in VM %d with pid=%r; starting background watcher",
                vmid,
                pid,
            )

            def _background_watch(node_arg: str, vmid_arg: int, pid_arg: int) -> None:
                try:
                    # Give the guest some time to start the process and produce status.
                    time.sleep(30)
                    # Poll a small number of times with backoff to avoid spamming PVE API.
                    poll_intervals = [1, 2, 4]
                    for interval in poll_intervals:
                        try:
                            status_resp = (
                                self._prox.nodes(node_arg)
                                .qemu(vmid_arg)
                                .agent.get("exec-status", pid=pid_arg)
                            )
                        except Exception as exc:
                            logger.debug(
                                "Background watcher: error querying exec-status for VM %d pid=%r: %s",
                                vmid_arg,
                                pid_arg,
                                exc,
                            )
                            time.sleep(interval)
                            continue

                        if isinstance(status_resp, dict):
                            info = status_resp.get("result", status_resp) or {}
                        else:
                            info = status_resp or {}
                            if not isinstance(info, dict):
                                info = {}

                        exited = bool(info.get("exited", False))
                        exitcode = None
                        if "exitcode" in info and info.get("exitcode") is not None:
                            exitcode = info.get("exitcode")
                        else:
                            exitcode = info.get("exit-code") or info.get("exit_code")

                        if exited or exitcode is not None:
                            out_b64 = (
                                info.get("out-data")
                                or info.get("out_data")
                                or info.get("out")
                            )
                            err_b64 = (
                                info.get("err-data")
                                or info.get("err_data")
                                or info.get("err")
                            )
                            out = ""
                            err = ""
                            try:
                                if out_b64:
                                    out = base64.b64decode(out_b64).decode(
                                        errors="replace"
                                    )
                                if err_b64:
                                    err = base64.b64decode(err_b64).decode(
                                        errors="replace"
                                    )
                            except Exception:
                                out = str(out_b64)
                                err = str(err_b64)

                            logger.info(
                                "Background QGA exec for VM %d finished: exitcode=%r stdout=%r stderr=%r",
                                vmid_arg,
                                exitcode,
                                out[:1000],
                                err[:1000],
                            )

                            if exitcode and int(exitcode) != 0:
                                logger.warning(
                                    "Background: userdata exited non-zero (%s) for VM %d",
                                    exitcode,
                                    vmid_arg,
                                )
                            return

                        time.sleep(interval)
                    # If we reach here, exec-status was not available or did not report completion.
                    logger.debug(
                        "Background watcher for VM %d pid=%r did not observe completion after retries",
                        vmid_arg,
                        pid_arg,
                    )
                except Exception as exc:
                    logger.debug(
                        "Background watcher for VM %d failed: %s", vmid_arg, exc
                    )

            try:
                watcher = threading.Thread(
                    target=_background_watch, args=(node, vmid, pid), daemon=True
                )
                watcher.start()
            except Exception as exc:
                logger.debug(
                    "Failed to start background watcher thread for VM %d: %s", vmid, exc
                )
            return

        except Exception as exc:
            logger.warning(
                "Failed to execute userdata via QGA for VM %d: %s", vmid, exc
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

        cluster_cfg = self._config.cluster
        if cluster_cfg.snippets_storage:
            snippet_name = f"garm-{vmid_int}.yml"
            try:
                self._prox.nodes(node).storage(cluster_cfg.snippets_storage).content(
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
            node = res.get("node", self._config.cluster.node)
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
