import os
import requests
import re
import json
import time
import random
import uuid
import threading
from datetime import datetime, timedelta

from flask import Flask, request, jsonify, redirect, session
from flask_cors import CORS
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
import spotipy
from spotipy.oauth2 import SpotifyOAuth

# ─── App Setup ────────────────────────────────────────────────────────────────

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
app.config["SESSION_PERMANENT"] = True
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=365)
app.config["SESSION_COOKIE_SAMESITE"] = "None"   # Required for cross-domain cookies
app.config["SESSION_COOKIE_SECURE"] = True        # Required when SameSite=None
CORS(app, supports_credentials=True, origins=[os.environ.get("FRONTEND_URL", "http://localhost:3000")])

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
TOKENS_DIR = os.path.join(DATA_DIR, "tokens")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000").rstrip("/")
if FRONTEND_URL and not FRONTEND_URL.startswith("http"):
    FRONTEND_URL = f"https://{FRONTEND_URL}"
USERS_FILE = os.path.join(DATA_DIR, "users.json")
os.makedirs(TOKENS_DIR, exist_ok=True)

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:5000").rstrip("/")
if BASE_URL and not BASE_URL.startswith("http"):
    BASE_URL = f"https://{BASE_URL}"
REDIRECT_URI = f"{BASE_URL}/callback"
SCOPE = "user-read-playback-state user-modify-playback-state playlist-read-private playlist-read-collaborative playlist-modify-public playlist-modify-private user-library-modify user-read-currently-playing"

bot_threads: dict[str, threading.Thread] = {}
bot_stop_flags: dict[str, threading.Event] = {}
bot_locks: dict[str, threading.Lock] = {}


# ─── Data Layer ───────────────────────────────────────────────────────────────

def _account_path(account_id: str) -> str:
    return os.path.join(DATA_DIR, f"account_{account_id}.json")


def _token_path(account_id: str) -> str:
    return os.path.join(TOKENS_DIR, f"{account_id}.json")


def load_account(account_id: str) -> dict | None:
    path = _account_path(account_id)
    if not os.path.exists(path):
        return None
    with open(path, "r") as f:
        return json.load(f)


def save_account(account_id: str, data: dict):
    with open(_account_path(account_id), "w") as f:
        json.dump(data, f, indent=2)


def load_all_accounts() -> list[dict]:
    accounts = []
    if not os.path.exists(DATA_DIR):
        return accounts
    for fname in os.listdir(DATA_DIR):
        if fname.startswith("account_") and fname.endswith(".json"):
            with open(os.path.join(DATA_DIR, fname), "r") as f:
                accounts.append(json.load(f))
    return accounts


def delete_account_files(account_id: str):
    path = _account_path(account_id)
    if os.path.exists(path):
        os.remove(path)
    token_path = _token_path(account_id)
    if os.path.exists(token_path):
        os.remove(token_path)


def new_account(name: str, client_id: str, client_secret: str) -> dict:
    account_id = str(uuid.uuid4())[:8]
    data = {
        "id": account_id,
        "name": name,
        "client_id": client_id,
        "client_secret": client_secret,
        "playlists": [],
        "current_index": 0,
        "status": "idle",
        "authorized": False,
        "log": [],
    }
    save_account(account_id, data)
    return data


def add_log(account_id: str, message: str):
    lock = bot_locks.get(account_id)
    if lock:
        lock.acquire()
    try:
        acc = load_account(account_id)
        if not acc:
            return
        entry = {"time": datetime.now().strftime("%H:%M:%S"), "msg": message}
        acc.setdefault("log", []).insert(0, entry)
        acc["log"] = acc["log"][:100]  # keep last 100
        save_account(account_id, acc)
    finally:
        if lock:
            lock.release()

# ─── User Authentication ─────────────────────────────────────────────────────

def load_users() -> dict:
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_users(users: dict):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "Not authenticated"}), 401
        return f(*args, **kwargs)
    return decorated_function

# ─── Core Logic ───────────────────────────────────────────────────────────────


def set_status(account_id: str, status: str):
    lock = bot_locks.get(account_id)
    if lock:
        lock.acquire()
    try:
        acc = load_account(account_id)
        if not acc:
            return
        acc["status"] = status
        save_account(account_id, acc)
    finally:
        if lock:
            lock.release()


# ─── Playlist URI Normalization ───────────────────────────────────────────────

def normalize_playlist_uri(uri_or_url: str) -> str | None:
    uri_or_url = uri_or_url.strip()
    m = re.match(r"spotify:playlist:([a-zA-Z0-9]+)", uri_or_url)
    if m:
        return f"spotify:playlist:{m.group(1)}"
    # Handle URLs, stripping query params
    m = re.match(r"https?://open\.spotify\.com/playlist/([a-zA-Z0-9]+)", uri_or_url.split("?")[0])
    if m:
        return f"spotify:playlist:{m.group(1)}"
    return None


def extract_all_playlist_uris(text: str) -> list[str]:
    """Extract every Spotify playlist URI/URL from a block of text (e.g. bulk paste)."""
    found = []
    seen = set()
    # Match full URLs: https://open.spotify.com/playlist/ID
    for pid in re.findall(r"https?://open\.spotify\.com/playlist/([a-zA-Z0-9]+)", text):
        uri = f"spotify:playlist:{pid}"
        if uri not in seen:
            seen.add(uri)
            found.append(uri)
    # Match raw URIs: spotify:playlist:ID
    for pid in re.findall(r"spotify:playlist:([a-zA-Z0-9]+)", text):
        uri = f"spotify:playlist:{pid}"
        if uri not in seen:
            seen.add(uri)
            found.append(uri)
    return found


# ─── Auto-Save External Playlists ────────────────────────────────────────────

def _auto_save_playlist(account_id: str, playlist_uri: str, sp=None):
    """If playlist_uri is not already saved for this account, add it and follow it on Spotify."""
    acc = load_account(account_id)
    if not acc:
        return
    if playlist_uri not in acc.get("playlists", []):
        acc["playlists"].append(playlist_uri)
        save_account(account_id, acc)
        playlist_id = playlist_uri.split(":")[-1]
        add_log(account_id, f"Auto-saved new playlist: {playlist_id}")

        # Also follow/save the playlist on the actual Spotify account
        if sp:
            try:
                sp.current_user_follow_playlist(playlist_id)
                add_log(account_id, f"Followed playlist on Spotify: {playlist_id}")
            except Exception as e:
                add_log(account_id, f"Follow request failed for {playlist_id}: {e}")


# ─── Spotify Auth Helpers ─────────────────────────────────────────────────────

def get_oauth(account: dict) -> SpotifyOAuth:
    return SpotifyOAuth(
        client_id=account["client_id"],
        client_secret=account["client_secret"],
        redirect_uri=REDIRECT_URI,
        scope=SCOPE,
        cache_path=_token_path(account["id"]),
        show_dialog=True,
    )


def get_spotify(account: dict) -> spotipy.Spotify | None:
    oauth = get_oauth(account)
    token_info = oauth.get_cached_token()
    if not token_info:
        return None
    if oauth.is_token_expired(token_info):
        try:
            token_info = oauth.refresh_access_token(token_info["refresh_token"])
        except Exception:
            return None
    return spotipy.Spotify(auth=token_info["access_token"])


# ─── Bot Engine ──────────────────────────────────────────────────────────────────────

def get_playlist_tracks(sp: spotipy.Spotify, playlist_uri: str, account_id: str = None) -> tuple[list[str], int]:
    """Returns (track_uris, total_count). total_count may be > 0 even if track_uris is empty."""
    playlist_id = playlist_uri.split(":")[-1]
    tracks = []
    total_count = 0

    if account_id:
        add_log(account_id, f"Fetching tracks for ID: {playlist_id}")

    offset = 0
    limit = 100
    headers = {"Authorization": f"Bearer {sp._auth}"}

    while True:
        try:
            resp = requests.get(
                f"https://api.spotify.com/v1/playlists/{playlist_id}/items",
                headers=headers,
                params={"limit": limit, "offset": offset},
                timeout=10
            )
            resp.raise_for_status()
            results = resp.json()
        except Exception as e:
            if account_id:
                add_log(account_id, f"Track list restricted, using count-based detection")
            # Fallback: get track count from playlist metadata (no fields filter)
            try:
                meta_resp = requests.get(
                    f"https://api.spotify.com/v1/playlists/{playlist_id}",
                    headers=headers, timeout=10
                )
                if meta_resp.status_code == 200:
                    meta = meta_resp.json()
                    total_count = meta.get("tracks", {}).get("total", 0)
                    if account_id:
                        add_log(account_id, f"Playlist has {total_count} tracks (from metadata)")
            except Exception:
                pass
            break

        items = results.get("items", []) if isinstance(results, dict) else []
        if not items:
            break

        for item in items:
            track = item.get("track") or item.get("item")
            if track and track.get("uri"):
                tracks.append(track["uri"])

        if account_id:
            add_log(account_id, f"Fetched {len(items)} items (offset {offset}), {len(tracks)} tracks total")

        next_url = results.get("next") if isinstance(results, dict) else None
        if next_url:
            offset += limit
        else:
            break

    total_count = max(total_count, len(tracks))
    if account_id:
        add_log(account_id, f"Total tracks found: {len(tracks)}, playlist size: {total_count}")

    return tracks, total_count


def get_device_id(sp: spotipy.Spotify, account_id: str, timeout: int = 30) -> str | None:
    """Wait for a Spotify device and return its ID."""
    elapsed = 0
    while elapsed < timeout:
        try:
            result = sp.devices()
            device_list = result.get("devices", []) if result else []
            for d in device_list:
                if not d.get("is_restricted", False):
                    add_log(account_id, f"Found device: {d.get('name')} ({d.get('type')})")
                    return d["id"]
            if device_list:
                names = [d.get('name', '?') for d in device_list]
                add_log(account_id, f"Devices found but restricted: {names}")
        except Exception as e:
            err = str(e)
            if "not registered" in err.lower():
                add_log(account_id, "Account not registered — add this email in Developer Dashboard → User Management")
                return None
            add_log(account_id, f"Device check error: {e}")
        add_log(account_id, f"Waiting for Spotify device... ({elapsed}s)")
        time.sleep(5)
        elapsed += 5
    return None


def run_bot(account_id: str):
    stop_flag = bot_stop_flags.get(account_id)
    if not stop_flag:
        return

    acc = load_account(account_id)
    if not acc:
        return

    if not acc["playlists"]:
        add_log(account_id, "No playlists to play")
        set_status(account_id, "error")
        return

    sp = get_spotify(acc)
    if not sp:
        add_log(account_id, "Not authorized — click Authorize")
        set_status(account_id, "error")
        return

    set_status(account_id, "starting")
    add_log(account_id, "Bot starting...")

    # Find a Spotify device to play on
    device_id = get_device_id(sp, account_id)
    if not device_id:
        add_log(account_id, "No Spotify device found — open Spotify on your phone, PC or go to open.spotify.com")
        set_status(account_id, "error")
        return

    # Disable shuffle and repeat
    try:
        sp.shuffle(False, device_id=device_id)
        sp.repeat("off", device_id=device_id)
    except Exception as e:
        add_log(account_id, f"Could not set shuffle/repeat: {e}")

    current_index = acc.get("current_index", 0)
    if current_index >= len(acc["playlists"]):
        current_index = 0

    while not stop_flag.is_set() and current_index < len(acc["playlists"]):
        playlist_uri = acc["playlists"][current_index]
        playlist_id = playlist_uri.split(":")[-1]
        add_log(account_id, f"Playing playlist {current_index + 1}/{len(acc['playlists'])} ({playlist_id})")

        # Re-read token in case it was refreshed
        sp = get_spotify(load_account(account_id))
        if not sp:
            add_log(account_id, "Token expired — re-authorize")
            set_status(account_id, "error")
            return

        # Get playlist track list for end detection
        try:
            tracks, total_count = get_playlist_tracks(sp, playlist_uri, account_id)
        except Exception as e:
            add_log(account_id, f"Failed to fetch tracks: {e}")
            tracks, total_count = [], 0

        if not tracks and total_count > 0:
            add_log(account_id, f"Track list restricted — using count-based detection ({total_count} tracks)")
        elif not tracks:
            add_log(account_id, f"Could not read track list — will play anyway with fallback detection")

        last_track_uri = tracks[-1] if tracks else None
        first_track_uri = tracks[0] if tracks else None
        track_set = set(tracks) if tracks else set()

        # Auto-follow playlist
        try:
            sp.current_user_follow_playlist(playlist_id)
            add_log(account_id, f"Auto-followed playlist: {playlist_id}")
        except Exception as e:
            add_log(account_id, f"Follow request failed for {playlist_id}: {e}")

        # Re-detect device (may have gone idle during delay)
        device_id = get_device_id(sp, account_id, timeout=30)
        if not device_id:
            add_log(account_id, "No device found before playback — skipping playlist")
            current_index += 1
            _save_index(account_id, current_index)
            continue

        # Start playback with retry
        started = False
        for attempt in range(3):
            try:
                sp.start_playback(context_uri=playlist_uri, device_id=device_id)
                time.sleep(2)
                try:
                    sp.shuffle(False, device_id=device_id)
                    sp.repeat("off", device_id=device_id)
                except Exception:
                    pass
                started = True
                break
            except Exception as e:
                add_log(account_id, f"Playback start attempt {attempt+1}/3 failed: {e}")
                if attempt < 2:
                    time.sleep(3)
                    # Re-detect device on retry
                    device_id = get_device_id(sp, account_id, timeout=15) or device_id

        if not started:
            add_log(account_id, "Failed to start playback after 3 attempts — skipping playlist")
            current_index += 1
            _save_index(account_id, current_index)
            continue

        # If track list is empty, extract from player queue (bypasses playlist restrictions)
        if not tracks:
            try:
                q_resp = requests.get(
                    "https://api.spotify.com/v1/me/player/queue",
                    headers={"Authorization": f"Bearer {sp._auth}"},
                    timeout=10
                )
                if q_resp.status_code == 200:
                    q_data = q_resp.json()
                    current = q_data.get("currently_playing")
                    queue_items = q_data.get("queue", [])
                    if current and current.get("uri"):
                        tracks = [current["uri"]]
                    for qi in queue_items:
                        uri = qi.get("uri")
                        if uri and uri not in tracks:
                            tracks.append(uri)
                    if tracks:
                        total_count = len(tracks)
                        last_track_uri = tracks[-1]
                        first_track_uri = tracks[0]
                        track_set = set(tracks)
                        add_log(account_id, f"Extracted {total_count} tracks from player queue")
                    else:
                        add_log(account_id, "Queue empty — using fallback detection")
            except Exception as e:
                add_log(account_id, f"Queue read failed: {e}")

        set_status(account_id, "playing")

        # ── Polling loop ──
        last_track_seen = False
        pause_count = 0
        none_count = 0
        unknown_count = 0
        poll_num = 0
        seen_track_uris = set()
        prev_track_uri = None
        auth_header = {"Authorization": f"Bearer {sp._auth}"}

        while not stop_flag.is_set():
            time.sleep(2)
            poll_num += 1

            # Re-get spotify client to handle token refresh
            acc_fresh = load_account(account_id)
            if not acc_fresh:
                return
            sp = get_spotify(acc_fresh)
            if not sp:
                add_log(account_id, "Token lost during playback — re-authorize")
                set_status(account_id, "error")
                return
            auth_header = {"Authorization": f"Bearer {sp._auth}"}

            try:
                pb = sp.current_playback()
            except Exception as e:
                add_log(account_id, f"Playback API error: {e}")
                none_count += 1
                if none_count >= 3:
                    add_log(account_id, "Playback unreachable (3x), advancing")
                    break
                continue

            # Case: playback returns None (device off/disconnected)
            if pb is None:
                none_count += 1
                if none_count >= 3:
                    add_log(account_id, "No playback data (3x), advancing")
                    break
                continue
            else:
                none_count = 0

            current_track_uri = None
            current_track_name = "?"
            duration = 0
            if pb.get("item"):
                current_track_uri = pb["item"].get("uri")
                current_track_name = pb["item"].get("name", "?")
                duration = pb["item"].get("duration_ms", 0)

            is_playing = pb.get("is_playing", False)
            progress = pb.get("progress_ms", 0)
            context = pb.get("context")
            context_uri = context.get("uri") if context else None

            # Track unique songs seen
            if current_track_uri:
                seen_track_uris.add(current_track_uri)

            # Detect track change — check queue on transition
            track_changed = (current_track_uri and current_track_uri != prev_track_uri and prev_track_uri is not None)
            prev_track_uri = current_track_uri

            if track_changed:
                add_log(account_id, f"Now playing: {current_track_name[:35]} ({len(seen_track_uris)}/{total_count})")
                # Check queue for autoplay
                try:
                    q_resp = requests.get("https://api.spotify.com/v1/me/player/queue", headers=auth_header, timeout=5)
                    if q_resp.status_code == 200:
                        q_data = q_resp.json()
                        queue_items = q_data.get("queue", [])
                        if queue_items and track_set:
                            # Only advance if current track is ALSO not in playlist (autoplay already playing)
                            current_in_playlist = current_track_uri in track_set
                            next_uri = queue_items[0].get("uri", "")
                            if not current_in_playlist and next_uri not in track_set:
                                add_log(account_id, "Autoplay active — advancing")
                                break
                except Exception:
                    pass

            # Log status every ~30 seconds (15 polls at 2s)
            if poll_num % 15 == 0:
                add_log(account_id, f"♪ {current_track_name[:30]} | {'▶' if is_playing else '⏸'} | seen={len(seen_track_uris)}/{total_count}")

            # Auto-save: if user switched to an external playlist, save it
            if (context_uri
                    and context_uri != playlist_uri
                    and context_uri.startswith("spotify:playlist:")):
                _auto_save_playlist(account_id, context_uri, sp)

            # Case 1: Context changed (Spotify autoplay kicked in)
            if context_uri and context_uri != playlist_uri:
                add_log(account_id, "Context changed (autoplay detected), advancing")
                break

            # Case 1c: Context gone (null context = autoplay radio)
            if context is None and is_playing and len(seen_track_uris) > 1:
                add_log(account_id, "Context lost (autoplay radio), advancing")
                break

            # Case 1b: Unknown track (not in playlist = autoplay injected)
            if track_set and current_track_uri and current_track_uri not in track_set:
                unknown_count += 1
                if unknown_count >= 2:
                    add_log(account_id, f"Unknown track detected: {current_track_name[:30]}, advancing")
                    break
            else:
                unknown_count = 0

            # Case 1d: Seen more unique tracks than playlist has (fallback count detection)
            if total_count > 0 and not track_set and len(seen_track_uris) > total_count:
                add_log(account_id, f"Seen {len(seen_track_uris)} tracks but playlist has {total_count} — autoplay detected, advancing")
                break

            # Track if we've seen the last track
            if last_track_uri and current_track_uri == last_track_uri:
                if not last_track_seen:
                    add_log(account_id, "Last track reached")
                last_track_seen = True

            # Case 2: Looped back to track 1 after last track was seen
            if last_track_seen and first_track_uri and current_track_uri == first_track_uri:
                if progress < 5000:
                    add_log(account_id, "Playlist looped to start, advancing")
                    break

            # Case 3: Paused handling
            if not is_playing:
                pause_count += 1
                # Only advance if paused AFTER the last track finished
                if last_track_seen and pause_count >= 2:
                    add_log(account_id, "Playback paused after last track, advancing")
                    break
                # Otherwise just wait — user paused mid-playlist, log every ~60s
                if pause_count % 30 == 0:
                    add_log(account_id, "⏸ Paused — waiting for resume...")
            else:
                pause_count = 0

        # Humanized delay before next playlist (10-30s random)
        delay = random.randint(10, 30)
        add_log(account_id, f"Waiting {delay}s before next playlist...")
        for _ in range(delay):
            if stop_flag.is_set():
                break
            time.sleep(1)

        # Advance to next playlist
        current_index += 1
        _save_index(account_id, current_index)

    # All playlists done or stopped
    if stop_flag.is_set():
        add_log(account_id, "Bot stopped by user")
        set_status(account_id, "idle")
    else:
        add_log(account_id, "All playlists completed!")
        set_status(account_id, "done")


def _save_index(account_id: str, index: int):
    lock = bot_locks.get(account_id)
    if lock:
        lock.acquire()
    try:
        acc = load_account(account_id)
        if acc:
            acc["current_index"] = index
            save_account(account_id, acc)
    finally:
        if lock:
            lock.release()


def start_bot(account_id: str) -> str | None:
    if account_id in bot_threads and bot_threads[account_id].is_alive():
        return "Bot is already running"

    acc = load_account(account_id)
    if not acc:
        return "Account not found"
    if not acc.get("authorized"):
        msg = "Account not authorized — click Authorize first"
        add_log(account_id, msg)
        set_status(account_id, "error")
        return msg
    if not acc["playlists"]:
        msg = "No playlists added"
        add_log(account_id, msg)
        set_status(account_id, "error")
        return msg

    # Reset state
    acc["current_index"] = 0
    acc["status"] = "starting"
    save_account(account_id, acc)

    stop_flag = threading.Event()
    bot_stop_flags[account_id] = stop_flag
    if account_id not in bot_locks:
        bot_locks[account_id] = threading.Lock()

    t = threading.Thread(target=run_bot, args=(account_id,), daemon=True)
    bot_threads[account_id] = t
    t.start()
    return None


def stop_bot(account_id: str) -> str | None:
    flag = bot_stop_flags.get(account_id)
    if flag:
        flag.set()
    if account_id in bot_threads:
        bot_threads[account_id].join(timeout=10)
        del bot_threads[account_id]
    if account_id in bot_stop_flags:
        del bot_stop_flags[account_id]
    set_status(account_id, "idle")
    return None


# ─── API Routes & Auth ───────────────────────────────────────────────────────────────

@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.json or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")
    users = load_users()
    
    # Auto-create admin if no users exist
    if not users:
        users["admin"] = {"password": generate_password_hash("admin")}
        save_users(users)
        
    if username in users and check_password_hash(users[username]["password"], password):
        session.permanent = True
        session["user_id"] = username
        return jsonify({"ok": True, "user": username})
    return jsonify({"error": "Invalid username or password"}), 401

@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.pop("user_id", None)
    return jsonify({"ok": True})

@app.route("/api/me", methods=["GET"])
def api_me():
    user = session.get("user_id")
    if user:
        return jsonify({"user": user})
    return jsonify({"user": None}), 401


@app.route("/api/accounts", methods=["GET"])
@login_required
def api_list_accounts():
    accounts = load_all_accounts()
    safe = []
    for a in accounts:
        running = account_id_running(a["id"])
        safe.append({
            "id": a["id"],
            "name": a["name"],
            "playlists": a["playlists"],
            "current_index": a["current_index"],
            "status": a["status"] if not running else a["status"],
            "authorized": a.get("authorized", False),
            "log": a.get("log", []),
            "running": running,
        })
    return jsonify(safe)


def account_id_running(account_id: str) -> bool:
    return account_id in bot_threads and bot_threads[account_id].is_alive()


@app.route("/api/add_account", methods=["POST"])
@login_required
def api_add_account():
    data = request.json
    if not data:
        return jsonify({"error": "No data provided"}), 400
    name = data.get("name", "").strip()
    client_id = data.get("client_id", "").strip()
    client_secret = data.get("client_secret", "").strip()
    if not name or not client_id or not client_secret:
        return jsonify({"error": "name, client_id, client_secret are required"}), 400
    acc = new_account(name, client_id, client_secret)
    return jsonify(acc), 201


@app.route("/api/delete_account/<account_id>", methods=["DELETE"])
@login_required
def api_delete_account(account_id):
    stop_bot(account_id)
    delete_account_files(account_id)
    return jsonify({"ok": True})


@app.route("/api/add_playlist/<account_id>", methods=["POST"])
@login_required
def api_add_playlist(account_id):
    acc = load_account(account_id)
    if not acc:
        return jsonify({"error": "Account not found"}), 404
    data = request.json
    raw = data.get("uri", "") if data else ""

    # Extract all playlist URIs from the pasted text (bulk paste support)
    uris = extract_all_playlist_uris(raw)

    # Fallback: single URI via normalize (handles edge cases)
    if not uris:
        normalized = normalize_playlist_uri(raw)
        if normalized:
            uris = [normalized]

    if not uris:
        return jsonify({"error": "No valid Spotify playlist URLs or URIs found"}), 400

    added = []
    skipped = []
    for uri in uris:
        if uri in acc["playlists"]:
            skipped.append(uri)
        else:
            acc["playlists"].append(uri)
            added.append(uri)

    save_account(account_id, acc)
    return jsonify({
        "ok": True,
        "added": len(added),
        "skipped": len(skipped),
        "playlists": acc["playlists"],
    })


@app.route("/api/remove_playlist/<account_id>/<int:playlist_index>", methods=["DELETE"])
@login_required
def api_remove_playlist(account_id, playlist_index):
    acc = load_account(account_id)
    if not acc:
        return jsonify({"error": "Account not found"}), 404
    if playlist_index < 0 or playlist_index >= len(acc["playlists"]):
        return jsonify({"error": "Invalid playlist index"}), 400
    acc["playlists"].pop(playlist_index)
    save_account(account_id, acc)
    return jsonify({"ok": True, "playlists": acc["playlists"]})


@app.route("/api/start/<account_id>", methods=["POST"])
@login_required
def api_start_bot(account_id):
    err = start_bot(account_id)
    if err:
        return jsonify({"error": err}), 400
    return jsonify({"ok": True})


@app.route("/api/stop/<account_id>", methods=["POST"])
@login_required
def api_stop_bot(account_id):
    stop_bot(account_id)
    return jsonify({"ok": True})


@app.route("/api/start-all", methods=["POST"])
@login_required
def api_start_all():
    accounts = load_all_accounts()
    results = {}
    for i, acc in enumerate(accounts):
        if i > 0:
            delay = random.randint(15, 45)
            add_log(acc["id"], f"Staggered start: waiting {delay}s before this account...")
            time.sleep(delay)
        err = start_bot(acc["id"])
        results[acc["id"]] = err or "started"
    return jsonify(results)


@app.route("/api/stop-all", methods=["POST"])
@login_required
def api_stop_all():
    accounts = load_all_accounts()
    for acc in accounts:
        stop_bot(acc["id"])
    return jsonify({"ok": True})


# ─── OAuth Routes ─────────────────────────────────────────────────────────────

@app.route("/authorize/<account_id>")
def auth_login(account_id):
    acc = load_account(account_id)
    if not acc:
        return jsonify({"error": "Account not found"}), 404
    oauth = get_oauth(acc)
    auth_url = oauth.get_authorize_url(state=account_id)
    return redirect(auth_url)


@app.route("/callback")
def auth_callback():
    code = request.args.get("code")
    state = request.args.get("state")
    if not code or not state:
        return "Missing code or state", 400

    account_id = state
    acc = load_account(account_id)
    if not acc:
        return "Account not found", 404

    oauth = get_oauth(acc)
    try:
        oauth.get_access_token(code)
    except Exception as e:
        return f"Auth failed: {e}", 500

    acc["authorized"] = True
    save_account(account_id, acc)
    add_log(account_id, "Authorization successful")
    return redirect(FRONTEND_URL)


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
