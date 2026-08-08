"""P07: egress-allowlist proxy sidecar for sandboxed task execution.

Default-deny sandboxing (`--network none`) needs no infrastructure and
is handled directly in `container_runner.py`. This module handles the
harder half of SRS-001 FR-3: letting a sandbox reach a small, explicit
set of allowlisted domains and nothing else.

Topology (see ADR-004): the sandbox joins an `--internal` Docker
network (no route to the outside world) that only the Squid sidecar
also joins, in addition to Squid's normal external-facing network.
The sandbox's HTTP_PROXY/HTTPS_PROXY point at Squid; Squid's ACL
enforces which domains are actually allowed through.
"""
import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

INTERNAL_NETWORK = "orchid-sandbox-internal"
PROXY_CONTAINER = "orchid-sandbox-egress-proxy"
PROXY_IMAGE = "ubuntu/squid:latest"
PROXY_PORT = 3128

_STATE_DIR = Path.home() / ".orchid" / "sandbox_egress"
_CONF_TEMPLATE = Path(__file__).parent / "sandbox_egress_squid.conf.template"


class EgressProxyError(Exception):
    pass


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    logger.debug("sandbox_egress: %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def _write_allowlist(domains: list[str]) -> Path:
    """Write the Squid dstdomain allowlist file. A leading '.' makes an
    entry match the domain and all its subdomains (Squid convention)."""
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    allowlist_path = _STATE_DIR / "allowlist.txt"
    lines = [d if d.startswith(".") else f".{d}" for d in domains]
    allowlist_path.write_text("\n".join(lines) + "\n" if lines else "")
    return allowlist_path


def _ensure_conf() -> Path:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    conf_path = _STATE_DIR / "squid.conf"
    conf_path.write_text(_CONF_TEMPLATE.read_text())
    return conf_path


def _network_exists(name: str) -> bool:
    result = _run(["docker", "network", "inspect", name])
    return result.returncode == 0


def _ensure_internal_network() -> None:
    if _network_exists(INTERNAL_NETWORK):
        return
    result = _run(["docker", "network", "create", "--internal", INTERNAL_NETWORK])
    if result.returncode != 0:
        raise EgressProxyError(f"failed to create internal network: {result.stderr}")


def _container_running(name: str) -> bool:
    result = _run(["docker", "inspect", "-f", "{{.State.Running}}", name])
    return result.returncode == 0 and result.stdout.strip() == "true"


def _container_exists(name: str) -> bool:
    result = _run(["docker", "inspect", name])
    return result.returncode == 0


def _proxy_ip_on_internal_network() -> str:
    """Return the proxy container's IP on INTERNAL_NETWORK.

    Used instead of the container name: runsc's netstack cannot resolve
    names via Docker's embedded DNS server on a user-defined bridge
    network (confirmed empirically — `socket.gaierror: [Errno -3]
    Temporary failure in name resolution` under --runtime=runsc, while
    the identical lookup succeeds under runc). Raw IP addressing sidesteps
    the DNS path entirely and works under both runtimes. A real gVisor
    limitation, not a bug in this code — see findings.md.
    """
    fmt = f'{{{{index .NetworkSettings.Networks "{INTERNAL_NETWORK}" "IPAddress"}}}}'
    result = _run(["docker", "inspect", "-f", fmt, PROXY_CONTAINER])
    ip = result.stdout.strip()
    if result.returncode != 0 or not ip:
        raise EgressProxyError(f"could not determine proxy IP on {INTERNAL_NETWORK}: {result.stderr}")
    return ip


def _start_proxy_container(conf_path: Path, allowlist_path: Path) -> None:
    if _container_exists(PROXY_CONTAINER):
        _run(["docker", "rm", "-f", PROXY_CONTAINER])

    result = _run([
        "docker", "run", "-d",
        "--name", PROXY_CONTAINER,
        "-v", f"{conf_path}:/etc/squid/squid.conf:ro",
        "-v", f"{allowlist_path}:/etc/squid/allowlist.txt:ro",
        PROXY_IMAGE,
    ])
    if result.returncode != 0:
        raise EgressProxyError(f"failed to start proxy container: {result.stderr}")

    # Dual-home: started on the default bridge (external access), now
    # also join the internal-only network so sandboxes can reach it.
    result = _run(["docker", "network", "connect", INTERNAL_NETWORK, PROXY_CONTAINER])
    if result.returncode != 0:
        raise EgressProxyError(f"failed to attach proxy to internal network: {result.stderr}")


def ensure_egress_proxy(domains: list[str]) -> str:
    """Ensure the Squid sidecar is running with the given allowlist.

    Idempotent: if the allowlist changed since the last call, the proxy
    container is recreated with the new config (Squid config isn't
    hot-reloaded here — simpler and cheap enough for this use case).
    Returns an http://<ip>:<port> URL to use as HTTP(S)_PROXY — the IP,
    not the container name, because runsc can't resolve Docker's
    embedded DNS on a user-defined network (see `_proxy_ip_on_internal_network`).
    """
    if shutil.which("docker") is None:
        raise EgressProxyError("docker is not available")

    _ensure_internal_network()
    allowlist_path = _write_allowlist(domains)
    conf_path = _ensure_conf()

    if not _container_running(PROXY_CONTAINER):
        _start_proxy_container(conf_path, allowlist_path)
    else:
        # Allowlist file just changed on disk (bind-mounted); Squid
        # doesn't pick that up on its own, so trigger a hot reconfigure
        # rather than a full restart.
        result = _run(["docker", "exec", PROXY_CONTAINER, "squid", "-k", "reconfigure"])
        if result.returncode != 0:
            logger.warning("squid reconfigure failed, restarting proxy container instead: %s", result.stderr)
            _run(["docker", "restart", PROXY_CONTAINER])

    return f"http://{_proxy_ip_on_internal_network()}:{PROXY_PORT}"


def teardown_egress_proxy() -> None:
    """Remove the proxy container and internal network (test/demo cleanup)."""
    _run(["docker", "rm", "-f", PROXY_CONTAINER])
    _run(["docker", "network", "rm", INTERNAL_NETWORK])
