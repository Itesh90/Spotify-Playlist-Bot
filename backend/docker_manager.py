"""
backend/docker_manager.py
──────────────────────────
Docker SDK integration for the Spotify Playlist Bot Orchestrator.

Responsibilities:
  - Spawn a worker container per Spotify account (start_worker)
  - Stop and remove a worker container (stop_worker)
  - Check if a worker is currently running (is_running)
  - Read recent logs from a worker container (get_logs)
  - Run a worker in INTERACTIVE mode for one-time login setup (setup_login)

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

# ─── Docker client ────────────────────────────────────────────────────────────

def _client() -> docker.DockerClient:
    """Return a Docker client connected via the Unix socket."""
    return docker.from_env()


def _container_name(account_id: str) -> str:
    return f"spb_worker_{account_id}"


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
        return None
    except NotFound:
        return None              # Already gone — not an error
    except APIError as e:
        log.error(f"Failed to stop worker {account_id}: {e}")
        return str(e)


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
        return []
    except APIError as e:
        log.warning(f"Log fetch error {account_id}: {e}")
        return []


def setup_login(account_id: str, proxy_url: str = "") -> str | None:
    """
    Start a worker container in INTERACTIVE mode (headless=False).
    Used once per account to perform manual Spotify login and save session.json.
    
    NOTE: This mode requires a VNC/display server or X forwarding to see the
    browser window. In the Dashboard, this will be presented as a guided flow.
    Returns None on success, error string on failure.
    """
    host_data_dir = os.path.join(HOST_STORAGE, account_id)
    os.makedirs(host_data_dir, exist_ok=True)

    env = {
        "ACCOUNT_ID": account_id,
        "INTERACTIVE": "1",
    }
    if proxy_url:
        env["PROXY_URL"] = proxy_url

    try:
        client = _client()
        # Stop any existing headless worker first
        stop_worker(account_id)

        client.containers.run(
            image=WORKER_IMAGE,
            name=_container_name(account_id) + "_setup",
            detach=True,
            remove=True,
            environment=env,
            volumes={
                host_data_dir: {
                    "bind": "/app/data",
                    "mode": "rw",
                }
            },
            network="spb_net",
            mem_limit="1g",
        )
        log.info(f"Setup container started for account: {account_id}")
        return None
    except APIError as e:
        log.error(f"Failed to start setup container {account_id}: {e}")
        return str(e)


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
                result[acc_id] = c.status
        return result
    except APIError as e:
        log.warning(f"Failed to list containers: {e}")
        return {}
