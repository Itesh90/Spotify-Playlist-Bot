"""
backend/docker_manager.py
──────────────────────────
Docker SDK integration for the Spotify Playlist Bot Orchestrator.

Responsibilities:
  - Spawn a worker container per Spotify account (start_worker)
  - Stop and remove a worker container (stop_worker)
  - Check if a worker is currently running (is_running)
  - Read recent logs from a worker container (get_logs)
  - Run a worker in INTERACTIVE mode with noVNC for browser login (setup_login)
  - Dynamic VNC port allocation (6081-6200 range)

Environment variables read:
  WORKER_IMAGE  → Docker image for the worker (default: spb_worker:latest)
  DATA_DIR      → Host path for account data (mounted into containers)
"""

import os
import logging
import docker
from docker.errors import NotFound, APIError

log = logging.getLogger(__name__)

WORKER_IMAGE = os.environ.get("WORKER_IMAGE", "spb_worker:latest")
# Host-side storage root — this must match what docker-compose mounts
HOST_STORAGE = os.environ.get("HOST_STORAGE_PATH", os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "storage", "accounts"
))

# ─── VNC Port Management ─────────────────────────────────────────────────────

_VNC_PORT_RANGE = range(6081, 6200)
_active_vnc_ports: dict[str, int] = {}  # account_id → assigned host port

# ─── Docker client ────────────────────────────────────────────────────────────

def _client() -> docker.DockerClient:
    """Return a Docker client connected via the Unix socket."""
    return docker.from_env()


def _container_name(account_id: str) -> str:
    return f"spb_worker_{account_id}"


def _get_free_vnc_port() -> int | None:
    """Find the first unused port in the VNC range (6081-6200)."""
    try:
        client = _client()
        # Check which ports are already bound by running containers
        used_ports = set()
        for container in client.containers.list(all=True):
            ports = container.attrs.get("NetworkSettings", {}).get("Ports", {}) or {}
            for bindings in ports.values():
                if bindings:
                    for b in bindings:
                        try:
                            used_ports.add(int(b["HostPort"]))
                        except (KeyError, ValueError, TypeError):
                            pass
        # Also include ports tracked in memory
        used_ports.update(_active_vnc_ports.values())

        for port in _VNC_PORT_RANGE:
            if port not in used_ports:
                return port
        return None
    except Exception as e:
        log.warning(f"Port scan error: {e}")
        return 6081  # Fallback to first port


# ─── Public API ───────────────────────────────────────────────────────────────

def is_running(account_id: str) -> bool:
    """True if the worker container for this account is up and running."""
    try:
        c = _client().containers.get(_container_name(account_id))
        return c.status == "running"
    except NotFound:
        return False
    except APIError as e:
        log.warning(f"Docker API error checking {account_id}: {e}")
        return False


def start_worker(account_id: str, proxy_url: str = "") -> str | None:
    """
    Spawn an isolated headless worker container for the given account.
    Returns None on success, or an error string on failure.

    The container mounts a host directory for persistent session/log storage:
      {HOST_STORAGE}/{account_id}/ → /app/data (inside container)
    """
    if is_running(account_id):
        return "Worker is already running"

    host_data_dir = os.path.join(HOST_STORAGE, account_id)
    os.makedirs(host_data_dir, exist_ok=True)

    env = {
        "ACCOUNT_ID": account_id,
        "INTERACTIVE": "0",
    }
    if proxy_url:
        env["PROXY_URL"] = proxy_url

    try:
        client = _client()
        client.containers.run(
            image=WORKER_IMAGE,
            name=_container_name(account_id),
            detach=True,
            remove=True,                        # auto-remove on exit
            environment=env,
            volumes={
                host_data_dir: {
                    "bind": "/app/data",
                    "mode": "rw",
                }
            },
            network="spb_net",
            # Security: drop all caps, add only what's needed
            cap_drop=["ALL"],
            # WireGuard needs NET_ADMIN if VPN is used; safe to add conditionally
            # cap_add=["NET_ADMIN"],
            mem_limit="512m",
            cpu_quota=100_000,                  # 1 CPU core max
        )
        log.info(f"Worker started: {_container_name(account_id)}")
        return None
    except APIError as e:
        log.error(f"Failed to start worker {account_id}: {e}")
        return str(e)


def stop_worker(account_id: str) -> str | None:
    """
    Stop and remove the worker container for the given account.
    Returns None on success, error string on failure.
    """
    try:
        c = _client().containers.get(_container_name(account_id))
        c.stop(timeout=10)
        log.info(f"Worker stopped: {_container_name(account_id)}")
        # Clean up VNC port tracking
        _active_vnc_ports.pop(account_id, None)
        return None
    except NotFound:
        _active_vnc_ports.pop(account_id, None)
        return None              # Already gone — not an error
    except APIError as e:
        log.error(f"Failed to stop worker {account_id}: {e}")
        return str(e)

    # Also try to stop any setup container
    try:
        c = _client().containers.get(_container_name(account_id) + "_setup")
        c.stop(timeout=10)
    except (NotFound, APIError):
        pass


def get_logs(account_id: str, tail: int = 50) -> list[str]:
    """
    Return the last `tail` lines of stdout/stderr from the worker container.
    Returns an empty list if the container is not found.
    """
    try:
        c = _client().containers.get(_container_name(account_id))
        raw = c.logs(tail=tail, timestamps=True).decode("utf-8", errors="replace")
        return [line for line in raw.splitlines() if line.strip()]
    except NotFound:
        # Also try setup container
        try:
            c = _client().containers.get(_container_name(account_id) + "_setup")
            raw = c.logs(tail=tail, timestamps=True).decode("utf-8", errors="replace")
            return [line for line in raw.splitlines() if line.strip()]
        except (NotFound, APIError):
            return []
    except APIError as e:
        log.warning(f"Log fetch error {account_id}: {e}")
        return []


def setup_login(account_id: str, proxy_url: str = "") -> tuple[str | None, int | None]:
    """
    Start a worker container in INTERACTIVE mode with noVNC.
    Assigns a dynamic VNC port from the 6081-6200 range.

    Returns (error_string, vnc_port) tuple.
    error_string is None on success.
    """
    host_data_dir = os.path.join(HOST_STORAGE, account_id)
    os.makedirs(host_data_dir, exist_ok=True)

    # Assign a free VNC port
    vnc_port = _get_free_vnc_port()
    if vnc_port is None:
        return ("No free VNC ports available (all 6081-6200 in use)", None)

    env = {
        "ACCOUNT_ID": account_id,
        "INTERACTIVE": "1",
    }
    if proxy_url:
        env["PROXY_URL"] = proxy_url

    try:
        client = _client()
        # Stop any existing worker or setup container first
        stop_worker(account_id)

        container_name = _container_name(account_id) + "_setup"

        # Also stop any previous setup container with same name
        try:
            old = client.containers.get(container_name)
            old.stop(timeout=5)
        except (NotFound, APIError):
            pass

        client.containers.run(
            image=WORKER_IMAGE,
            name=container_name,
            detach=True,
            remove=True,
            environment=env,
            volumes={
                host_data_dir: {
                    "bind": "/app/data",
                    "mode": "rw",
                }
            },
            ports={"6080/tcp": vnc_port},        # noVNC WebSocket port
            network="spb_net",
            mem_limit="1g",
        )

        _active_vnc_ports[account_id] = vnc_port
        log.info(f"Setup container started for account: {account_id} (VNC port: {vnc_port})")
        return (None, vnc_port)
    except APIError as e:
        log.error(f"Failed to start setup container {account_id}: {e}")
        return (str(e), None)


def get_setup_status(account_id: str) -> str:
    """
    Check if the interactive setup has completed for this account.
    Returns: 'done', 'running', or 'not_started'
    """
    done_flag = os.path.join(HOST_STORAGE, account_id, ".setup_done")
    session_file = os.path.join(HOST_STORAGE, account_id, "session.json")

    if os.path.exists(done_flag):
        return "done"
    elif os.path.exists(session_file):
        return "ready"

    # Check if setup container is running
    try:
        c = _client().containers.get(_container_name(account_id) + "_setup")
        if c.status == "running":
            return "running"
    except (NotFound, APIError):
        pass

    return "not_started"


def get_spotify_username(account_id: str) -> str | None:
    """Read the captured Spotify username from the worker's data directory."""
    user_file = os.path.join(HOST_STORAGE, account_id, "spotify_user.txt")
    if os.path.exists(user_file):
        with open(user_file, "r") as f:
            return f.read().strip()
    return None


def get_all_worker_statuses() -> dict[str, str]:
    """
    Returns a dict of {account_id: container_status} for all spb_worker_* containers.
    Useful for the dashboard's real-time fleet view.
    """
    try:
        client = _client()
        all_containers = client.containers.list(all=True, filters={"name": "spb_worker_"})
        result = {}
        for c in all_containers:
            # Container name is /spb_worker_{account_id}
            raw_name = c.name.lstrip("/")
            if raw_name.startswith("spb_worker_"):
                acc_id = raw_name.removeprefix("spb_worker_")
                # Skip setup containers — they have _setup suffix
                if acc_id.endswith("_setup"):
                    continue
                result[acc_id] = c.status
        return result
    except APIError as e:
        log.warning(f"Failed to list containers: {e}")
        return {}

