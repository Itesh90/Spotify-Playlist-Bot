# Spotify Playlist Bot — Full Build Prompt

## What You Are Building

A **multi-account Spotify playlist automation bot** with a web dashboard. The bot watches Spotify playback on multiple accounts simultaneously. When a playlist finishes on any account, it automatically starts the next playlist in that account's queue — without any manual intervention.

This is a **production-grade system** meant to run 24/7 on a VPS server, managing N Spotify accounts from a single dashboard at `http://YOUR_SERVER_IP:5000`.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Bot logic | Python 3.10+ |
| Spotify API | `spotipy` library (OAuth 2.0) |
| Web server | Flask |
| Concurrency | Python `threading` (one thread per account) |
| Frontend | Vanilla HTML/CSS/JS (single file, no framework) |
| Config storage | `accounts.json` (flat file) |
| Token storage | `tokens/` directory (one `.cache-{id}` file per account) |
| Deployment | Any Linux VPS or localhost |

No database. No Docker. No build step. Runs with two commands.

---

## Project File Structure

Build exactly this structure — no more, no less:

```
spotify_bot/
├── app.py                  # Flask server + bot worker logic (single file)
├── accounts.json           # Auto-created on first account add
├── requirements.txt        # flask, spotipy
├── tokens/                 # Auto-created, stores OAuth cache files
│   ├── .cache-client_1
│   └── .cache-client_2
└── templates/
    └── index.html          # Full dashboard UI (single HTML file)
```

---

## Core Concepts — Read Before Building

### How Spotify API Playback Works

The Spotify Web API endpoint `GET /me/player` returns the current playback state for an account. Key fields:

```json
{
  "is_playing": true,
  "context": {
    "uri": "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"
  },
  "item": { "name": "Song Title" }
}
```

- `is_playing: false` + `context.uri` matches the last known playlist = **playlist ended**
- `context.uri` changed to something else = **user manually switched**
- `null` response = **no active playback on any device**

### How Multi-Account Works

Each Spotify account gets its own:
1. OAuth token cached at `tokens/.cache-{account_id}`
2. `spotipy.Spotify` instance authenticated independently
3. Python thread running its own polling loop
4. Status entry in the shared `bot_status` dict

Accounts never interfere with each other. One bot instance = one thread = one account.

### Important Spotify Limitation

**Spotify only allows one active playback device per account at a time.** If two devices play on the same account, the second one takes over and pauses the first. This is why each client needs a separate Spotify account.

---

## Part 1 — Build `app.py`

`app.py` contains everything: Flask routes, bot worker function, account management. Single file.

### Imports and Setup

```python
from flask import Flask, render_template, jsonify, request
import json, os, threading, time
import spotipy
from spotipy.oauth2 import SpotifyOAuth

app = Flask(__name__)

ACCOUNTS_FILE = "accounts.json"
TOKENS_DIR = "tokens"
REDIRECT_URI = "http://localhost:5000/callback"
POLL_INTERVAL = 5  # seconds between Spotify API polls

os.makedirs(TOKENS_DIR, exist_ok=True)

bot_threads = {}     # account_id -> Thread object
bot_status = {}      # account_id -> { state, current_playlist, index, log }
```

### Helper Functions

```python
def load_accounts():
    if not os.path.exists(ACCOUNTS_FILE):
        return []
    with open(ACCOUNTS_FILE) as f:
        return json.load(f)

def save_accounts(accounts):
    with open(ACCOUNTS_FILE, "w") as f:
        json.dump(accounts, f, indent=2)

def get_sp(account):
    """Create an authenticated Spotify client for one account."""
    return spotipy.Spotify(auth_manager=SpotifyOAuth(
        client_id=account["client_id"],
        client_secret=account["client_secret"],
        redirect_uri=REDIRECT_URI,
        scope="user-read-playback-state user-modify-playback-state",
        cache_path=f"{TOKENS_DIR}/.cache-{account['id']}"
    ))
```

### Bot Worker Function

This is the core logic. One instance runs per account in its own thread.

```python
def bot_worker(account):
    aid = account["id"]
    playlists = account.get("playlists", [])

    # Initialize status for this account
    bot_status[aid] = {
        "state": "starting",
        "current_playlist": "",
        "index": 0,
        "log": []
    }

    def log(msg):
        """Append to log, keep last 50 lines."""
        bot_status[aid]["log"].append(msg)
        if len(bot_status[aid]["log"]) > 50:
            bot_status[aid]["log"].pop(0)

    try:
        sp = get_sp(account)

        # Find active device
        devices = sp.devices()
        device_id = next(
            (d["id"] for d in devices.get("devices", []) if d["is_active"]),
            None
        )
        # Fallback: use first available device even if not active
        if not device_id and devices.get("devices"):
            device_id = devices["devices"][0]["id"]

        if not device_id:
            bot_status[aid]["state"] = "error"
            log("No Spotify device found. Open Spotify on a device first.")
            return

        # Disable shuffle and repeat so playlist plays straight through
        sp.shuffle(False, device_id=device_id)
        sp.repeat("off", device_id=device_id)

        # Start first playlist
        current_index = 0
        sp.start_playback(device_id=device_id, context_uri=playlists[current_index])
        current_context = playlists[current_index]

        bot_status[aid].update({
            "state": "playing",
            "current_playlist": current_context,
            "index": current_index
        })
        log(f"Started playlist {current_index + 1} of {len(playlists)}")
        time.sleep(3)  # Give Spotify time to register playback

        # Main polling loop
        while bot_status[aid].get("state") not in ("stopped", "error"):
            time.sleep(POLL_INTERVAL)

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
                context = ctx.get("uri") if ctx else None

            # --- Detect: playlist ended ---
            if not is_playing and context == current_context:
                current_index += 1

                if current_index >= len(playlists):
                    bot_status[aid]["state"] = "done"
                    log("All playlists finished.")
                    break

                # Start next playlist
                device_id = next(
                    (d["id"] for d in sp.devices().get("devices", []) if d["is_active"]),
                    device_id  # Keep last known device as fallback
                )
                sp.start_playback(device_id=device_id, context_uri=playlists[current_index])
                current_context = playlists[current_index]
                bot_status[aid].update({
                    "current_playlist": current_context,
                    "index": current_index
                })
                log(f"Moved to playlist {current_index + 1} of {len(playlists)}")

            # --- Detect: user manually changed playlist ---
            elif context and context != current_context:
                current_context = context
                bot_status[aid]["current_playlist"] = context
                log("External context change detected, bot synced.")

    except Exception as e:
        bot_status[aid]["state"] = "error"
        bot_status[aid].setdefault("log", []).append(str(e))
```

### Flask API Routes

Build all of these routes:

```
GET  /                          → serve dashboard HTML
GET  /api/accounts              → list all accounts with their bot status
POST /api/accounts              → add new account
DELETE /api/accounts/<id>       → remove account
POST /api/accounts/<id>/playlists → update playlist queue for an account
POST /api/bot/<id>/start        → start bot for account
POST /api/bot/<id>/stop         → stop bot for account
GET  /api/bot/<id>/status       → get current status for account
```

```python
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/accounts")
def api_accounts():
    accounts = load_accounts()
    result = []
    for a in accounts:
        status = bot_status.get(a["id"], {
            "state": "idle",
            "current_playlist": "",
            "index": 0,
            "log": []
        })
        result.append({**a, "status": status})
    return jsonify(result)

@app.route("/api/accounts", methods=["POST"])
def add_account():
    data = request.json
    accounts = load_accounts()
    accounts.append({
        "id": data["id"],
        "client_id": data["client_id"],
        "client_secret": data["client_secret"],
        "playlists": data.get("playlists", [])
    })
    save_accounts(accounts)
    return jsonify({"ok": True})

@app.route("/api/accounts/<aid>", methods=["DELETE"])
def delete_account(aid):
    accounts = [a for a in load_accounts() if a["id"] != aid]
    save_accounts(accounts)
    bot_status.pop(aid, None)
    return jsonify({"ok": True})

@app.route("/api/accounts/<aid>/playlists", methods=["POST"])
def update_playlists(aid):
    accounts = load_accounts()
    for a in accounts:
        if a["id"] == aid:
            a["playlists"] = request.json.get("playlists", [])
    save_accounts(accounts)
    return jsonify({"ok": True})

@app.route("/api/bot/<aid>/start", methods=["POST"])
def start_bot(aid):
    accounts = load_accounts()
    account = next((a for a in accounts if a["id"] == aid), None)
    if not account:
        return jsonify({"error": "Account not found"}), 404
    if aid in bot_threads and bot_threads[aid].is_alive():
        return jsonify({"error": "Bot already running"}), 400
    t = threading.Thread(target=bot_worker, args=(account,), daemon=True)
    bot_threads[aid] = t
    t.start()
    return jsonify({"ok": True})

@app.route("/api/bot/<aid>/stop", methods=["POST"])
def stop_bot(aid):
    if aid in bot_status:
        bot_status[aid]["state"] = "stopped"
    return jsonify({"ok": True})

@app.route("/api/bot/<aid>/status")
def bot_status_api(aid):
    return jsonify(bot_status.get(aid, {"state": "idle", "log": []}))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
```

---

## Part 2 — Build `templates/index.html`

Single HTML file. No external CSS file. No JS framework. No build step.

### Design Spec

- **Theme:** Dark. Background `#0a0a0a`. Surface cards `#111111`. Accent `#1db954` (Spotify green).
- **Fonts:** `DM Mono` for code/labels/IDs, `Syne` for headings and buttons. Import from Google Fonts.
- **Layout:** Fixed header → stats row → accounts grid.
- **Auto-refresh:** Poll `/api/accounts` every 5 seconds via `setInterval`.

### Dashboard Sections

**Header:**
- Spotify green circle logo icon (SVG path of Spotify logo inside)
- Title: "Spotify Bot Dashboard"
- Subtitle: "playlist automation · multi-account" (DM Mono, muted)
- "+ Add Account" button (green, top right)

**Stats Row (4 cards):**
- Total Accounts (white number)
- Playing Now (green number)
- Idle / Done (amber number)
- Errors (red number)

These update dynamically from the account fetch.

**Accounts Grid:**
- `repeat(auto-fill, minmax(380px, 1fr))` grid
- One card per account

### Account Card Spec

Each card has two sections: top bar and body.

**Top bar:**
- Avatar circle with 2-letter initials of account ID
- Account name (bold) + playlist count (muted mono)
- Status badge (right side): idle / starting / playing / done / error / stopped
  - playing = green badge
  - error = red badge
  - others = muted gray badge

**Card body (only show relevant sections):**

1. **Now playing bar** (only when state = playing):
   - Small pulsing green dot (CSS animation)
   - Truncated playlist URI

2. **Progress bar** (only when playlists exist):
   - Label: "Playlist X of Y" + percentage
   - Thin progress bar (3px height, green fill)

3. **Playlist list:**
   - Each playlist as a row: number badge | truncated URI | × remove button
   - Active playlist row highlighted in green
   - Strip `spotify:playlist:` prefix for display, show only the ID part

4. **Add playlist input:**
   - Text input: placeholder `spotify:playlist:URI`
   - "Add" button (ghost style)

5. **Activity log** (show last 5 lines, only if log exists):
   - Small dark box, DM Mono font
   - Each line prefixed with `›`

6. **Action buttons:**
   - If running: "■ Stop" (red style)
   - If not running: "▶ Start" (green style)
   - Always: "Delete" (ghost style)

### Modal — Add Account

Triggered by "+ Add Account" button. Overlay with centered modal card.

Fields:
- Account ID (nickname, e.g. `client_1`)
- Spotify Client ID
- Spotify Client Secret (type=password)

Buttons: Cancel | Add Account

Close on clicking outside the modal.

### JavaScript Requirements

```javascript
let accountsData = [];  // Global state

// Fetch all accounts + statuses, re-render entire grid
async function fetchAccounts() { ... }

// Start/stop individual bots
async function startBot(id) { ... }
async function stopBot(id) { ... }

// Start all bots that are currently idle/done/stopped
async function startAll() { ... }

// Add/remove playlists per account
async function addPlaylist(id) { ... }
async function removePlaylist(id, idx) { ... }

// Account CRUD
async function addAccount() { ... }
async function deleteAccount(id) { ... }

// Toast notification (bottom right, auto-dismiss after 2.5s)
function toast(msg, type) { ... }

// Auto-refresh every 5 seconds
setInterval(fetchAccounts, 5000);
fetchAccounts();  // Initial load
```

---

## Part 3 — `requirements.txt`

```
flask
spotipy
```

---

## Part 4 — Setup & Deployment Instructions

Include a `README.md` with these exact steps:

### Step 1: Get Spotify API Credentials

1. Go to https://developer.spotify.com/dashboard
2. Log in → Create App
3. App name: anything (e.g. "Playlist Bot")
4. Redirect URI: `http://localhost:5000/callback` (or `http://YOUR_SERVER_IP:5000/callback` for VPS)
5. Copy the **Client ID** and **Client Secret**

> One Spotify Developer App can serve all accounts. Each account gets its own OAuth token but shares the same app credentials.

### Step 2: Install and Run

```bash
pip install flask spotipy
python app.py
```

Open `http://localhost:5000` in browser.

### Step 3: Add an Account

1. Click "+ Add Account"
2. Enter a nickname (e.g. `client_1`)
3. Paste the Spotify Client ID and Secret
4. Click Add Account

### Step 4: First-Time OAuth Login

The first time you click "▶ Start" for an account, a browser window opens asking you to log in with that Spotify account. After login, Spotify redirects to `http://localhost:5000/callback` and the token is saved to `tokens/.cache-{id}`. This only happens once per account.

### Step 5: Add Playlists

In each account card, paste Spotify playlist URIs in the format:
```
spotify:playlist:37i9dQZF1DXcBWIGoYBM5M
```

Get this by right-clicking any playlist in Spotify → Share → Copy Spotify URI.

### Step 6: Start the Bot

Click "▶ Start" on a card, or "▶ Start All" to launch all accounts at once.

---

## Part 5 — VPS Deployment (Oracle Cloud Free Tier)

Oracle Cloud offers a permanently free VM (2 vCPUs, 1GB RAM) — enough for 50+ accounts.

```bash
# SSH into server
ssh ubuntu@YOUR_SERVER_IP

# Install Python
sudo apt update && sudo apt install python3 python3-pip -y

# Upload project
scp -r spotify_bot/ ubuntu@YOUR_SERVER_IP:/home/ubuntu/

# Install deps
pip3 install flask spotipy

# Run permanently with screen (survives SSH disconnect)
screen -S spotifybot
python3 app.py
# Press Ctrl+A then D to detach

# Dashboard accessible at:
# http://YOUR_SERVER_IP:5000

# Reattach later:
# screen -r spotifybot
```

**Important:** Update the Spotify Developer App's Redirect URI to `http://YOUR_SERVER_IP:5000/callback` before deploying to VPS.

---

## Edge Cases — Handle All of These

| Scenario | Expected Behavior |
|---|---|
| No active device found | Set state = error, log message, stop thread |
| Spotify API returns null playback | Treat as same context, keep polling |
| User manually pauses | Bot waits — does not skip to next playlist |
| User manually changes playlist | Bot detects context URI change, syncs internal state |
| Bot is stopped mid-playlist | Thread exits cleanly on next poll cycle |
| All playlists finished | Set state = done, thread exits, log "All playlists finished" |
| Network error during poll | Log error, continue polling (do not crash) |
| Account deleted while bot running | Bot state is cleared, thread continues until next poll then exits |
| Two bots started for same account | Return 400 error if thread already alive |
| Empty playlist queue | Do not start bot, return error or log warning |

---

## Status State Machine

```
idle → starting → playing → done
                ↓
              error
                ↓
             stopped (manual)
```

- `idle`: account added, bot never started
- `starting`: thread launched, finding device
- `playing`: actively polling, playlist running
- `done`: all playlists in queue finished
- `error`: unrecoverable error (no device, auth failure)
- `stopped`: manually stopped via dashboard

---

## Security Notes

- `accounts.json` contains Spotify Client Secrets — **do not expose this file publicly**
- The dashboard has no authentication by default — on VPS, either add HTTP basic auth or restrict access by IP via firewall
- OAuth tokens in `tokens/` directory are sensitive — add both paths to `.gitignore`

```
# .gitignore
accounts.json
tokens/
__pycache__/
```

---

## What the Bot Does NOT Do

- It does not stream audio itself — it only controls Spotify playback via API
- It does not work without Spotify open on a device (phone, PC, web player)
- It does not support multiple simultaneous devices on one account (Spotify limitation)
- It does not scrape or bypass Spotify's API — 100% official Web API

---

## Completion Checklist

Before considering the build done, verify:

- [ ] `app.py` runs with `python app.py` with no errors
- [ ] Dashboard loads at `http://localhost:5000`
- [ ] Can add an account via the modal
- [ ] Can add/remove playlists per account
- [ ] Can start and stop individual bots
- [ ] "▶ Start All" button starts all idle accounts
- [ ] Status badges update live every 5 seconds
- [ ] Progress bar reflects current playlist index
- [ ] Log lines appear inside each card
- [ ] Stats row (total / playing / idle / errors) updates correctly
- [ ] Deleting an account removes it from the grid
- [ ] Multiple accounts run in parallel without blocking each other
- [ ] Bot auto-advances to next playlist when current one ends
- [ ] Bot syncs when user manually changes playlist

---

## Summary

Build a Flask + Python bot that:
1. Authenticates with multiple Spotify accounts via OAuth
2. Starts a playlist on each account's active device
3. Polls Spotify API every 5 seconds per account
4. Detects when a playlist ends and starts the next one
5. Runs all accounts in parallel via threads
6. Exposes a dark-themed web dashboard for management
7. Persists account config to `accounts.json`
8. Caches OAuth tokens to `tokens/` directory

**One command to run. One file of logic. One file of UI. Everything else is config.**