# 🎵 Spotify Playlist Bot

A multi-account Spotify playlist automation bot with a web dashboard. Add your Spotify accounts, queue up playlists, and the bot plays them sequentially — hands-free.

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-Backend-black?logo=flask)
![Spotify](https://img.shields.io/badge/Spotify-API-1DB954?logo=spotify&logoColor=white)

---

## Features

- **Multi-Account Support** — Add multiple Spotify accounts, each with their own API credentials
- **Sequential Playlist Playback** — Plays through playlists one by one, in order
- **Auto-Follow Playlists** — Automatically saves playlists to your library
- **Smart End Detection** — Detects when a playlist ends via 4 strategies:
  - Context change (autoplay kicks in)
  - Unknown track detection (autoplay injects a song)
  - Loop detection (playlist restarts from track 1)
  - Pause detection (playback pauses after last track)
- **OAuth Per Account** — Each account authorizes independently, tokens cached to disk
- **Live Dashboard** — Dark-theme web UI with status badges, progress bars, activity logs
- **Shuffle/Repeat Disabled** — Ensures clean sequential playback

---

## Quick Start

### 1. Clone & Install

```bash
git clone https://github.com/YOUR_USERNAME/Spotify-Playlist-Bot.git
cd Spotify-Playlist-Bot
pip install -r requirements.txt
```

### 2. Spotify App Setup

For **each** Spotify account you want to use:

1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Create a new app
3. Add `http://127.0.0.1:5000/callback` as a **Redirect URI**
4. Copy the **Client ID** and **Client Secret**

### 3. Run

```bash
python app.py
```

Open **http://127.0.0.1:5000** in your browser.

### 4. Use

1. **Add Account** — Enter a name, Client ID, and Client Secret
2. **Authorize** — Click the Authorize button, log in to Spotify
3. **Add Playlists** — Paste Spotify playlist URLs or URIs
4. **Start** — Click Start and the bot takes over

---

## Deploy to Railway

1. Push this repo to GitHub
2. Connect it to [Railway](https://railway.app)
3. Set environment variables:

| Variable | Value |
|----------|-------|
| `BASE_URL` | `https://your-app.up.railway.app` |
| `SECRET_KEY` | Any random string |

4. Update each Spotify app's Redirect URI to `https://your-app.up.railway.app/callback`

---

## Project Structure

```
├── app.py              # Flask backend (API + bot engine)
├── templates/
│   └── index.html      # Dashboard UI
├── requirements.txt    # Python dependencies
├── Procfile            # Railway/Heroku deployment
└── data/               # Created at runtime (gitignored)
    ├── account_*.json  # Per-account config
    └── tokens/         # Cached OAuth tokens
```

---

## API Endpoints

| Method | Route | Description |
|--------|-------|-------------|
| `GET` | `/` | Dashboard |
| `GET` | `/api/accounts` | List all accounts |
| `POST` | `/api/accounts` | Add account |
| `DELETE` | `/api/accounts/<id>` | Delete account |
| `POST` | `/api/accounts/<id>/playlists` | Add playlist |
| `DELETE` | `/api/accounts/<id>/playlists/<idx>` | Remove playlist |
| `POST` | `/api/accounts/<id>/start` | Start bot |
| `POST` | `/api/accounts/<id>/stop` | Stop bot |
| `POST` | `/api/start-all` | Start all bots |
| `POST` | `/api/stop-all` | Stop all bots |
| `GET` | `/auth/<id>` | OAuth login |
| `GET` | `/callback` | OAuth callback |

---

## Requirements

- Python 3.10+
- Spotify Premium account(s)
- Active Spotify device (phone, desktop, or web player)

---

## License

MIT
