"""
worker/main.py
──────────────
Spotify Playlist Bot — Isolated Worker Node
Runs inside a Docker container per account.

Modes:
  INTERACTIVE=1  → Opens visible browser for one-time manual Spotify login.
                   Saves session state to /app/data/session.json on exit.
  Default        → Headless mode. Loads session.json, keeps browser open
                   as an "active device" while Flask orchestrator controls
                   playback via the Spotify Web API.

Environment variables (injected by orchestrator via docker run -e):
  ACCOUNT_ID     → Unique account slug (used for storage paths)
  INTERACTIVE    → Set to "1" for setup mode
  PROXY_URL      → Optional. Format: http://user:pass@host:port
"""

import os
import sys
import json
import time
import signal
import logging

from playwright.sync_api import sync_playwright, Browser, BrowserContext

# ─── Config ───────────────────────────────────────────────────────────────────

ACCOUNT_ID    = os.environ.get("ACCOUNT_ID", "default")
INTERACTIVE   = os.environ.get("INTERACTIVE", "0") == "1"
PROXY_URL     = os.environ.get("PROXY_URL", "")           # e.g. http://user:pass@host:port
DATA_DIR      = os.environ.get("DATA_DIR", "/app/data")
SESSION_FILE  = os.path.join(DATA_DIR, "session.json")
LOG_FILE      = os.path.join(DATA_DIR, "worker.log")

os.makedirs(DATA_DIR, exist_ok=True)

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),          # Docker captures stdout
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ─── Graceful Shutdown ────────────────────────────────────────────────────────

_shutdown = False

def _handle_signal(sig, frame):
    global _shutdown
    log.info(f"Worker {ACCOUNT_ID}: Shutdown signal received.")
    _shutdown = True

signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)

# ─── Browser Helpers ──────────────────────────────────────────────────────────

def _build_proxy_config() -> dict | None:
    """Returns a Playwright proxy dict if PROXY_URL is set, else None."""
    if not PROXY_URL:
        return None
    # Parse http://user:pass@host:port
    return {"server": PROXY_URL}


def _launch_browser(playwright, headless: bool) -> tuple[Browser, BrowserContext]:
    """Launch Chromium (or Firefox in interactive mode) with optional proxy."""
    proxy = _build_proxy_config()
    launch_kwargs = {
        "headless": headless,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    }
    if proxy:
        launch_kwargs["proxy"] = proxy

    if INTERACTIVE:
        # Firefox for interactive — better compatibility with Spotify login UI
        browser = playwright.firefox.launch(headless=False)
        log.info(f"Worker {ACCOUNT_ID}: Firefox launched in INTERACTIVE mode.")
    else:
        browser = playwright.chromium.launch(**launch_kwargs)
        log.info(f"Worker {ACCOUNT_ID}: Chromium launched in HEADLESS mode.")

    # Load saved session state if it exists
    context_kwargs = {
        "locale": "en-IN",
        "timezone_id": "Asia/Kolkata",
        "viewport": {"width": 1280, "height": 800},
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    }

    if os.path.exists(SESSION_FILE) and not INTERACTIVE:
        context_kwargs["storage_state"] = SESSION_FILE
        log.info(f"Worker {ACCOUNT_ID}: Loaded session from {SESSION_FILE}")
    elif not os.path.exists(SESSION_FILE) and not INTERACTIVE:
        log.warning(f"Worker {ACCOUNT_ID}: No session.json found — run INTERACTIVE=1 first.")

    if proxy:
        context_kwargs["proxy"] = proxy

    context = browser.new_context(**context_kwargs)

    # Evasion: hide navigator.webdriver
    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        window.chrome = { runtime: {} };
    """)

    return browser, context


# ─── Interactive Setup Mode ───────────────────────────────────────────────────

def run_interactive_setup():
    """
    Opens Spotify Web Player in a visible browser so the user can
    manually log in. After login, saves session state and exits.
    """
    log.info(f"Worker {ACCOUNT_ID}: ─── INTERACTIVE SETUP MODE ───")
    log.info("Open the browser, log in to Spotify, then press ENTER here to save and exit.")

    with sync_playwright() as p:
        browser, context = _launch_browser(p, headless=False)
        page = context.new_page()
        page.goto("https://open.spotify.com", timeout=30_000)
        log.info("Browser is open. Log in to Spotify now...")

        # Wait for user confirmation
        input("\n>>> Press ENTER after you have logged in to Spotify <<<\n")

        # Save cookies & localStorage
        context.storage_state(path=SESSION_FILE)
        log.info(f"Worker {ACCOUNT_ID}: Session saved → {SESSION_FILE}")

        browser.close()

    log.info(f"Worker {ACCOUNT_ID}: Setup complete. Restart without INTERACTIVE=1 for bot mode.")
    sys.exit(0)


# ─── Headless Bot Mode ────────────────────────────────────────────────────────

def run_headless():
    """
    Opens Spotify Web Player in headless Chromium using a saved session.
    Keeps the browser alive as an "Active Device" so the Flask orchestrator
    can target it via sp.start_playback(device_id=...).

    The actual playlist-switching logic is handled by the Flask API (app.py).
    This process just maintains the device presence.
    """
    log.info(f"Worker {ACCOUNT_ID}: ─── HEADLESS BOT MODE ───")

    if not os.path.exists(SESSION_FILE):
        log.error(
            f"Worker {ACCOUNT_ID}: Missing session.json. "
            "Run with INTERACTIVE=1 to set up this account first."
        )
        sys.exit(1)

    with sync_playwright() as p:
        browser, context = _launch_browser(p, headless=True)
        page = context.new_page()

        # Navigate to Spotify Web Player
        log.info(f"Worker {ACCOUNT_ID}: Navigating to open.spotify.com...")
        try:
            page.goto("https://open.spotify.com", timeout=30_000)
            page.wait_for_load_state("domcontentloaded", timeout=15_000)
            log.info(f"Worker {ACCOUNT_ID}: Spotify Web Player loaded. Device is now ACTIVE.")
        except Exception as e:
            log.error(f"Worker {ACCOUNT_ID}: Failed to load Spotify: {e}")
            browser.close()
            sys.exit(1)

        # ── Keep-alive loop ───────────────────────────────────────────────────
        # Every 60 seconds: check page is still alive (session not expired).
        # The Flask orchestrator handles all playback logic externally.
        heartbeat_interval = 60
        last_check = time.time()

        log.info(f"Worker {ACCOUNT_ID}: Entering keep-alive loop. Checking every {heartbeat_interval}s.")

        while not _shutdown:
            time.sleep(2)

            # Mainframe Live Feed: Capture screenshot every 2 seconds
            try:
                screen_path = os.path.join(DATA_DIR, "live.jpeg")
                page.screenshot(path=screen_path, type="jpeg", quality=40)
            except Exception as ex:
                pass

            if time.time() - last_check < heartbeat_interval:
                continue

            last_check = time.time()

            # Re-check the page is still valid (not logged out / crashed)
            try:
                current_url = page.url

                if "login" in current_url or "accounts.spotify.com" in current_url:
                    log.warning(f"Worker {ACCOUNT_ID}: Session expired — redirected to login. Saving new state...")
                    # Attempt to restore session
                    if os.path.exists(SESSION_FILE):
                        context.storage_state(path=SESSION_FILE + ".expired_bak")
                    log.error(f"Worker {ACCOUNT_ID}: Cannot recover session automatically. INTERACTIVE=1 required.")
                    browser.close()
                    sys.exit(2)
                else:
                    log.info(f"Worker {ACCOUNT_ID}: ♡ Heartbeat OK — {current_url[:60]}")
            except Exception as e:
                log.warning(f"Worker {ACCOUNT_ID}: Heartbeat error: {e}")

        # Graceful shutdown
        log.info(f"Worker {ACCOUNT_ID}: Shutdown — saving session state...")
        try:
            context.storage_state(path=SESSION_FILE)
        except Exception:
            pass
        browser.close()
        log.info(f"Worker {ACCOUNT_ID}: Browser closed cleanly.")


# ─── Entry Point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if INTERACTIVE:
        run_interactive_setup()
    else:
        run_headless()
