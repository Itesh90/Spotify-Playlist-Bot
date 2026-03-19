import os
import re
import json
import time
import uuid
import threading
from datetime import datetime

from flask import Flask, request, jsonify, redirect, render_template, session
import spotipy
from spotipy.oauth2 import SpotifyOAuth

# ─── App Setup ────────────────────────────────────────────────────────────────

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "spotify-bot-secret-key-change-me")

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
TOKENS_DIR = os.path.join(DATA_DIR, "tokens")
os.makedirs(TOKENS_DIR, exist_ok=True)

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:5000")
REDIRECT_URI = f"{BASE_URL}/callback"
SCOPE = "user-read-playback-state user-modify-playback-state playlist-read-private playlist-modify-public user-read-currently-playing"

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
        acc["log"].append(entry)
        acc["log"] = acc["log"][-20:]
        save_account(account_id, acc)
    finally:
        if lock:
            lock.release()


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

def get_playlist_tracks(sp: spotipy.Spotify, playlist_uri: str, account_id: str = None) -> list[str]:
    playlist_id = playlist_uri.split(":")[-1]
    tracks = []

    if account_id:
        add_log(account_id, f"Fetching tracks for ID: {playlist_id}")

    try:
        playlist_data = sp.playlist(playlist_id)
        results = playlist_data.get("tracks", {})
    except Exception as e:
        if account_id:
            add_log(account_id, f"playlist fetch error: {e}")
        return tracks

    page = 1
    while results:
        items = results.get("items", [])
        null_count = 0

        for item in items:
            # Spotify sometimes uses "item" key instead of "track"
            track = item.get("track") or item.get("item")
            if track and track.get("uri") and track.get("type") == "track":
                tracks.append(track["uri"])
            else:
                null_count += 1
                if account_id and null_count <= 2:
                    t = item.get("track") or item.get("item")
                    t_type = t.get("type") if t else None
                    add_log(account_id, f"Skipped item type={t_type}")

        if account_id:
            add_log(account_id, f"Page {page}: {len(items)} items, {null_count} skipped, {len(tracks)} valid")

        if results.get("next"):
            try:
                results = sp.next(results)
                page += 1
            except Exception as e:
                if account_id:
                    add_log(account_id, f"Pagination error: {e}")
                break
        else:
            break

    if account_id:
        add_log(account_id, f"Total tracks found: {len(tracks)}")

    return tracks


def wait_for_device(sp: spotipy.Spotify, account_id: str, timeout: int = 30) -> bool:
    """Wait for an active Spotify device, polling every 5 seconds."""
    elapsed = 0
    while elapsed < timeout:
        try:
            devices = sp.devices()
            if devices and devices.get("devices"):
                return True
        except Exception:
            pass
        add_log(account_id, f"Waiting for active device... ({elapsed}s)")
        time.sleep(5)
        elapsed += 5
    return False


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

    # Wait for an active Spotify device
    if not wait_for_device(sp, account_id):
        add_log(account_id, "No active Spotify device found — open Spotify on any device")
        set_status(account_id, "error")
        return

    # Disable shuffle and repeat
    try:
        sp.shuffle(False)
        sp.repeat("off")
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
            tracks = get_playlist_tracks(sp, playlist_uri, account_id)
        except Exception as e:
            add_log(account_id, f"Failed to fetch tracks: {e}")
            set_status(account_id, "error")
            return

        if not tracks:
            add_log(account_id, f"Could not read track list — will play anyway with fallback detection")

        last_track_uri = tracks[-1] if tracks else None
        first_track_uri = tracks[0] if tracks else None
        track_set = set(tracks) if tracks else set()

        # Auto-follow playlist if not already saved
        try:
            sp.current_user_follow_playlist(playlist_id)
        except Exception:
            pass  # Non-critical, continue even if follow fails

        # Start playback
        try:
            sp.start_playback(context_uri=playlist_uri)
            time.sleep(1)
            sp.shuffle(False)
            sp.repeat("off")
        except Exception as e:
            add_log(account_id, f"Failed to start playback: {e}")
            set_status(account_id, "error")
            return

        set_status(account_id, "playing")

        # ── Polling loop ──
        last_track_seen = False
        pause_count = 0
        none_count = 0

        while not stop_flag.is_set():
            time.sleep(5)

            # Re-get spotify client to handle token refresh
            acc_fresh = load_account(account_id)
            if not acc_fresh:
                return
            sp = get_spotify(acc_fresh)
            if not sp:
                add_log(account_id, "Token lost during playback — re-authorize")
                set_status(account_id, "error")
                return

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
            if pb.get("item"):
                current_track_uri = pb["item"].get("uri")

            is_playing = pb.get("is_playing", False)
            context = pb.get("context")
            context_uri = context.get("uri") if context else None

            # Case 1: Context changed (Spotify autoplay kicked in)
            if context_uri and context_uri != playlist_uri:
                add_log(account_id, "Context changed (autoplay detected), advancing")
                break

            # Case 1b: Unknown track playing (not in playlist = autoplay injected)
            if track_set and current_track_uri and current_track_uri not in track_set and last_track_seen:
                add_log(account_id, "Unknown track detected (autoplay), advancing")
                break

            # Track if we've seen the last track
            if last_track_uri and current_track_uri == last_track_uri:
                last_track_seen = True

            # Case 2: Looped back to track 1 after last track was seen
            if last_track_seen and first_track_uri and current_track_uri == first_track_uri:
                progress = pb.get("progress_ms", 0)
                if progress < 5000:
                    add_log(account_id, "Playlist looped to start, advancing")
                    break

            # Case 3: Paused after last track was seen
            if last_track_seen and not is_playing:
                pause_count += 1
                if pause_count >= 2:
                    add_log(account_id, "Playback paused after last track, advancing")
                    break
            else:
                pause_count = 0

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


# ─── API Routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/accounts", methods=["GET"])
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


@app.route("/api/accounts", methods=["POST"])
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


@app.route("/api/accounts/<account_id>", methods=["DELETE"])
def api_delete_account(account_id):
    stop_bot(account_id)
    delete_account_files(account_id)
    return jsonify({"ok": True})


@app.route("/api/accounts/<account_id>/playlists", methods=["POST"])
def api_add_playlist(account_id):
    acc = load_account(account_id)
    if not acc:
        return jsonify({"error": "Account not found"}), 404
    data = request.json
    uri = data.get("uri", "").strip() if data else ""
    normalized = normalize_playlist_uri(uri)
    if not normalized:
        return jsonify({"error": "Invalid playlist URI or URL"}), 400
    if normalized in acc["playlists"]:
        return jsonify({"error": "Playlist already added"}), 400
    acc["playlists"].append(normalized)
    save_account(account_id, acc)
    return jsonify({"ok": True, "playlists": acc["playlists"]})


@app.route("/api/accounts/<account_id>/playlists/<int:playlist_index>", methods=["DELETE"])
def api_remove_playlist(account_id, playlist_index):
    acc = load_account(account_id)
    if not acc:
        return jsonify({"error": "Account not found"}), 404
    if playlist_index < 0 or playlist_index >= len(acc["playlists"]):
        return jsonify({"error": "Invalid playlist index"}), 400
    acc["playlists"].pop(playlist_index)
    save_account(account_id, acc)
    return jsonify({"ok": True, "playlists": acc["playlists"]})


@app.route("/api/accounts/<account_id>/start", methods=["POST"])
def api_start_bot(account_id):
    err = start_bot(account_id)
    if err:
        return jsonify({"error": err}), 400
    return jsonify({"ok": True})


@app.route("/api/accounts/<account_id>/stop", methods=["POST"])
def api_stop_bot(account_id):
    stop_bot(account_id)
    return jsonify({"ok": True})


@app.route("/api/start-all", methods=["POST"])
def api_start_all():
    accounts = load_all_accounts()
    results = {}
    for acc in accounts:
        err = start_bot(acc["id"])
        results[acc["id"]] = err or "started"
    return jsonify(results)


@app.route("/api/stop-all", methods=["POST"])
def api_stop_all():
    accounts = load_all_accounts()
    for acc in accounts:
        stop_bot(acc["id"])
    return jsonify({"ok": True})


# ─── OAuth Routes ─────────────────────────────────────────────────────────────

@app.route("/auth/<account_id>")
def auth_login(account_id):
    acc = load_account(account_id)
    if not acc:
        return "Account not found", 404
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
        oauth.get_access_token(code, as_dict=True)
    except Exception as e:
        return f"Auth failed: {e}", 500

    acc["authorized"] = True
    save_account(account_id, acc)
    add_log(account_id, "Authorization successful")
    return redirect("/")


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
