from flask import Flask, render_template, jsonify, request
import json, os, threading, time
import spotipy
from spotipy.oauth2 import SpotifyOAuth

app = Flask(__name__)

ACCOUNTS_FILE = "accounts.json"
TOKENS_DIR = "tokens"
REDIRECT_URI = os.environ.get("REDIRECT_URI", "http://localhost:5000/callback")
POLL_INTERVAL = 5  # seconds

os.makedirs(TOKENS_DIR, exist_ok=True)

bot_threads = {}   # account_id -> Thread
bot_status = {}    # account_id -> { state, current_playlist, index, log }
status_lock = threading.Lock()


# ─── Helpers ─────────────────────────────────────────────────────────────────

def load_accounts():
    if not os.path.exists(ACCOUNTS_FILE):
        return []
    with open(ACCOUNTS_FILE) as f:
        return json.load(f)


def save_accounts(accounts):
    with open(ACCOUNTS_FILE, "w") as f:
        json.dump(accounts, f, indent=2)


SPOTIFY_SCOPE = (
    "user-read-playback-state "
    "user-modify-playback-state "
    "playlist-modify-public "
    "playlist-modify-private "
    "playlist-read-private"
)


def get_sp(account):
    return spotipy.Spotify(auth_manager=SpotifyOAuth(
        client_id=account["client_id"],
        client_secret=account["client_secret"],
        redirect_uri=REDIRECT_URI,
        scope=SPOTIFY_SCOPE,
        cache_path=f"{TOKENS_DIR}/.cache-{account['id']}",
        open_browser=False,
    ))


def normalize_playlist_uri(raw):
    """Convert Spotify share URLs to URIs, or return as-is if already a URI."""
    raw = raw.strip()
    if raw.startswith("https://open.spotify.com/playlist/"):
        playlist_id = raw.split("/playlist/")[1].split("?")[0]
        return f"spotify:playlist:{playlist_id}"
    return raw


def get_active_device(sp, fallback_id=None):
    try:
        devices = sp.devices().get("devices", [])
        active = next((d["id"] for d in devices if d["is_active"]), None)
        if active:
            return active
        if devices:
            return devices[0]["id"]
    except Exception:
        pass
    return fallback_id


def ensure_playlist_followed(sp, playlist_uri):
    """Follow the playlist if the user hasn't already saved it."""
    try:
        playlist_id = playlist_uri.split(":")[-1]  # Extract ID from URI
        user_id = sp.current_user()["id"]
        already_following = sp.playlist_is_following(playlist_id, [user_id])
        if already_following and already_following[0]:
            return  # Already saved, nothing to do
        sp.current_user_follow_playlist(playlist_id)
    except Exception:
        pass  # Non-fatal — don't crash the bot over this


def get_playlist_track_uris(sp, playlist_uri):
    """Fetch all track URIs from a playlist. Returns a set of URI strings."""
    try:
        pl_id = playlist_uri.split(":")[-1]
        results = sp.playlist_tracks(pl_id, limit=100)
        uris = set()
        while results:
            for item in results.get("items", []):
                track = item.get("track")
                if track and track.get("uri"):
                    uris.add(track["uri"])
            if results.get("next"):
                results = sp.next(results)
            else:
                break
        return uris
    except Exception as e:
        return set()


# ─── Bot Worker ───────────────────────────────────────────────────────────────

def bot_worker(account):
    aid = account["id"]
    playlists = account.get("playlists", [])

    with status_lock:
        bot_status[aid] = {
            "state": "starting",
            "current_playlist": "",
            "index": 0,
            "log": [],
        }

    def log(msg):
        entry = f"[{time.strftime('%H:%M:%S')}] {msg}"
        with status_lock:
            if not isinstance(bot_status[aid].get("log"), list):
                bot_status[aid]["log"] = []
            bot_status[aid]["log"].append(entry)
            if len(bot_status[aid]["log"]) > 50:
                bot_status[aid]["log"].pop(0)

    def set_state(state, **kwargs):
        with status_lock:
            bot_status[aid]["state"] = state
            for key in ("current_playlist", "index"):
                if key in kwargs:
                    bot_status[aid][key] = kwargs[key]

    def should_stop():
        with status_lock:
            return bot_status[aid].get("state") in ("stopped", "error")

    if not playlists:
        set_state("error")
        log("No playlists configured. Add playlists before starting.")
        return

    try:
        sp = get_sp(account)

        # Validate token works
        try:
            sp.current_user()
        except EOFError:
            set_state("error")
            log("Not authorized. Click \"🔑 Authorize\" first, then Start.")
            return
        except spotipy.exceptions.SpotifyException as e:
            if "403" in str(e) or "PREMIUM_REQUIRED" in str(e):
                set_state("error")
                log("Spotify Premium is required to control playback.")
                return
            raise

        device_id = get_active_device(sp)
        if not device_id:
            set_state("error")
            log("No Spotify device found. Open Spotify on a device first.")
            return

        # Disable shuffle/repeat for clean sequential play
        try:
            sp.shuffle(False, device_id=device_id)
            sp.repeat("off", device_id=device_id)
        except Exception:
            pass  # Non-fatal — proceed regardless

        current_index = 0
        sp.start_playback(device_id=device_id, context_uri=playlists[current_index])
        current_context = playlists[current_index]
        ensure_playlist_followed(sp, current_context)
        set_state("playing", current_playlist=current_context, index=current_index)
        log(f"Started playlist {current_index + 1} of {len(playlists)}")

        # Fetch all track URIs for the current playlist
        playlist_track_uris = get_playlist_track_uris(sp, current_context)
        if playlist_track_uris:
            log(f"Loaded {len(playlist_track_uris)} track URIs for detection")
        else:
            log("⚠️ Could not load track URIs — re-authorize to enable autoplay detection")
        time.sleep(3)  # Let Spotify register playback

        def advance_to_next():
            """Advance to the next playlist. Returns True if advanced, False if all done."""
            nonlocal current_index, current_context, device_id, playlist_track_uris, prev_playing
            current_index += 1
            if current_index >= len(playlists):
                set_state("done")
                log("All playlists finished.")
                return False
            device_id = get_active_device(sp, fallback_id=device_id)
            sp.start_playback(device_id=device_id, context_uri=playlists[current_index])
            current_context = playlists[current_index]
            ensure_playlist_followed(sp, current_context)
            set_state("playing", current_playlist=current_context, index=current_index)
            log(f"Moved to playlist {current_index + 1} of {len(playlists)}")
            playlist_track_uris = get_playlist_track_uris(sp, current_context)
            log(f"Loaded {len(playlist_track_uris)} track URIs for detection")
            prev_playing = True
            time.sleep(3)
            return True

        # ── Main polling loop ─────────────────────────────────────────────────
        prev_playing = True
        null_count = 0
        while not should_stop():
            time.sleep(POLL_INTERVAL)

            if should_stop():
                break

            try:
                state = sp.current_playback()
            except Exception as e:
                log(f"Poll error: {e}")
                continue

            # ── Parse playback state ──────────────────────────────────────────
            if state is None:
                null_count += 1
                # Only advance after 3 consecutive nulls (15 sec) to avoid
                # false triggers from brief network hiccups
                if null_count >= 3 and prev_playing:
                    log("Playback stopped completely — advancing.")
                    null_count = 0
                    if not advance_to_next():
                        break
                continue

            null_count = 0  # Reset on valid response
            is_playing = state.get("is_playing", False)
            item = state.get("item")
            current_track_uri = item.get("uri") if item else None
            progress = state.get("progress_ms")
            duration = item.get("duration_ms") if item else None

            # ── Sync if user manually switched to another configured playlist ──
            ctx = state.get("context")
            state_context = ctx.get("uri") if isinstance(ctx, dict) else None
            if state_context and state_context != current_context and state_context in playlists:
                current_index = playlists.index(state_context)
                current_context = state_context
                playlist_track_uris = get_playlist_track_uris(sp, current_context)
                set_state("playing", current_playlist=current_context, index=current_index)
                log(f"Synced to playlist {current_index + 1} (user switched manually)")
                prev_playing = is_playing
                continue

            # ── Track-based autoplay detection (THE KEY FIX) ──────────────────
            if is_playing and current_track_uri and playlist_track_uris:
                if current_track_uri not in playlist_track_uris:
                    # The playing track is NOT in our playlist = autoplay kicked in
                    log(f"Autoplay detected! Track {current_track_uri} is not in playlist.")
                    if not advance_to_next():
                        break
                    continue

            # ── Fallback: natural end (paused near end of last track) ─────────
            near_end = (progress is not None and duration is not None
                        and duration > 0 and progress >= duration - 2000)
            if not is_playing and near_end:
                log("Playlist ended naturally (last track finished).")
                if not advance_to_next():
                    break
                continue

            # ── Track playing state for next cycle ────────────────────────────
            prev_playing = is_playing

    except spotipy.exceptions.SpotifyException as e:
        set_state("error")
        log(f"Spotify API error: {e}")
    except Exception as e:
        set_state("error")
        log(f"Unexpected error: {e}")


# ─── Flask Routes ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/callback")
def callback():
    """Spotify OAuth callback — exchange the code for a token."""
    code = request.args.get("code")
    error = request.args.get("error")
    state = request.args.get("state")  # account ID passed as state

    if error:
        return f"OAuth error: {error}", 400
    if not code:
        return "OAuth error: no code returned.", 400

    accounts = load_accounts()

    # Find the specific account by state (account ID)
    target_accounts = [a for a in accounts if a["id"] == state] if state else accounts
    if not target_accounts:
        target_accounts = accounts  # fallback: try all

    exchanged = False
    for account in target_accounts:
        try:
            auth_manager = SpotifyOAuth(
                client_id=account["client_id"],
                client_secret=account["client_secret"],
                redirect_uri=REDIRECT_URI,
                scope=SPOTIFY_SCOPE,
                cache_path=f"{TOKENS_DIR}/.cache-{account['id']}",
                open_browser=False,
            )
            auth_manager.get_access_token(code, as_dict=False)
            exchanged = True
            break
        except Exception:
            continue

    if not exchanged:
        return "OAuth error: could not exchange code. Make sure you added the correct Client ID and Secret.", 400

    return """
    <html><body style="background:#0a0a0a;color:#1db954;font-family:monospace;
    display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
    <div style="text-align:center">
        <div style="font-size:2rem;margin-bottom:1rem">&#10003;</div>
        <div>Spotify authentication successful.</div>
        <div style="color:#666;margin-top:.5rem;font-size:.85rem">
            You can close this tab and return to the dashboard.
        </div>
    </div></body></html>
    """


@app.route("/api/accounts")
def api_accounts():
    accounts = load_accounts()
    result = []
    for a in accounts:
        with status_lock:
            status = bot_status.get(a["id"], {
                "state": "idle",
                "current_playlist": "",
                "index": 0,
                "log": [],
            })
            status_copy = dict(status)
        result.append({
            "id": a["id"],
            "client_id": a["client_id"],
            "playlists": a.get("playlists", []),
            "status": status_copy,
        })
    return jsonify(result)


@app.route("/api/accounts", methods=["POST"])
def add_account():
    data = request.json
    if not data or not all(k in data for k in ("id", "client_id", "client_secret")):
        return jsonify({"error": "Missing required fields"}), 400
    accounts = load_accounts()
    if any(a["id"] == data["id"] for a in accounts):
        return jsonify({"error": "Account ID already exists"}), 409
    accounts.append({
        "id": data["id"],
        "client_id": data["client_id"],
        "client_secret": data["client_secret"],
        "playlists": data.get("playlists", []),
    })
    save_accounts(accounts)
    return jsonify({"ok": True})


@app.route("/api/accounts/<aid>/auth-url")
def get_auth_url(aid):
    """Generate the Spotify OAuth URL for a specific account."""
    accounts = load_accounts()
    account = next((a for a in accounts if a["id"] == aid), None)
    if not account:
        return jsonify({"error": "Account not found"}), 404
    auth_manager = SpotifyOAuth(
        client_id=account["client_id"],
        client_secret=account["client_secret"],
        redirect_uri=REDIRECT_URI,
        scope=SPOTIFY_SCOPE,
        cache_path=f"{TOKENS_DIR}/.cache-{account['id']}",
        open_browser=False,
        state=aid,  # Pass account ID as state so callback can identify it
    )
    url = auth_manager.get_authorize_url(state=aid)
    return jsonify({"url": url})


@app.route("/api/accounts/<aid>/token-status")
def get_token_status(aid):
    """Check if a cached token exists for the account."""
    cache_path = f"{TOKENS_DIR}/.cache-{aid}"
    if os.path.exists(cache_path):
        try:
            with open(cache_path) as f:
                token_info = json.load(f)
            has_token = bool(token_info.get("access_token"))
            return jsonify({"authorized": has_token})
        except Exception:
            pass
    return jsonify({"authorized": False})


@app.route("/api/accounts/<aid>", methods=["DELETE"])
def delete_account(aid):
    accounts = [a for a in load_accounts() if a["id"] != aid]
    save_accounts(accounts)
    with status_lock:
        if aid in bot_status:
            bot_status[aid]["state"] = "stopped"
    # Don't pop bot_status here — the thread needs it to exit cleanly
    return jsonify({"ok": True})


@app.route("/api/accounts/<aid>/playlists", methods=["POST"])
def update_playlists(aid):
    accounts = load_accounts()
    found = False
    for a in accounts:
        if a["id"] == aid:
            a["playlists"] = [normalize_playlist_uri(p) for p in request.json.get("playlists", [])]
            found = True
    if not found:
        return jsonify({"error": "Account not found"}), 404
    save_accounts(accounts)
    return jsonify({"ok": True})


@app.route("/api/bot/<aid>/start", methods=["POST"])
def start_bot(aid):
    accounts = load_accounts()
    account = next((a for a in accounts if a["id"] == aid), None)
    if not account:
        return jsonify({"error": "Account not found"}), 404
    if not account.get("playlists"):
        return jsonify({"error": "No playlists configured"}), 400

    # Check if token exists — user must Authorize first
    cache_path = f"{TOKENS_DIR}/.cache-{aid}"
    if not os.path.exists(cache_path):
        return jsonify({"error": "Not authorized. Click 🔑 Authorize first."}), 400

    if aid in bot_threads and bot_threads[aid].is_alive():
        return jsonify({"error": "Bot already running"}), 400
    t = threading.Thread(target=bot_worker, args=(account,), daemon=True)
    bot_threads[aid] = t
    t.start()
    return jsonify({"ok": True})


@app.route("/api/bot/<aid>/stop", methods=["POST"])
def stop_bot(aid):
    with status_lock:
        if aid in bot_status:
            bot_status[aid]["state"] = "stopped"
    return jsonify({"ok": True})


@app.route("/api/bot/<aid>/status")
def get_bot_status(aid):
    with status_lock:
        status = bot_status.get(aid, {"state": "idle", "log": []})
        return jsonify(dict(status))


@app.route("/api/bot/start-all", methods=["POST"])
def start_all_bots():
    accounts = load_accounts()
    started = []
    errors = []
    for account in accounts:
        aid = account["id"]
        with status_lock:
            current_state = bot_status.get(aid, {}).get("state", "idle")
        if current_state in ("playing", "starting"):
            continue  # Already running
        if not account.get("playlists"):
            errors.append(f"{aid}: no playlists")
            continue
        cache_path = f"{TOKENS_DIR}/.cache-{aid}"
        if not os.path.exists(cache_path):
            errors.append(f"{aid}: not authorized")
            continue
        if aid in bot_threads and bot_threads[aid].is_alive():
            continue
        t = threading.Thread(target=bot_worker, args=(account,), daemon=True)
        bot_threads[aid] = t
        t.start()
        started.append(aid)
    return jsonify({"started": started, "errors": errors})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
