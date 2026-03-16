from flask import Flask, render_template, jsonify, request, redirect
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


def get_sp(account):
    return spotipy.Spotify(auth_manager=SpotifyOAuth(
        client_id=account["client_id"],
        client_secret=account["client_secret"],
        redirect_uri=REDIRECT_URI,
        scope="user-read-playback-state user-modify-playback-state",
        cache_path=f"{TOKENS_DIR}/.cache-{account['id']}",
        open_browser=False,
    ))


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

        # Validate token works (may trigger auth URL print)
        try:
            sp.current_user()
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
        set_state("playing", current_playlist=current_context, index=current_index)
        log(f"Started playlist {current_index + 1} of {len(playlists)}")
        time.sleep(3)  # Let Spotify register playback

        # ── Main polling loop ─────────────────────────────────────────────────
        while not should_stop():
            time.sleep(POLL_INTERVAL)

            if should_stop():
                break

            try:
                state = sp.current_playback()
            except Exception as e:
                log(f"Poll error: {e}")
                continue

            if not state:
                is_playing = False
                context = current_context
            else:
                is_playing = state.get("is_playing", False)
                ctx = state.get("context")
                # DEBUG: Log context type and value for diagnosis
                print(f"[DEBUG] Context type: {type(ctx)}, value: {ctx}")
                if ctx is None:
                    context = None
                elif isinstance(ctx, dict):
                    context = ctx.get("uri")
                else:
                    # Handle unexpected context type
                    print(f"[DEBUG] Unexpected context type: {type(ctx)}")
                    context = str(ctx) if ctx else None

            # Playlist ended (paused and context matches last known playlist)
            if not is_playing and context == current_context:
                # DEBUG: Log playlist end detection
                print(f"[DEBUG] Playlist end detected: is_playing={is_playing}, context={context}, current_context={current_context}")
                current_index += 1
                if current_index >= len(playlists):
                    set_state("done")
                    log("All playlists finished.")
                    break

                device_id = get_active_device(sp, fallback_id=device_id)
                sp.start_playback(device_id=device_id, context_uri=playlists[current_index])
                current_context = playlists[current_index]
                set_state("playing", current_playlist=current_context, index=current_index)
                log(f"Moved to playlist {current_index + 1} of {len(playlists)}")

            # User manually changed context — sync bot state
            elif context and context != current_context:
                current_context = context
                with status_lock:
                    bot_status[aid]["current_playlist"] = context
                log("External context change detected, bot synced.")

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
    """Spotify OAuth callback — spotipy handles token exchange automatically."""
    code = request.args.get("code")
    if not code:
        return "OAuth error: no code returned.", 400
    return """
    <html><body style="background:#0a0a0a;color:#1db954;font-family:monospace;
    display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
    <div style="text-align:center">
        <div style="font-size:2rem;margin-bottom:1rem">✓</div>
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


@app.route("/api/accounts/<aid>", methods=["DELETE"])
def delete_account(aid):
    accounts = [a for a in load_accounts() if a["id"] != aid]
    save_accounts(accounts)
    with status_lock:
        bot_status.pop(aid, None)
    return jsonify({"ok": True})


@app.route("/api/accounts/<aid>/playlists", methods=["POST"])
def update_playlists(aid):
    accounts = load_accounts()
    found = False
    for a in accounts:
        if a["id"] == aid:
            a["playlists"] = request.json.get("playlists", [])
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
        if aid in bot_threads and bot_threads[aid].is_alive():
            continue
        t = threading.Thread(target=bot_worker, args=(account,), daemon=True)
        bot_threads[aid] = t
        t.start()
        started.append(aid)
    return jsonify({"started": started, "errors": errors})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
