import os
import sys
import json
import time
import random
import signal
import logging
import subprocess
from datetime import datetime, timedelta

from playwright.sync_api import sync_playwright, Browser, BrowserContext

# ─── Config ───────────────────────────────────────────────────────────────────

ACCOUNT_ID    = os.environ.get("ACCOUNT_ID", "default")
INTERACTIVE   = os.environ.get("INTERACTIVE", "0") == "1"
PROXY_URL     = os.environ.get("PROXY_URL", "")           # e.g. http://user:pass@host:port
VNC_PORT      = int(os.environ.get("VNC_PORT", "6080"))    # websockify listens on this (host network)
DATA_DIR       = os.environ.get("DATA_DIR", "/app/data")
SESSION_FILE   = os.path.join(DATA_DIR, "session.json")
PLAYLIST_FILE  = os.path.join(DATA_DIR, "playlists.json")
LAST_STATE_FILE = os.path.join(DATA_DIR, "last_state.json")
RESUME_MODE    = os.environ.get("RESUME_MODE", "0") == "1"
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
    # Parse http://user:pass@host:port into separate fields
    # Playwright (especially Firefox) needs username/password as separate fields
    from urllib.parse import urlparse, unquote
    try:
        parsed = urlparse(PROXY_URL)
        port = parsed.port  # raises ValueError if port is not an integer
        if not parsed.hostname or not port:
            raise ValueError(f"Missing hostname or port in proxy URL")
        config = {"server": f"{parsed.scheme}://{parsed.hostname}:{port}"}
        if parsed.username:
            config["username"] = unquote(parsed.username)
        if parsed.password:
            config["password"] = unquote(parsed.password)
        log.info(f"Worker {ACCOUNT_ID}: Proxy configured → {parsed.scheme}://{parsed.hostname}:{port}")
        return config
    except Exception as e:
        log.error(
            f"Worker {ACCOUNT_ID}: Invalid PROXY_URL — {e}. "
            f"Check format: http://user:pass@host:port  "
            f"(if username/password contain '@', encode them as '%40'). "
            f"Launching WITHOUT proxy."
        )
        return None


def _launch_browser(playwright, headless: bool, use_chrome: bool = False) -> tuple[Browser, BrowserContext]:
    """
    Launch a browser for the worker.
      - INTERACTIVE mode: Firefox (visible, for login UI)
      - Headless/bot mode: Google Chrome via channel='chrome' (includes Widevine DRM
        so Spotify actually plays audio and registers as a real device)
    """
    proxy = _build_proxy_config()

    if INTERACTIVE:
        # Firefox for interactive — better compatibility with Spotify login UI
        # Memory-saving flags reduce RAM usage from ~400MB to ~200MB on 1GB servers
        # DRM/EME disabled: Widevine refuses to render protected content over a
        # virtual display (Xvfb/VNC), causing the screen to go black 10-15 sec
        # after Spotify Web Player loads. Setup mode only handles login, never
        # playback, so DRM is not needed. Headless playback container uses
        # Chrome with full Widevine in a separate code path (unaffected).
        firefox_kwargs = {
            "headless": False,
            "firefox_user_prefs": {
                "browser.cache.memory.capacity": 8192,       # 8MB memory cache (default ~256MB)
                "browser.sessionhistory.max_entries": 5,     # Fewer history entries
                "gfx.webrender.enabled": False,              # Disable GPU renderer (saves RAM)
                "media.memory_cache_max_size": 8192,         # 8MB media cache
                "javascript.options.mem.gc_incremental": True,
                "media.eme.enabled": False,                  # Disable EME (DRM API) — prevents VNC black screen
                "media.gmp-widevinecdm.enabled": False,      # Don't load Widevine CDM (~50MB savings)
                "media.autoplay.default": 5,                 # Block all autoplay (no DRM init trigger)
            },
        }
        if proxy:
            firefox_kwargs["proxy"] = proxy
        browser = playwright.firefox.launch(**firefox_kwargs)
        log.info(f"Worker {ACCOUNT_ID}: Firefox launched in INTERACTIVE mode (low-memory config).")
    elif use_chrome:
        # Google Chrome (not Chromium) — includes Widevine DRM for Spotify playback.
        # Spotify requires DRM to stream audio. Without it, the play button silently
        # fails and no playback session is registered on Spotify's servers.
        # Chrome appears as "Web Player (Chrome)" in Spotify Connect — identical
        # to a real user, indistinguishable from normal browser usage.
        chrome_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--autoplay-policy=no-user-gesture-required",  # allow music autoplay
            "--disable-background-timer-throttling",       # keep timers alive in bg
            "--disable-renderer-backgrounding",            # prevent tab sleeping
        ]
        launch_kwargs = {
            "channel": "chrome",   # use installed Google Chrome, not Playwright Chromium
            "headless": headless,  # False when running on Xvfb (DRM needs visible context)
            "args": chrome_args,
        }
        if proxy:
            launch_kwargs["proxy"] = proxy
        browser = playwright.chromium.launch(**launch_kwargs)
        mode = "VISIBLE (Xvfb)" if not headless else "HEADLESS"
        log.info(f"Worker {ACCOUNT_ID}: Google Chrome launched in {mode} mode (Widevine DRM enabled).")
    else:
        # Fallback: plain Chromium headless (no DRM — for testing only)
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
        browser = playwright.chromium.launch(**launch_kwargs)
        log.info(f"Worker {ACCOUNT_ID}: Chromium launched in HEADLESS mode (no DRM).")

    # Load saved session state if it exists
    # Rotate user-agent per account so each appears as a different device.
    # Uses a deterministic hash so the same account always gets the same UA.
    _USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.6312.106 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3.1 Safari/605.1.15",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.6261.129 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.6312.87 Safari/537.36",
    ]
    _ua_index = hash(ACCOUNT_ID) % len(_USER_AGENTS)

    context_kwargs = {
        "locale": "en-IN",
        "timezone_id": "Asia/Kolkata",
        "viewport": {"width": 1280, "height": 800},
        "user_agent": _USER_AGENTS[_ua_index],
    }

    if INTERACTIVE:
        # Match viewport to the 1920x1080 Xvfb display so Firefox fills the VNC screen.
        # Firefox UI chrome (tab bar + nav bar) uses ~80px, so content height = 1080 - 80.
        context_kwargs["viewport"] = {"width": 1920, "height": 1000}

    if os.path.exists(SESSION_FILE):
        context_kwargs["storage_state"] = SESSION_FILE
        log.info(f"Worker {ACCOUNT_ID}: Loaded session from {SESSION_FILE}")
    elif not INTERACTIVE:
        log.warning(f"Worker {ACCOUNT_ID}: No session.json found — run INTERACTIVE=1 first.")

    if proxy:
        context_kwargs["proxy"] = proxy

    context = browser.new_context(**context_kwargs)

    # Data Saver: block non-essential resources in headless playback mode.
    # Spotify uses MSE for audio (xhr/fetch streams), so blocking the `media`
    # resource type does NOT affect playback. Stylesheets and scripts stay
    # enabled to preserve React rendering and click targeting.
    # Skipped in INTERACTIVE mode — VNC login needs full visual fidelity.
    if not INTERACTIVE:
        _setup_data_saver(context)

    # Evasion: hide navigator.webdriver
    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        window.chrome = { runtime: {} };
    """)

    return browser, context


def _setup_data_saver(context):
    """
    Block image, font, and media resource types to cut bandwidth ~75%.
    Spotify audio uses Media Source Extensions (MSE) over xhr/fetch, NOT the
    `media` resource type, so playback is unaffected. Album art, icons,
    custom fonts, and any preview videos are dropped.
    """
    BLOCKED_TYPES = {"image", "font", "media"}

    def _route_handler(route):
        try:
            if route.request.resource_type in BLOCKED_TYPES:
                route.abort()
            else:
                route.continue_()
        except Exception:
            # Route may already be handled or context closed — fail silent
            try:
                route.continue_()
            except Exception:
                pass

    try:
        context.route("**/*", _route_handler)
        log.info(f"Worker {ACCOUNT_ID}: Data Saver enabled — blocking {sorted(BLOCKED_TYPES)}")
    except Exception as e:
        log.warning(f"Worker {ACCOUNT_ID}: Failed to enable Data Saver: {e}")


# ─── VNC Services (Interactive Mode Only) ─────────────────────────────────────

def _start_vnc_services() -> list[subprocess.Popen]:
    """
    Starts Xvfb (virtual display), x11vnc (VNC server), and
    websockify (WebSocket→VNC bridge for noVNC).
    Returns subprocess handles for cleanup.
    """
    procs = []

    # 1. Start Xvfb on display :99
    # Use 1920x1080 (16:9) so VNC fills modern widescreen browsers without black bars.
    # 24-bit color prevents rendering artifacts with noVNC.
    # stderr → DEVNULL to prevent pipe buffer deadlock (64KB limit blocks the process).
    xvfb = subprocess.Popen(
        ["Xvfb", ":99", "-screen", "0", "1920x1080x24"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    procs.append(xvfb)
    os.environ["DISPLAY"] = ":99"
    time.sleep(1)  # Wait for Xvfb to initialize
    if xvfb.poll() is not None:
        log.error(f"Worker {ACCOUNT_ID}: Xvfb CRASHED on startup!")
        return procs
    log.info(f"Worker {ACCOUNT_ID}: Xvfb started on :99")

    # 2. Start x11vnc — captures the Xvfb display
    #    -localhost: only websockify connects (no raw VNC exposed to the network)
    #    -shared: allow multiple simultaneous connections (prevents "already connected" kicks)
    #    -noxdamage: avoid X Damage extension crashes with Xvfb
    #    stderr → DEVNULL to prevent pipe buffer deadlock
    x11vnc = subprocess.Popen(
        ["x11vnc", "-display", ":99", "-nopw", "-localhost",
         "-xkb", "-forever", "-shared", "-noxdamage", "-quiet",
         "-rfbport", "5900"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    procs.append(x11vnc)
    time.sleep(1)
    if x11vnc.poll() is not None:
        log.error(f"Worker {ACCOUNT_ID}: x11vnc CRASHED!")
        return procs
    log.info(f"Worker {ACCOUNT_ID}: x11vnc started on port 5900")

    # 3. Start websockify — bridges WebSocket (VNC_PORT) → VNC (5900)
    #    --heartbeat=10: aggressive keep-alive prevents NAT/proxy timeouts
    #    stderr → DEVNULL to prevent pipe buffer deadlock (websockify logs every
    #    connection/heartbeat event — fills 64KB pipe buffer within minutes, blocking
    #    the process and killing the VNC connection)
    novnc_web = "/opt/novnc"
    websockify = subprocess.Popen(
        ["websockify", f"0.0.0.0:{VNC_PORT}", "localhost:5900",
         "--web", novnc_web, "--heartbeat=10"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    procs.append(websockify)
    time.sleep(2)  # Give websockify time to bind and accept connections
    if websockify.poll() is not None:
        log.error(f"Worker {ACCOUNT_ID}: websockify CRASHED!")
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

            # ── Check if saved session is still valid (one-time login) ────
            # If session.json exists and user is still logged in, skip re-login entirely.
            if os.path.exists(SESSION_FILE):
                log.info(f"Worker {ACCOUNT_ID}: Existing session found — checking if still valid...")
                try:
                    page.goto("https://open.spotify.com", timeout=60_000)
                    page.wait_for_load_state("domcontentloaded", timeout=50_000)
                    time.sleep(3)

                    login_state = page.evaluate("""
                        () => {
                            const userWidget = document.querySelector('[data-testid="user-widget-link"]');
                            const profileBtn = document.querySelector('[data-testid="user-widget-button"]');
                            const loginBtn = document.querySelector('[data-testid="login-button"]');
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
                        log.info(f"Worker {ACCOUNT_ID}: Session still valid! User: {spotify_user} — no re-login needed.")

                        # Refresh session cookies
                        context.storage_state(path=SESSION_FILE)
                        log.info(f"Worker {ACCOUNT_ID}: Session refreshed → {SESSION_FILE}")

                        # Save username
                        user_file = os.path.join(DATA_DIR, "spotify_user.txt")
                        with open(user_file, "w") as f:
                            f.write(spotify_user or "unknown")

                        # Write done flag so backend auto-starts headless worker
                        done_flag = os.path.join(DATA_DIR, ".setup_done")
                        with open(done_flag, "w") as f:
                            f.write("done")
                        log.info(f"Worker {ACCOUNT_ID}: Setup complete (session reused). Flag written.")

                        time.sleep(10)  # Keep VNC alive briefly
                        browser.close()
                        return  # Skip login flow — finally block still runs
                except Exception as e:
                    log.info(f"Worker {ACCOUNT_ID}: Session check failed ({e}) — proceeding with fresh login.")

            # Navigate directly to Spotify login page.
            # open.spotify.com does NOT auto-redirect to login — it shows a "Log in" button.
            # Going to accounts.spotify.com directly shows the login form immediately.
            login_url = "https://accounts.spotify.com/login?continue=https%3A%2F%2Fopen.spotify.com%2F"
            log.info(f"Worker {ACCOUNT_ID}: Navigating to Spotify login page...")
            nav_ok = False
            for _nav_attempt in range(3):
                try:
                    page.goto(login_url, timeout=30_000)
                    nav_ok = True
                    break
                except Exception as nav_err:
                    log.warning(f"Worker {ACCOUNT_ID}: Navigation failed (attempt {_nav_attempt+1}/3): {nav_err}")
                    time.sleep(5)
            if not nav_ok:
                log.error(f"Worker {ACCOUNT_ID}: Could not load login page after 3 attempts (proxy may be down). Keeping VNC alive for manual access.")
                # Keep VNC alive so user can see the browser and diagnose
                while not _shutdown:
                    time.sleep(5)
                    try:
                        screen_path = os.path.join(DATA_DIR, "live.jpeg")
                        page.screenshot(path=screen_path, type="jpeg", quality=40)
                    except Exception:
                        pass
                browser.close()
                return
            log.info(f"Worker {ACCOUNT_ID}: Login page loaded. Waiting for user to log in...")

            # ── Auto-detect login via DOM polling ─────────────────────────
            # Detects login on both accounts.spotify.com and open.spotify.com.
            # If Spotify doesn't auto-redirect after login, we navigate manually.
            spotify_user = None
            poll_count = 0
            max_polls = 900  # 30 minutes (900 × 2s)
            initial_login_url = page.url  # Track the starting URL
            last_url = initial_login_url
            url_changed_at = None  # Track when URL first changed from login page

            while not _shutdown and poll_count < max_polls:
                time.sleep(2)
                poll_count += 1

                try:
                    # Capture live screenshot for the Browser Window feed
                    try:
                        screen_path = os.path.join(DATA_DIR, "live.jpeg")
                        page.screenshot(path=screen_path, type="jpeg", quality=40)
                    except Exception:
                        pass

                    # Check ALL pages in context (login may open new tabs)
                    current_url = page.url
                    for p in context.pages:
                        try:
                            p_url = p.url
                            if "open.spotify.com" in p_url and "accounts.spotify.com" not in p_url:
                                page = p  # Switch to the logged-in page
                                current_url = p_url
                                log.info(f"Worker {ACCOUNT_ID}: Found open.spotify.com in another tab!")
                                break
                        except Exception:
                            pass

                    # ── Case 1: On accounts.spotify.com (check FIRST — login
                    #    URL contains "open.spotify.com" in query param!) ─────
                    if "accounts.spotify.com" in current_url:
                        # Track ANY URL change (email→password, password→status, etc.)
                        if current_url != last_url:
                            last_url = current_url
                            url_changed_at = poll_count
                            log.info(f"Worker {ACCOUNT_ID}: Auth URL changed: {current_url[:80]}")

                        # 20s after the LAST URL change, try navigating to open.spotify.com
                        if url_changed_at and (poll_count - url_changed_at) >= 10:
                            log.info(f"Worker {ACCOUNT_ID}: Login likely complete — navigating to open.spotify.com...")
                            try:
                                page.goto("https://open.spotify.com", timeout=30_000)
                                page.wait_for_load_state("domcontentloaded", timeout=15_000)
                            except Exception as nav_err:
                                log.warning(f"Worker {ACCOUNT_ID}: Navigation error: {nav_err}")
                            url_changed_at = None
                            continue

                        # Fallback: if stuck on auth page >60s with no URL change,
                        # try navigating to open.spotify.com — the user may have
                        # logged in but the redirect didn't fire in Playwright.
                        if poll_count % 30 == 0 and poll_count >= 30:
                            log.info(f"Worker {ACCOUNT_ID}: Checking if login completed (navigating to open.spotify.com)...")
                            try:
                                page.goto("https://open.spotify.com", timeout=30_000)
                                page.wait_for_load_state("domcontentloaded", timeout=15_000)
                            except Exception as nav_err:
                                log.warning(f"Worker {ACCOUNT_ID}: Navigation error: {nav_err}")
                            continue

                        if poll_count % 15 == 0:
                            log.info(f"Worker {ACCOUNT_ID}: On auth page, waiting for login... ({poll_count * 2}s)")
                        continue

                    # ── Case 2: On open.spotify.com ──────────────────────────
                    if "open.spotify.com" in current_url:
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
                            log.info(f"Worker {ACCOUNT_ID}: Login detected! User: {spotify_user}")
                            time.sleep(2)
                            break

                        # On open.spotify.com but not logged in — go back to login
                        if poll_count % 15 == 0:
                            log.info(f"Worker {ACCOUNT_ID}: On Spotify but not logged in yet... ({poll_count * 2}s)")
                            page.goto(login_url, timeout=30_000)
                        continue

                    # ── Case 3: On some other URL (redirect, consent, etc.) ──
                    if poll_count % 15 == 0:
                        log.info(f"Worker {ACCOUNT_ID}: On intermediate page: {current_url[:80]} ({poll_count * 2}s)")

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

            # Navigate to Spotify Web Player so user sees it in VNC
            try:
                if "open.spotify.com" not in page.url:
                    log.info(f"Worker {ACCOUNT_ID}: Navigating to Spotify Web Player...")
                    page.goto("https://open.spotify.com", timeout=30_000)
                    page.wait_for_load_state("domcontentloaded", timeout=15_000)
                    log.info(f"Worker {ACCOUNT_ID}: Spotify Web Player loaded in VNC.")
            except Exception as nav_err:
                log.warning(f"Worker {ACCOUNT_ID}: Could not navigate to Web Player: {nav_err}")

            # Write the .setup_done flag so the backend knows we're finished
            done_flag = os.path.join(DATA_DIR, ".setup_done")
            with open(done_flag, "w") as f:
                f.write("done")
            log.info(f"Worker {ACCOUNT_ID}: Setup complete flag written.")

            # Keep VNC alive so user can see the Web Player before container exits
            time.sleep(60)

            browser.close()

    finally:
        _stop_vnc_services(vnc_procs)

    log.info(f"Worker {ACCOUNT_ID}: Interactive setup complete. Container exiting.")
    sys.exit(0)


# ─── Headless Bot Mode ────────────────────────────────────────────────────────

def _load_playlists() -> tuple[list[str], int]:
    """Load playlist URIs and current_index from playlists.json."""
    if not os.path.exists(PLAYLIST_FILE):
        return [], 0
    try:
        with open(PLAYLIST_FILE, "r") as f:
            data = json.load(f)
        return data.get("playlists", []), data.get("current_index", 0)
    except Exception:
        return [], 0


def _save_last_state(playlist_index: int, playlist_id: str, track_name: str,
                     track_row: int | None = None, progress_pct: float = 0.0):
    """
    Persist fine-grained playback state so the user can resume from exactly
    where the bot stopped. Written periodically from the polling loop.
    """
    try:
        state = {
            "playlist_index": playlist_index,
            "playlist_id": playlist_id,
            "track_name": track_name or "",
            "track_row": track_row,
            "progress_pct": progress_pct,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        with open(LAST_STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception:
        pass


def _load_last_state() -> dict | None:
    """Load the last-known playback state (or None if missing/invalid)."""
    if not os.path.exists(LAST_STATE_FILE):
        return None
    try:
        with open(LAST_STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return None


def _save_progress(index: int):
    """Persist current playlist index so restarts resume correctly."""
    try:
        data = {}
        if os.path.exists(PLAYLIST_FILE):
            with open(PLAYLIST_FILE, "r") as f:
                data = json.load(f)
        data["current_index"] = index
        with open(PLAYLIST_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def _uri_to_url(uri: str) -> str:
    """Convert spotify:playlist:XXXX or a URL to an open.spotify.com URL."""
    if uri.startswith("http"):
        return uri
    parts = uri.split(":")
    if len(parts) >= 3:
        return f"https://open.spotify.com/{parts[1]}/{parts[2]}"
    return f"https://open.spotify.com/playlist/{uri}"


def _screenshot(page):
    """Capture live screenshot for the Browser Window feed."""
    try:
        page.screenshot(path=os.path.join(DATA_DIR, "live.jpeg"), type="jpeg", quality=40)
    except Exception:
        pass


# ─── Spotify Token Capture ───────────────────────────────────────────────────
# Capture Bearer tokens from Spotify's own API calls via Playwright request interception.
# This is more reliable than calling the get_access_token endpoint via page.evaluate().
_captured_spotify_token = {"token": None}


def _setup_token_capture(page):
    """
    Listen for Spotify's own network requests and capture the Bearer token.
    Spotify's JS makes dozens of API calls — each one carries the access token.
    """
    def on_request(request):
        try:
            url = request.url
            if "api.spotify.com" in url or "spclient.wg.spotify.com" in url:
                auth = request.headers.get("authorization", "")
                if auth.startswith("Bearer ") and len(auth) > 50:
                    _captured_spotify_token["token"] = auth[7:]
        except Exception:
            pass
    page.on("request", on_request)


def _click_play(page, playlist_id: str = "", timeout: int = 10_000) -> bool:
    """
    Click the playlist's main play button (the big green one in the header).
    Uses targeted selectors to avoid clicking the bottom playback bar's play
    button, which would just resume the previous queue (e.g. Liked Songs).
    """
    # 1. Most specific: the action-bar play button in the playlist header
    # 2. Fallback: any data-testid play-button (less reliable)
    selectors = [
        '[data-testid="action-bar-row"] button[data-testid="play-button"]',
        'section button[data-testid="play-button"]',
        'button[data-testid="play-button"]',
    ]
    for sel in selectors:
        try:
            btn = page.wait_for_selector(sel, timeout=timeout, state="visible")
            if btn:
                btn.click()
                log.info(f"Worker {ACCOUNT_ID}: Clicked play button ({sel})")
                return True
        except Exception:
            continue
    return False


def _force_play_first_track(page, track_index: int = 1) -> bool:
    """
    Force playback to start at a specific track (1-based) in the tracklist.
    Uses the per-row hover play button — this reliably sets the playlist
    context to the target track and avoids the double-click ambiguity that
    previously caused Spotify to skip mid-playback.

    track_index=1 → first track; used for resume with a known index.
    """
    try:
        # Spotify renders a hidden play button on each track row (appears on
        # hover in the UI). It's always present in the DOM — we click it with
        # force=True to bypass the visibility check. This plays that specific
        # track from within the playlist context.
        rows = page.query_selector_all(
            'div[data-testid="playlist-tracklist"] div[data-testid="tracklist-row"]'
        )
        if not rows:
            log.warning(f"Worker {ACCOUNT_ID}: No tracklist rows found for force-play")
            return False
        idx = max(0, min(track_index - 1, len(rows) - 1))
        row = rows[idx]
        btn = row.query_selector('button[data-testid="play-button"]')
        if btn:
            btn.click(force=True)
            log.info(f"Worker {ACCOUNT_ID}: Force-played row #{idx + 1} via hover play button")
            return True
        # Fallback: click the row itself (older Spotify UI)
        row.click(force=True)
        log.info(f"Worker {ACCOUNT_ID}: Force-played row #{idx + 1} via row click (fallback)")
        return True
    except Exception as e:
        log.warning(f"Worker {ACCOUNT_ID}: Force-play failed: {e}")
    return False


def _ensure_playing_from_top(page) -> dict | None:
    """
    Atomic DOM op that runs AFTER the header play button click to guarantee
    the playlist starts from track 1 in order. Root cause we're fixing:
      - Spotify persists shuffle=ON from a prior session.
      - Clicking the header play button builds a SHUFFLED queue.
      - Calling _disable_shuffle_repeat() afterwards turns shuffle off but
        does NOT rebuild the current queue — so playback continues in the
        shuffled order (which is why "track 2" changes between runs and
        why track 1 appears to get skipped mid-play).

    This helper, in a single page.evaluate call:
      1. Clicks shuffle/repeat buttons off if they are active.
      2. Reads the first row's track name and the now-playing widget name.
      3. If they don't match, clicks the first row's per-row play button
         (or dispatches a real dblclick event as fallback). This makes
         Spotify rebuild the queue with track 1 at the top, in order.
    """
    try:
        result = page.evaluate("""() => {
            const out = { shuffle_off: false, repeat_off: false };

            const sh = document.querySelector('button[data-testid="control-button-shuffle"][aria-checked="true"]');
            if (sh) { sh.click(); out.shuffle_off = true; }
            const rp = document.querySelector('button[data-testid="control-button-repeat"][aria-checked="true"]');
            if (rp) { rp.click(); out.repeat_off = true; }

            const rows = document.querySelectorAll(
                'div[data-testid="playlist-tracklist"] div[data-testid="tracklist-row"]'
            );
            if (!rows.length) { out.action = 'no_rows'; return out; }

            const firstRow = rows[0];
            const firstName = (
                firstRow.querySelector('a[data-testid="internal-track-link"]')?.textContent ||
                firstRow.querySelector('div[dir="auto"]')?.textContent || ''
            ).trim();
            const nowName = (
                document.querySelector('div[data-testid="now-playing-widget"] a[data-testid="context-item-link"]')?.textContent ||
                document.querySelector('div[data-testid="now-playing-widget"] a')?.textContent || ''
            ).trim();

            out.first = firstName;
            out.now = nowName;

            if (firstName && nowName && firstName === nowName) {
                out.action = 'already_first';
                return out;
            }

            // Click track 1's per-row play button (visible on hover in the UI,
            // but always present in the DOM — we can click it programmatically).
            const btn = firstRow.querySelector('button[data-testid="play-button"]');
            if (btn) {
                btn.click();
                out.action = 'clicked_first_row_btn';
                return out;
            }

            // Fallback: dispatch a real double-click event on the row. This
            // matches how the Spotify UI starts playback on double-click.
            const evt = new MouseEvent('dblclick', { bubbles: true, cancelable: true, view: window });
            firstRow.dispatchEvent(evt);
            out.action = 'dblclick_first_row';
            return out;
        }""")
        return result
    except Exception as e:
        log.warning(f"Worker {ACCOUNT_ID}: ensure_playing_from_top failed: {e}")
        return None


def _find_track_row_by_name(page, track_name: str) -> int | None:
    """
    Find the 1-based row index of a track whose displayed name matches
    `track_name` (case-insensitive, prefix match tolerant). Returns None
    if not found. Used by RESUME_MODE to jump to the last-played track.
    """
    if not track_name:
        return None
    try:
        return page.evaluate("""(target) => {
            const rows = document.querySelectorAll(
                'div[data-testid="playlist-tracklist"] div[data-testid="tracklist-row"]'
            );
            const t = target.toLowerCase().trim();
            for (let i = 0; i < rows.length; i++) {
                const link = rows[i].querySelector('a[data-testid="internal-track-link"]')
                           || rows[i].querySelector('div[dir="auto"]');
                const name = (link?.textContent || '').toLowerCase().trim();
                if (name && (name === t || name.startsWith(t) || t.startsWith(name))) {
                    return i + 1;
                }
            }
            return null;
        }""", track_name)
    except Exception:
        return None


def _follow_playlist(page, playlist_id: str = ""):
    """
    Save/follow the current playlist if not already in the user's library.
    Tries DOM click first, then falls back to Spotify API via browser token.
    """
    # Method 1: DOM click on the save/add button
    # Wait for the action bar to render (Spotify lazy-loads it)
    try:
        page.wait_for_selector(
            '[data-testid="action-bar-row"]', timeout=5_000, state="visible"
        )
    except Exception:
        pass  # Action bar might not exist on all pages

    try:
        save_btn = page.query_selector(
            '[data-testid="action-bar-row"] button[data-testid="add-button"]'
        )
        if not save_btn:
            save_btn = page.query_selector('button[data-testid="add-button"]')
        if save_btn:
            is_saved = save_btn.get_attribute("aria-checked")
            if is_saved == "true":
                log.info(f"Worker {ACCOUNT_ID}: Playlist already in library")
                return
            save_btn.click()
            log.info(f"Worker {ACCOUNT_ID}: ★ Playlist saved to library")
            time.sleep(1)
            return
    except Exception:
        pass

    # Method 2: Spotify API via captured token
    if playlist_id:
        token = _get_spotify_token(page)
        if not token:
            log.info(f"Worker {ACCOUNT_ID}: No token — cannot save playlist via API")
            return
        try:
            saved = page.evaluate("""async ([pid, token]) => {
                try {
                    const resp = await fetch('https://api.spotify.com/v1/playlists/' + pid + '/followers', {
                        method: 'PUT',
                        headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' }
                    });
                    return resp.ok;
                } catch(e) { return false; }
            }""", [playlist_id, token])
            if saved:
                log.info(f"Worker {ACCOUNT_ID}: ★ Playlist saved to library (API)")
            else:
                log.info(f"Worker {ACCOUNT_ID}: Could not save playlist via API")
        except Exception as e:
            log.warning(f"Worker {ACCOUNT_ID}: Could not save playlist: {e}")


def _is_playing(page) -> bool:
    """Check if Spotify is currently playing by looking at the pause button."""
    try:
        pause = page.query_selector('button[data-testid="control-button-playpause"][aria-label="Pause"]')
        return pause is not None
    except Exception:
        return False


def _get_now_playing(page) -> dict:
    """
    Extract current playback state from the Spotify Web Player DOM.
    Returns { track, artist, playing, progress_pct, context_url }
    """
    try:
        info = page.evaluate("""() => {
            const result = { track: null, artist: null, playing: false, progress_pct: 0, context_url: null };

            // Track name
            const trackEl = document.querySelector('div[data-testid="now-playing-widget"] a[data-testid="context-item-link"]')
                         || document.querySelector('div[data-testid="now-playing-widget"] a');
            if (trackEl) result.track = trackEl.textContent?.trim() || null;

            // Artist
            const artistEl = document.querySelector('div[data-testid="now-playing-widget"] a[href*="/artist/"]');
            if (artistEl) result.artist = artistEl.textContent?.trim() || null;

            // Playing state
            const pauseBtn = document.querySelector('button[data-testid="control-button-playpause"][aria-label="Pause"]');
            result.playing = !!pauseBtn;

            // Progress bar percentage
            const progressBar = document.querySelector('div[data-testid="playback-progressbar"] div[style*="width"]')
                             || document.querySelector('div[data-testid="progress-bar"] div[style*="width"]');
            if (progressBar) {
                const style = progressBar.getAttribute('style') || '';
                const match = style.match(/width:\s*([\d.]+)%/);
                if (match) result.progress_pct = parseFloat(match[1]);
            }

            // Current context (playlist URL from the "now playing" link)
            const contextLink = document.querySelector('div[data-testid="now-playing-widget"] a[href*="/playlist/"]');
            if (contextLink) result.context_url = contextLink.getAttribute('href');

            return result;
        }""")
        return info or {}
    except Exception:
        return {}


def _auto_save_detected_playlist(page, current_playlist_id: str):
    """
    During autoplay, if context switched to a different playlist, save it.
    Mirrors manual bot's _auto_save_playlist() — detects new playlist from
    the now-playing widget and adds it to playlists.json.
    """
    state = _get_now_playing(page)
    context_url = state.get("context_url") or ""
    if not context_url or "/playlist/" not in context_url:
        return None
    if current_playlist_id in context_url:
        return None  # Same playlist, nothing to save

    new_id = context_url.split("/playlist/")[-1].split("?")[0].split("/")[0]
    if not new_id:
        return None

    # Add to playlists.json if not already there
    try:
        data = {}
        if os.path.exists(PLAYLIST_FILE):
            with open(PLAYLIST_FILE, "r") as f:
                data = json.load(f)
        existing = data.get("playlists", [])
        if not any(new_id in p for p in existing):
            new_uri = f"https://open.spotify.com/playlist/{new_id}"
            existing.append(new_uri)
            data["playlists"] = existing
            with open(PLAYLIST_FILE, "w") as f:
                json.dump(data, f)
            log.info(f"Worker {ACCOUNT_ID}: ★ Auto-saved autoplay playlist: {new_id}")
            return new_id
    except Exception:
        pass
    return None


def _get_spotify_token(page) -> str | None:
    """
    Get Spotify access token. Tries:
      1. Token captured from Spotify's own network requests (most reliable)
      2. Spotify's internal get_access_token endpoint (fallback)
    """
    # Method 1: Use token already captured from Spotify's own API traffic
    if _captured_spotify_token["token"]:
        return _captured_spotify_token["token"]

    # Method 2: Call get_access_token endpoint via browser context
    try:
        result = page.evaluate("""async () => {
            try {
                const resp = await fetch(
                    'https://open.spotify.com/get_access_token?reason=transport&productType=web_player',
                    { credentials: 'include' }
                );
                if (!resp.ok) return { error: 'HTTP ' + resp.status };
                const data = await resp.json();
                return { token: data.accessToken || null };
            } catch(e) { return { error: e.message || 'fetch failed' }; }
        }""")
        if result and result.get("token"):
            _captured_spotify_token["token"] = result["token"]
            return result["token"]
        if result and result.get("error"):
            log.warning(f"Worker {ACCOUNT_ID}: get_access_token failed: {result['error']}")
    except Exception as e:
        log.warning(f"Worker {ACCOUNT_ID}: Token extraction error: {e}")
    return None


def _get_queue_info(page) -> dict | None:
    """
    Get playback queue from Spotify API via the browser's access token.
    Returns { count, track_names, track_uris } or None on failure.
    Mirrors manual bot's queue check (app.py lines 508-535, 612-625).
    """
    token = _get_spotify_token(page)
    if not token:
        log.warning(f"Worker {ACCOUNT_ID}: No Spotify token for queue API")
        return None

    try:
        result = page.evaluate("""async (token) => {
            try {
                const resp = await fetch('https://api.spotify.com/v1/me/player/queue', {
                    headers: { 'Authorization': 'Bearer ' + token }
                });
                if (!resp.ok) {
                    const retryAfter = resp.headers.get('Retry-After');
                    return { error: 'HTTP ' + resp.status, retry_after: retryAfter ? parseInt(retryAfter) : null };
                }

                const data = await resp.json();
                const current = data.currently_playing;
                const queue = data.queue || [];

                let names = [];
                let uris = [];
                if (current) {
                    if (current.name) names.push(current.name);
                    if (current.uri) uris.push(current.uri);
                }
                for (const item of queue) {
                    if (item.name) names.push(item.name);
                    if (item.uri) uris.push(item.uri);
                }

                return { count: names.length, track_names: names, track_uris: uris };
            } catch(e) { return { error: e.message || 'fetch failed' }; }
        }""", token)

        if result and result.get("error"):
            retry_after = result.get("retry_after")
            if retry_after:
                log.warning(f"Worker {ACCOUNT_ID}: Queue API error: {result['error']} (Retry-After: {retry_after}s)")
            else:
                log.warning(f"Worker {ACCOUNT_ID}: Queue API error: {result['error']}")
            return result  # Return the error result so caller can read retry_after
        return result
    except Exception as e:
        log.warning(f"Worker {ACCOUNT_ID}: Queue API exception: {e}")
        return None


def _disable_shuffle_repeat(page):
    """Turn off shuffle and repeat via UI buttons."""
    try:
        # Disable shuffle if active
        shuffle_btn = page.query_selector('button[data-testid="control-button-shuffle"][aria-checked="true"]')
        if shuffle_btn:
            shuffle_btn.click()
            log.info(f"Worker {ACCOUNT_ID}: Disabled shuffle")
            time.sleep(0.5)
    except Exception:
        pass
    try:
        # Disable repeat if active
        repeat_btn = page.query_selector('button[data-testid="control-button-repeat"][aria-checked="true"]')
        if repeat_btn:
            repeat_btn.click()
            log.info(f"Worker {ACCOUNT_ID}: Disabled repeat")
            time.sleep(0.5)
    except Exception:
        pass


def _start_xvfb() -> subprocess.Popen | None:
    """
    Start Xvfb (virtual display) for headless bot mode.
    Chrome needs a visible rendering context for Widevine DRM to work.
    Running on Xvfb satisfies this — no physical display needed.
    """
    try:
        xvfb = subprocess.Popen(
            ["Xvfb", ":99", "-screen", "0", "1280x800x24"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )
        os.environ["DISPLAY"] = ":99"
        time.sleep(2)  # Wait for Xvfb to initialize
        if xvfb.poll() is not None:
            stderr = xvfb.stderr.read().decode() if xvfb.stderr else ""
            log.error(f"Worker {ACCOUNT_ID}: Xvfb CRASHED on startup! stderr: {stderr}")
            return None
        log.info(f"Worker {ACCOUNT_ID}: Xvfb started on :99 (virtual display for DRM)")
        return xvfb
    except FileNotFoundError:
        log.warning(f"Worker {ACCOUNT_ID}: Xvfb not found — falling back to headless (no DRM).")
        return None


def run_headless():
    """
    Opens Spotify Web Player using Google Chrome with Widevine DRM.
    Runs Chrome on Xvfb (virtual display) so DRM works without a physical screen.
    Spotify sees this as a real "Web Player (Chrome)" device in Spotify Connect.
    Plays playlists with human-like delays — mirrors the manual bot features.
    """
    log.info(f"Worker {ACCOUNT_ID}: ─── HEADLESS BOT MODE ───")

    if not os.path.exists(SESSION_FILE):
        log.error(f"Worker {ACCOUNT_ID}: Missing session.json. Run with INTERACTIVE=1 first.")
        sys.exit(1)

    playlists, current_index = _load_playlists()

    # ── Human-like delay profile (unique per session, mirrors manual bot) ──
    delay_profile = {
        "playlist_gap_min": random.randint(30, 60),      # 30s – 1min between playlists
        "playlist_gap_max": random.randint(90, 180),      # 1.5min – 3min
        "poll_base": random.uniform(1.5, 3.0),
        "track_pause_min": random.uniform(0.3, 1.0),
        "track_pause_max": random.uniform(1.5, 3.0),
    }
    log.info(f"Worker {ACCOUNT_ID}: Delay profile: gap={delay_profile['playlist_gap_min']}-{delay_profile['playlist_gap_max']}s, poll={delay_profile['poll_base']:.1f}s")

    if not playlists:
        log.info(f"Worker {ACCOUNT_ID}: No playlists configured — keep-alive mode only.")

    # ── Start Xvfb virtual display for Chrome + Widevine DRM ──────────────────
    # Widevine DRM requires a visible rendering context. Xvfb provides a virtual
    # display that satisfies this requirement without a physical monitor.
    xvfb_proc = _start_xvfb()
    use_chrome = xvfb_proc is not None  # Use Chrome with DRM if Xvfb is available

    try:
        with sync_playwright() as p:
            # Launch Chrome on Xvfb (headless=False for DRM) or fall back to Chromium
            browser, context = _launch_browser(p, headless=(not use_chrome), use_chrome=use_chrome)
            page = context.new_page()

            # Capture Bearer tokens from Spotify's own API traffic
            _setup_token_capture(page)

            log.info(f"Worker {ACCOUNT_ID}: Navigating to open.spotify.com...")
            try:
                # domcontentloaded returns as soon as DOM is ready — Spotify's
                # React SPA keeps streaming assets forever so waiting for 'load'
                # always hits the timeout.
                page.goto("https://open.spotify.com", timeout=60_000, wait_until="domcontentloaded")
                log.info(f"Worker {ACCOUNT_ID}: Spotify Web Player loaded.")
            except Exception as e:
                log.error(f"Worker {ACCOUNT_ID}: Failed to load Spotify: {e}")
                browser.close()
                sys.exit(1)

            _screenshot(page)

            # ── Resume from last state (if RESUME_MODE=1) ─────────────────
            # The user requested this from the dashboard Resume button. We
            # look up the saved `last_state.json` and jump the playlist
            # index. The specific track is picked up later inside the
            # playback loop when we see the saved playlist_id.
            resume_target_track: str | None = None
            resume_target_playlist: str | None = None
            if RESUME_MODE:
                last = _load_last_state()
                if last:
                    saved_idx = last.get("playlist_index")
                    if isinstance(saved_idx, int) and 0 <= saved_idx < len(playlists):
                        current_index = saved_idx
                        resume_target_playlist = last.get("playlist_id")
                        resume_target_track = last.get("track_name")
                        log.info(
                            f"Worker {ACCOUNT_ID}: ⟳ RESUME → playlist #{current_index + 1} "
                            f"({resume_target_playlist}), track '{(resume_target_track or '?')[:40]}'"
                        )
                    else:
                        log.info(f"Worker {ACCOUNT_ID}: RESUME_MODE set but last_state is out of range — starting fresh")
                else:
                    log.info(f"Worker {ACCOUNT_ID}: RESUME_MODE set but no last_state.json — starting fresh")

            # ── Playlist playback loop ────────────────────────────────────
            if playlists:
                if current_index >= len(playlists):
                    current_index = 0
                log.info(f"Worker {ACCOUNT_ID}: {len(playlists)} playlists queued, starting from #{current_index + 1}")

                while not _shutdown and current_index < len(playlists):
                    playlist_uri = playlists[current_index]
                    playlist_url = _uri_to_url(playlist_uri)
                    playlist_id = playlist_uri.split(":")[-1] if ":" in playlist_uri else playlist_uri.split("/")[-1].split("?")[0]
                    log.info(f"Worker {ACCOUNT_ID}: ▶ Playlist {current_index + 1}/{len(playlists)} ({playlist_id})")
                    # Persist current index at the START so the dashboard reflects
                    # which playlist is active in real time (not only on completion).
                    _save_progress(current_index)

                    # Navigate to the playlist page.
                    # wait_until="domcontentloaded" returns in ~1-2s instead of
                    # hitting the 30s 'load' timeout (Spotify never fully loads).
                    tracklist_sel = 'div[data-testid="playlist-tracklist"], [data-testid="action-bar-row"]'
                    rendered = False
                    for attempt in range(2):
                        # Liveness check — if Chrome renderer crashed, recreate the page
                        # from the same context so token capture and cookies carry over.
                        try:
                            page.evaluate("1")
                        except Exception:
                            log.warning(f"Worker {ACCOUNT_ID}: Page is dead — recreating from context")
                            try:
                                page.close()
                            except Exception:
                                pass
                            page = context.new_page()
                            _setup_token_capture(page)

                        try:
                            page.goto(playlist_url, timeout=60_000, wait_until="domcontentloaded")
                        except Exception as e:
                            log.warning(f"Worker {ACCOUNT_ID}: Navigation failed (attempt {attempt+1}/2): {e}")
                            # Recreate page on crash so next attempt isn't stuck with a dead one
                            if "crashed" in str(e).lower() or "closed" in str(e).lower():
                                try:
                                    page.close()
                                except Exception:
                                    pass
                                page = context.new_page()
                                _setup_token_capture(page)
                            time.sleep(2)
                            continue

                        # Wait up to 30s for the React SPA to render the tracklist.
                        # Slow 4GB VPS + Chrome + Widevine can take 15-25s on cold load.
                        try:
                            page.wait_for_selector(tracklist_sel, timeout=30_000, state="visible")
                            rendered = True
                            break
                        except Exception:
                            log.warning(f"Worker {ACCOUNT_ID}: Tracklist not rendered in 30s (attempt {attempt+1}/2) — reloading")
                            time.sleep(1)

                    if not rendered:
                        log.warning(f"Worker {ACCOUNT_ID}: Playlist page never rendered — skipping.")
                        current_index += 1
                        _save_progress(current_index)
                        continue

                    time.sleep(1)  # One beat for React state to settle
                    _screenshot(page)

                    # DOM-only pre-play count (rough estimate — used as fallback only)
                    # Real count comes from Queue API AFTER play starts (token not ready yet)
                    total_count = 0
                    try:
                        total_count = page.evaluate("""async () => {
                            // Method 1: aria-rowcount on the tracklist grid
                            // Spotify sets this for accessibility on virtualized lists —
                            // gives exact total even when only a few rows are rendered.
                            const grid = document.querySelector('div[data-testid="playlist-tracklist"] [role="grid"]');
                            if (grid) {
                                const rc = grid.getAttribute('aria-rowcount');
                                if (rc) {
                                    const n = parseInt(rc);
                                    // aria-rowcount includes the header row, so subtract 1
                                    if (n > 1) return n - 1;
                                }
                            }

                            // Also check presentation role grids
                            const grids = document.querySelectorAll('div[data-testid="playlist-tracklist"] [aria-rowcount]');
                            for (const g of grids) {
                                const rc = parseInt(g.getAttribute('aria-rowcount'));
                                if (rc > 1) return rc - 1;
                            }

                            // Method 2: "N songs" text in header/subtitle
                            const spans = document.querySelectorAll('section header span');
                            for (const el of spans) {
                                const m = el.textContent.match(/(\\d+)\\s+songs?/i);
                                if (m) return parseInt(m[1]);
                            }
                            const header = document.querySelector('section header');
                            if (header) {
                                const m = header.textContent.match(/(\\d+)\\s+songs?/i);
                                if (m) return parseInt(m[1]);
                            }
                            // Check subtitle/description area too
                            const meta = document.querySelectorAll('section span, section p, [data-testid="playlist-page"] span');
                            for (const el of meta) {
                                const m = el.textContent.match(/(\\d+)\\s+songs?/i);
                                if (m) return parseInt(m[1]);
                            }

                            // Method 3: Scroll tracklist to discover highest aria-rowindex
                            const tracklist = document.querySelector('div[data-testid="playlist-tracklist"]');
                            if (tracklist) {
                                // Find scrollable parent
                                let sp = tracklist;
                                while (sp && sp !== document.body) {
                                    const st = window.getComputedStyle(sp);
                                    if (st.overflowY === 'auto' || st.overflowY === 'scroll') break;
                                    sp = sp.parentElement;
                                }
                                if (!sp || sp === document.body) sp = document.querySelector('main') || document.documentElement;

                                let maxIdx = 0;
                                for (let i = 0; i < 20; i++) {
                                    const idxEls = tracklist.querySelectorAll('[aria-rowindex]');
                                    for (const el of idxEls) {
                                        const idx = parseInt(el.getAttribute('aria-rowindex'));
                                        if (idx > maxIdx) maxIdx = idx;
                                    }
                                    sp.scrollTop += 600;
                                    await new Promise(r => setTimeout(r, 200));
                                    if (sp.scrollTop + sp.clientHeight >= sp.scrollHeight - 10) break;
                                }
                                sp.scrollTop = 0; // scroll back to top
                                // aria-rowindex is 1-based and includes header row at index 1
                                if (maxIdx > 1) return maxIdx - 1;
                            }

                            // Method 4: Count visible rows (least reliable)
                            const rows = document.querySelectorAll('div[data-testid="playlist-tracklist"] div[data-testid="tracklist-row"]');
                            return rows.length;
                        }""") or 0
                    except Exception:
                        pass
                    log.info(f"Worker {ACCOUNT_ID}: Playlist has {total_count} tracks (DOM scroll)")

                    # Auto-save playlist to library (mirrors manual bot)
                    _follow_playlist(page, playlist_id)

                    # ── Resume-to-track hand-off ──────────────────────────
                    # If we entered this playlist via RESUME_MODE AND this is
                    # the playlist the user stopped on, find the saved track's
                    # row and play from there directly. Consumed once: after
                    # the first use we clear the target so subsequent
                    # playlists play normally from the top.
                    resume_row: int | None = None
                    if (resume_target_track and resume_target_playlist
                            and resume_target_playlist in playlist_id):
                        resume_row = _find_track_row_by_name(page, resume_target_track)
                        if resume_row:
                            log.info(
                                f"Worker {ACCOUNT_ID}: ⟳ Resuming at row #{resume_row} "
                                f"('{resume_target_track[:35]}')"
                            )
                        else:
                            log.warning(
                                f"Worker {ACCOUNT_ID}: Resume track '{resume_target_track[:35]}' "
                                f"not found in DOM — starting from top"
                            )
                        # Consume the resume target either way
                        resume_target_track = None
                        resume_target_playlist = None

                    if resume_row:
                        # Resume path: click the row's play button directly
                        _force_play_first_track(page, track_index=resume_row)
                        time.sleep(2)
                        # Still disable shuffle so the queue from here is in order
                        _disable_shuffle_repeat(page)
                    else:
                        # Normal path: click the playlist's header play button
                        # first (this makes the bottom playbar — and therefore
                        # the shuffle/repeat buttons — appear in the DOM).
                        if not _click_play(page, playlist_id=playlist_id):
                            log.warning(f"Worker {ACCOUNT_ID}: Could not find play button — skipping.")
                            current_index += 1
                            _save_progress(current_index)
                            continue
                        time.sleep(2)

                        # CRITICAL: ensure the queue starts at track 1 in order.
                        # Spotify may have shuffle ON from a previous session,
                        # which causes the header play button to build a random
                        # queue. _ensure_playing_from_top() disables shuffle AND
                        # restarts from track 1 via the row's play button in a
                        # single atomic DOM op — retries once for reliability.
                        for fix_attempt in range(2):
                            result = _ensure_playing_from_top(page)
                            if not result:
                                break
                            if result.get("shuffle_off"):
                                log.info(f"Worker {ACCOUNT_ID}: ⊘ Shuffle was ON — disabled before track 1")
                            if result.get("repeat_off"):
                                log.info(f"Worker {ACCOUNT_ID}: ⊘ Repeat was ON — disabled before track 1")
                            action = result.get("action", "")
                            if action == "already_first":
                                log.info(f"Worker {ACCOUNT_ID}: ✓ Already on track 1 ({(result.get('now') or '')[:35]})")
                                break
                            elif action in ("clicked_first_row_btn", "dblclick_first_row"):
                                log.info(
                                    f"Worker {ACCOUNT_ID}: ⟲ Restarted at track 1 '{(result.get('first') or '?')[:35]}' "
                                    f"(was playing '{(result.get('now') or '?')[:25]}', via {action})"
                                )
                                time.sleep(2)  # let the queue rebuild
                                # Verify on next iteration
                                continue
                            elif action == "no_rows":
                                log.warning(f"Worker {ACCOUNT_ID}: Tracklist rows gone — skipping ensure-from-top")
                                break
                            break

                    # Track URIs/names — populated later via Queue API in polling loop
                    playlist_track_uris = set()
                    playlist_track_names = set()
                    log.info(f"Worker {ACCOUNT_ID}: Playlist has {total_count} tracks (DOM)")

                    # ── Per-playlist polling loop ─────────────────────────────
                    prev_track = None
                    prev_track_uri = None        # URI of previous track for URI-based detection
                    first_track = None           # For loop detection
                    seen_tracks = set()          # Track names seen
                    seen_uris = set()            # Track URIs seen
                    pause_count = 0
                    context_gone_count = 0
                    unknown_count = 0
                    poll_num = 0
                    last_track_seen = False
                    last_poll_time = 0
                    queue_fetch_done = False  # One-shot Queue API fetch after rate limit cools
                    poll_interval = delay_profile["poll_base"]

                    while not _shutdown:
                        time.sleep(1)  # 1s base tick for fast screenshots
                        _screenshot(page)

                        # Only do state checks on the jittered poll interval
                        now = time.time()
                        if now - last_poll_time < poll_interval:
                            continue
                        last_poll_time = now
                        poll_interval = delay_profile["poll_base"] + random.uniform(-0.3, 0.5)
                        poll_num += 1

                        state = _get_now_playing(page)
                        current_track = state.get("track")
                        is_playing = state.get("playing", False)
                        progress_pct = state.get("progress_pct", 0)
                        context_url = state.get("context_url") or ""

                        # Get current track URI from DOM for precise detection
                        current_uri = None
                        try:
                            current_uri = page.evaluate("""() => {
                                const el = document.querySelector('div[data-testid="now-playing-widget"] a[data-testid="context-item-link"]');
                                return el ? el.getAttribute('href') : null;
                            }""")
                            if current_uri:
                                # href = /track/XXXX → convert to spotify:track:XXXX
                                parts = current_uri.strip('/').split('/')
                                if len(parts) >= 2:
                                    current_uri = f"spotify:{parts[-2]}:{parts[-1]}"
                        except Exception:
                            pass

                        # Track unique songs
                        if current_track:
                            seen_tracks.add(current_track)
                            if first_track is None:
                                first_track = current_track
                        if current_uri:
                            seen_uris.add(current_uri)

                        # ── Track change detection ────────────────────────
                        # Fire on: (a) any change between polls, OR (b) the very first
                        # track we ever see (prev_track is None). Previously (b) was
                        # excluded, so track 1 never produced a ♪ log and users thought
                        # the bot was "skipping the first song".
                        track_changed = (current_track and current_track != prev_track)
                        is_first_log = (prev_track is None and current_track is not None)
                        prev_track = current_track
                        prev_track_uri = current_uri

                        if track_changed or is_first_log:
                            micro = random.uniform(delay_profile["track_pause_min"], delay_profile["track_pause_max"])
                            time.sleep(micro)
                            artist = state.get("artist", "")
                            # Classify [✓] / [✗ AUTOPLAY]:
                            #  - With URIs: precise membership check
                            #  - Without URIs: first `total_count` distinct tracks seen
                            #    are assumed playlist tracks; anything after is autoplay.
                            if playlist_track_uris:
                                is_playlist_track = (current_uri in playlist_track_uris
                                                     or current_track in playlist_track_names)
                            elif total_count > 0:
                                is_playlist_track = len(seen_tracks) <= total_count
                            else:
                                is_playlist_track = True
                            in_playlist = "✓" if is_playlist_track else "✗ AUTOPLAY"
                            log.info(f"Worker {ACCOUNT_ID}: ♪ Now playing: {current_track[:40]} — {artist[:25]} [{in_playlist}] ({len(seen_tracks)}/{total_count})")

                            # ── URI-based autoplay detection on track change ──────────
                            # If we have the full playlist URI set AND the new track's URI
                            # is NOT in that set → this is an autoplay track, advance now.
                            if playlist_track_uris and current_uri and len(seen_tracks) > 1:
                                if current_uri not in playlist_track_uris:
                                    log.info(f"Worker {ACCOUNT_ID}: Track URI not in playlist → autoplay detected, advancing.")
                                    break

                            # Name-based fallback (when URI not available but names are)
                            if playlist_track_names and not playlist_track_uris and len(seen_tracks) > 1:
                                if current_track and current_track not in playlist_track_names:
                                    unknown_count += 1
                                    if unknown_count >= 2:
                                        log.info(f"Worker {ACCOUNT_ID}: Track name not in playlist → autoplay detected, advancing.")
                                        break
                                else:
                                    unknown_count = 0

                        # ── Status log every ~30s ─────────────────────────
                        if poll_num % 15 == 0:
                            sym = "▶" if is_playing else "⏸"
                            log.info(f"Worker {ACCOUNT_ID}: {sym} {(current_track or '?')[:35]} | seen={len(seen_tracks)}/{total_count} | {progress_pct:.0f}%")

                        # ── Persist last-known state (for Resume button) ─────
                        # Every ~5 polls (~10s) so the dashboard can resume from
                        # an up-to-date checkpoint if the bot is stopped mid-song.
                        if poll_num % 5 == 0 and current_track:
                            _save_last_state(
                                playlist_index=current_index,
                                playlist_id=playlist_id,
                                track_name=current_track,
                                progress_pct=progress_pct,
                            )

                        # ── Queue API fetch (retry with backoff on 429) ──────────
                        # Attempt at poll 30, 60, 120. If we get a Retry-After, honor it.
                        if (not queue_fetch_done and not playlist_track_uris and is_playing
                                and poll_num in (30, 60, 120)):
                            q_retry = _get_queue_info(page)
                            if q_retry and not q_retry.get("error") and q_retry.get("count", 0) > 0:
                                queue_fetch_done = True
                                playlist_track_uris = set(q_retry.get("track_uris", []))
                                playlist_track_names = set(q_retry.get("track_names", []))
                                q_count = q_retry["count"]
                                if q_count > total_count or total_count == 0:
                                    total_count = q_count
                                log.info(f"Worker {ACCOUNT_ID}: ✓ Queue API: {total_count} tracks ({len(playlist_track_uris)} URIs)")
                            elif q_retry and q_retry.get("error", "").startswith("HTTP 429"):
                                # Rate limited — stop trying for this playlist, rely on DOM count
                                queue_fetch_done = True
                                log.info(f"Worker {ACCOUNT_ID}: Queue API rate-limited — using DOM count ({total_count}) only")

                        # ── Auto-save: if context switched to another playlist during playback ──
                        if (context_url and playlist_id not in context_url
                                and "/playlist/" in context_url):
                            _auto_save_detected_playlist(page, playlist_id)

                        # ── Autoplay / playlist-end detection ─────────────

                        # 1. Context changed to a DIFFERENT playlist (explicit autoplay)
                        # Require a different /playlist/ URL — transient nulls don't count.
                        if (context_url and "/playlist/" in context_url
                                and playlist_id not in context_url and len(seen_tracks) > 1):
                            log.info(f"Worker {ACCOUNT_ID}: Context changed to {context_url[:50]} — autoplay detected, advancing.")
                            break

                        # 2. Context gone (null context) — ONLY a tie-breaker, never a
                        # primary signal. Spotify's DOM transiently reports null context
                        # during normal track transitions, which previously caused the
                        # bot to bail after 3 tracks. Now we require: null for 10+ polls
                        # AND we've already seen ≥ total_count tracks (playlist basically
                        # done) AND the URI is not in the playlist. Without URIs, we
                        # trust DOM progress and never advance on null context alone.
                        if (not context_url and is_playing
                                and total_count > 0 and len(seen_tracks) >= total_count
                                and playlist_track_uris and current_uri
                                and current_uri not in playlist_track_uris):
                            context_gone_count += 1
                            if context_gone_count >= 10:
                                log.info(f"Worker {ACCOUNT_ID}: Playlist done + autoplay URI detected — advancing.")
                                break
                        else:
                            context_gone_count = 0

                        # 2b. Name-based fallback: unknown track not in playlist names
                        if (playlist_track_names and not playlist_track_uris
                                and current_track and current_track not in playlist_track_names
                                and len(seen_tracks) > 1):
                            unknown_count += 1
                            if unknown_count >= 2:
                                log.info(f"Worker {ACCOUNT_ID}: Unknown track: {current_track[:30]} — autoplay detected, advancing.")
                                break
                        else:
                            unknown_count = 0

                        # 3. Seen more unique tracks than playlist has → autoplay.
                        # A no-shuffle/no-repeat playlist can only yield `total_count`
                        # distinct tracks; anything beyond that is Spotify injecting
                        # its radio. Fire as soon as we cross the line (no buffer).
                        if total_count > 0 and len(seen_tracks) > total_count:
                            log.info(f"Worker {ACCOUNT_ID}: Seen {len(seen_tracks)} tracks but playlist has {total_count} — autoplay detected, advancing.")
                            break

                        # 4. First-track loop detection (playlist repeated from start)
                        #    Mirrors manual bot: last_track_seen + back to first_track
                        if last_track_seen and first_track and current_track == first_track:
                            log.info(f"Worker {ACCOUNT_ID}: Playlist looped back to first track — advancing.")
                            break

                        # 5. Pause handling
                        if not is_playing:
                            pause_count += 1
                            # After last track + paused = playlist ended
                            if last_track_seen and pause_count >= 10:
                                log.info(f"Worker {ACCOUNT_ID}: Paused after last track — playlist complete.")
                                break
                            # Check queue after ~10s pause — if empty, playlist is done
                            # (handles case where user skipped to last track manually)
                            if pause_count == 5:
                                q_check = _get_queue_info(page)
                                if q_check and not q_check.get("error") and q_check.get("count", 999) <= 1:
                                    log.info(f"Worker {ACCOUNT_ID}: Playlist complete (paused + queue empty).")
                                    break
                            # User paused the song? Give them ~30s of grace, then
                            # auto-resume so the Docker node keeps playing. If they
                            # resume manually, pause_count resets below.
                            if pause_count >= 15:  # ~30s paused (at 2s poll base)
                                log.info(f"Worker {ACCOUNT_ID}: Paused ~30s — auto-resuming playback...")
                                _click_play(page, timeout=3000)
                                pause_count = 0
                            elif pause_count % 5 == 0:
                                log.info(f"Worker {ACCOUNT_ID}: ⏸ Paused — waiting...")
                        else:
                            pause_count = 0

                        # 6. Progress near end + many tracks seen = likely last track
                        if total_count > 0 and len(seen_tracks) >= total_count:
                            last_track_seen = True
                            if progress_pct > 95:
                                log.info(f"Worker {ACCOUNT_ID}: Last track finishing (progress {progress_pct:.0f}%)...")

                    # ── Human delay between playlists (autoplay keeps playing) ──
                    # Goals for this block:
                    #   1. Show the wall-clock time the next playlist will start.
                    #   2. Capture every unique autoplay track name that plays during
                    #      the gap (used for logging + auto-save detection).
                    #   3. Check for context changes every second (not every 15s) so
                    #      an autoplay-triggered new playlist is saved immediately.
                    #   4. If Spotify auto-pauses (user or idle), resume it so the
                    #      autoplay queue keeps flowing until the gap ends.
                    if not _shutdown and current_index + 1 < len(playlists):
                        next_pl = playlists[current_index + 1]
                        next_id = next_pl.split(":")[-1] if ":" in next_pl else next_pl.split("/")[-1].split("?")[0]
                        delay = random.randint(delay_profile["playlist_gap_min"], delay_profile["playlist_gap_max"])
                        start_ts = datetime.now()
                        target_ts = start_ts + timedelta(seconds=delay)
                        log.info(
                            f"Worker {ACCOUNT_ID}: ⏳ Waiting {delay}s — next playlist ({next_id}) at "
                            f"{target_ts.strftime('%H:%M:%S')} — autoplay continues..."
                        )
                        autoplay_tracks: list[str] = []    # ordered unique names
                        autoplay_seen: set[str] = set()
                        last_ap_track = None
                        ap_pause_count = 0
                        for sec in range(delay):
                            if _shutdown:
                                break
                            time.sleep(1)
                            _screenshot(page)

                            ap_state = _get_now_playing(page)
                            ap_track = ap_state.get("track") or ""
                            ap_artist = ap_state.get("artist") or ""
                            ap_playing = ap_state.get("playing", False)

                            # Capture every new autoplay track name as it plays
                            if ap_track and ap_track not in autoplay_seen:
                                autoplay_seen.add(ap_track)
                                autoplay_tracks.append(ap_track)
                                if ap_track != last_ap_track:
                                    log.info(
                                        f"Worker {ACCOUNT_ID}: ♫ Autoplay: {ap_track[:40]} — {ap_artist[:25]}"
                                    )
                                    last_ap_track = ap_track

                            # Every second: check if autoplay switched to a new playlist
                            # and auto-save it if so (previously only every 15s).
                            _auto_save_detected_playlist(page, playlist_id)

                            # Pause/resume during the gap: if Spotify stops, try to
                            # resume after ~20s so the queue keeps feeding screenshots.
                            if not ap_playing:
                                ap_pause_count += 1
                                if ap_pause_count >= 20:
                                    log.info(f"Worker {ACCOUNT_ID}: Autoplay paused {ap_pause_count}s — resuming...")
                                    _click_play(page, timeout=2000)
                                    ap_pause_count = 0
                            else:
                                ap_pause_count = 0

                            # Countdown every 15s showing wall-clock target
                            remaining = delay - sec
                            if sec % 15 == 0 and sec > 0:
                                log.info(
                                    f"Worker {ACCOUNT_ID}: ⏳ {remaining}s remaining "
                                    f"(next at {target_ts.strftime('%H:%M:%S')}) — "
                                    f"Autoplay: {(ap_track or '?')[:35]} — {(ap_artist or '')[:20]}"
                                )
                        if autoplay_tracks:
                            log.info(
                                f"Worker {ACCOUNT_ID}: Autoplay gap captured {len(autoplay_tracks)} tracks: "
                                + ", ".join(t[:25] for t in autoplay_tracks[:5])
                                + (f" (+{len(autoplay_tracks) - 5} more)" if len(autoplay_tracks) > 5 else "")
                            )
                        if not _shutdown:
                            log.info(f"Worker {ACCOUNT_ID}: ▶ Delay done — starting next playlist ({next_id})")

                    current_index += 1
                    _save_progress(current_index)

                if not _shutdown:
                    log.info(f"Worker {ACCOUNT_ID}: All playlists completed!")

            # ── Keep-alive loop (after playlists finish or if none configured) ─
            heartbeat_interval = 60
            last_check = time.time()
            log.info(f"Worker {ACCOUNT_ID}: Entering keep-alive loop.")

            while not _shutdown:
                time.sleep(1)
                _screenshot(page)

                if time.time() - last_check < heartbeat_interval:
                    continue
                last_check = time.time()

                try:
                    current_url = page.url
                    if "login" in current_url or "accounts.spotify.com" in current_url:
                        log.warning(f"Worker {ACCOUNT_ID}: Session expired — redirected to login.")
                        context.storage_state(path=SESSION_FILE + ".expired_bak")
                        log.error(f"Worker {ACCOUNT_ID}: Cannot recover. INTERACTIVE=1 required.")
                        browser.close()
                        sys.exit(2)
                    else:
                        log.info(f"Worker {ACCOUNT_ID}: ♡ Heartbeat OK — {current_url[:60]}")
                except Exception as e:
                    log.warning(f"Worker {ACCOUNT_ID}: Heartbeat error: {e}")

            log.info(f"Worker {ACCOUNT_ID}: Shutdown — saving session state...")
            try:
                context.storage_state(path=SESSION_FILE)
            except Exception:
                pass
            browser.close()
            log.info(f"Worker {ACCOUNT_ID}: Browser closed cleanly.")
    finally:
        # Clean up Xvfb virtual display
        if xvfb_proc:
            try:
                xvfb_proc.terminate()
                xvfb_proc.wait(timeout=5)
            except Exception:
                try:
                    xvfb_proc.kill()
                except Exception:
                    pass
            log.info(f"Worker {ACCOUNT_ID}: Xvfb stopped.")


# ─── Entry Point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if INTERACTIVE:
        run_interactive_setup()
    else:
        run_headless()
