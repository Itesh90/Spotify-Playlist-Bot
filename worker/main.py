import os
import sys
import json
import time
import signal
import logging
import subprocess

from playwright.sync_api import sync_playwright, Browser, BrowserContext

# ─── Config ───────────────────────────────────────────────────────────────────

ACCOUNT_ID    = os.environ.get("ACCOUNT_ID", "default")
INTERACTIVE   = os.environ.get("INTERACTIVE", "0") == "1"
PROXY_URL     = os.environ.get("PROXY_URL", "")           # e.g. http://user:pass@host:port
VNC_PORT      = int(os.environ.get("VNC_PORT", "6080"))    # websockify listens on this (host network)
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
        # Must pass args for Docker compatibility (no-sandbox etc.)
        firefox_kwargs = {"headless": False}
        if proxy:
            firefox_kwargs["proxy"] = proxy
        # Firefox uses MOZ_ env vars for some settings
        browser = playwright.firefox.launch(**firefox_kwargs)
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


# ─── VNC Services (Interactive Mode Only) ─────────────────────────────────────

def _start_vnc_services() -> list[subprocess.Popen]:
    """
    Starts Xvfb (virtual display), x11vnc (VNC server), and
    websockify (WebSocket→VNC bridge for noVNC).
    Returns subprocess handles for cleanup.
    """
    procs = []

    # 1. Start Xvfb on display :99
    xvfb = subprocess.Popen(
        ["Xvfb", ":99", "-screen", "0", "1280x800x24"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    )
    procs.append(xvfb)
    os.environ["DISPLAY"] = ":99"
    time.sleep(2)  # Wait for Xvfb to fully initialize (Codespaces can be slow)
    # Verify Xvfb is still alive
    if xvfb.poll() is not None:
        stderr = xvfb.stderr.read().decode() if xvfb.stderr else ""
        log.error(f"Worker {ACCOUNT_ID}: Xvfb CRASHED on startup! stderr: {stderr}")
        return procs
    log.info(f"Worker {ACCOUNT_ID}: Xvfb started on :99")

    # 2. Start x11vnc — captures the Xvfb display
    #    With host networking, bind to localhost only (websockify connects locally).
    #    Binding to 0.0.0.0 would expose raw VNC to the entire VM.
    x11vnc = subprocess.Popen(
        ["x11vnc", "-display", ":99", "-nopw", "-localhost",
         "-xkb", "-forever", "-quiet", "-rfbport", "5900"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    )
    procs.append(x11vnc)
    time.sleep(1)
    if x11vnc.poll() is not None:
        stderr = x11vnc.stderr.read().decode() if x11vnc.stderr else ""
        log.error(f"Worker {ACCOUNT_ID}: x11vnc CRASHED! stderr: {stderr}")
        return procs
    log.info(f"Worker {ACCOUNT_ID}: x11vnc started on port 5900")

    # 3. Start websockify — bridges WebSocket (VNC_PORT) → VNC (5900)
    #    With host networking, websockify binds directly to the VM port.
    #    noVNC downloaded from GitHub to /opt/novnc in the Dockerfile.
    #    --heartbeat=30 keeps the WebSocket alive through Codespaces proxy.
    novnc_web = "/opt/novnc"
    websockify = subprocess.Popen(
        ["websockify", f"0.0.0.0:{VNC_PORT}", "localhost:5900",
         "--web", novnc_web, "--heartbeat=30"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    )
    procs.append(websockify)
    time.sleep(2)  # Give websockify time to bind and accept connections
    if websockify.poll() is not None:
        stderr = websockify.stderr.read().decode() if websockify.stderr else ""
        log.error(f"Worker {ACCOUNT_ID}: websockify CRASHED! stderr: {stderr}")
        return procs
    log.info(f"Worker {ACCOUNT_ID}: websockify/noVNC started on port {VNC_PORT} (web={novnc_web})")

    return procs


def _stop_vnc_services(procs: list[subprocess.Popen]):
    """Kill all VNC-related subprocesses."""
    for p in procs:
        try:
            p.terminate()
            p.wait(timeout=5)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass


# ─── Interactive Setup Mode ───────────────────────────────────────────────────

def run_interactive_setup():
    """
    Opens Spotify Web Player in a visible browser via noVNC.
    The user logs in through the dashboard's embedded iframe.
    Auto-detects login completion, saves session, and exits.
    """
    log.info(f"Worker {ACCOUNT_ID}: ─── INTERACTIVE SETUP MODE (noVNC) ───")

    # Start VNC services so the browser is accessible via noVNC on port 6080
    vnc_procs = _start_vnc_services()

    try:
        with sync_playwright() as p:
            browser, context = _launch_browser(p, headless=False)
            page = context.new_page()

            # Navigate directly to Spotify login page.
            # open.spotify.com does NOT auto-redirect to login — it shows a "Log in" button.
            # Going to accounts.spotify.com directly shows the login form immediately.
            login_url = "https://accounts.spotify.com/login?continue=https%3A%2F%2Fopen.spotify.com%2F"
            log.info(f"Worker {ACCOUNT_ID}: Navigating to Spotify login page...")
            page.goto(login_url, timeout=30_000)
            log.info(f"Worker {ACCOUNT_ID}: Login page loaded. Waiting for user to log in...")

            # ── Auto-detect login via DOM polling ─────────────────────────
            # Instead of tracking URL transitions (fragile — Spotify doesn't
            # always redirect predictably), we check for logged-in UI elements
            # in the DOM. This works regardless of how the user navigates.
            spotify_user = None
            poll_count = 0
            max_polls = 300  # 10 minutes (300 × 2s)

            while not _shutdown and poll_count < max_polls:
                time.sleep(2)
                poll_count += 1

                try:
                    current_url = page.url

                    # Only check for login on the Spotify web player page
                    if "open.spotify.com" not in current_url:
                        if poll_count % 15 == 0:
                            log.info(f"Worker {ACCOUNT_ID}: On auth page, waiting... ({poll_count * 2}s)")
                        continue

                    # Check DOM for logged-in indicators
                    login_state = page.evaluate("""
                        () => {
                            const userWidget = document.querySelector('[data-testid="user-widget-link"]');
                            const profileBtn = document.querySelector('[data-testid="user-widget-button"]');
                            const loginBtn = document.querySelector('[data-testid="login-button"]');
                            const signupBtn = document.querySelector('[data-testid="signup-button"]');
                            
                            if ((userWidget || profileBtn) && !loginBtn) {
                                return {
                                    loggedIn: true,
                                    username: userWidget ? userWidget.textContent.trim() : null
                                };
                            }
                            return { loggedIn: false, username: null };
                        }
                    """)

                    if login_state and login_state.get("loggedIn"):
                        spotify_user = login_state.get("username")
                        log.info(f"Worker {ACCOUNT_ID}: ✅ Login detected! User: {spotify_user}")
                        time.sleep(2)  # Brief settle
                        break

                    # On open.spotify.com but not logged in yet (login button visible)
                    if poll_count % 15 == 0:
                        log.info(f"Worker {ACCOUNT_ID}: On Spotify but not logged in yet... ({poll_count * 2}s)")

                except Exception as e:
                    log.warning(f"Worker {ACCOUNT_ID}: Poll error: {e}")
                    continue

            # ── Save session ──────────────────────────────────────────────
            if _shutdown:
                log.info(f"Worker {ACCOUNT_ID}: Shutdown during setup — aborting.")
                browser.close()
                return

            if not spotify_user:
                log.warning(f"Worker {ACCOUNT_ID}: Login not detected after {max_polls * 2}s — session NOT saved.")
                browser.close()
                return

            # Save cookies & localStorage (only after confirmed login)
            context.storage_state(path=SESSION_FILE)
            log.info(f"Worker {ACCOUNT_ID}: Session saved → {SESSION_FILE}")

            log.info(f"Worker {ACCOUNT_ID}: Spotify user: {spotify_user}")
            user_file = os.path.join(DATA_DIR, "spotify_user.txt")
            with open(user_file, "w") as f:
                f.write(spotify_user)

            # Write the .setup_done flag so the backend knows we're finished
            done_flag = os.path.join(DATA_DIR, ".setup_done")
            with open(done_flag, "w") as f:
                f.write("done")
            log.info(f"Worker {ACCOUNT_ID}: Setup complete flag written.")

            # Keep VNC alive briefly so user sees success in the iframe
            time.sleep(5)

            browser.close()

    finally:
        _stop_vnc_services(vnc_procs)

    log.info(f"Worker {ACCOUNT_ID}: Interactive setup complete. Container exiting.")
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
