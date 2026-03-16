# Spotify Playlist Bot

An automated tool to manage and play Spotify playlists sequentially across multiple accounts.

## Features
- Multi-account support
- Sequential playlist playback
- Real-time status dashboard
- Automated token management via Spotify OAuth

## Setup

### 1. Spotify Developer Setup
- Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).
- Create a new App.
- Add `http://localhost:5000/callback` to the **Redirect URIs**.
- Note down your **Client ID** and **Client Secret**.

### 2. Local Installation
1. Clone the repository:
   ```bash
   git clone https://github.com/your-username/spotify-playlist-bot.git
   cd spotify-playlist-bot
   ```
2. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   .\.venv\Scripts\activate  # Windows
   source .venv/bin/activate  # macOS/Linux
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Run the app:
   ```bash
   python app.py
   ```

## GitHub Preparation
- **Secrets**: Never commit `accounts.json` or the `tokens/` directory. They are included in `.gitignore`.
- **Environment Variables**: For production, use environment variables for sensitive configuration.

## License
MIT
