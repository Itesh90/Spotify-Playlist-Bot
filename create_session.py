#!/usr/bin/env python3
"""
create_session.py
─────────────────
Local session creator for the Spotify Playlist Bot.
Run this on your Windows machine (NOT in Docker / Codespaces).

Opens a visible Firefox window → you log in to Spotify →
session.json is saved automatically when login is detected.

Usage:
    pip install playwright
    playwright install firefox
    python create_session.py
"""

import os
import sys
import json
import glob
import time

# ─── Find Accounts ───────────────────────────────────────────────────────────

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
STORAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "storage", "accounts")


def load_accounts() -> list[dict]:
    """Load all account JSON files from the data directory."""
    accounts = []
    for path in sorted(glob.glob(os.path.join(DATA_DIR, "account_*.json"))):
        with open(path, "r") as f:
            accounts.append(json.load(f))
    return accounts


def pick_account(accounts: list[dict]) -> dict:
    """Interactive picker — shows numbered list, user picks one."""
    print("\n╔══════════════════════════════════════════╗")
    print("║   Spotify Playlist Bot — Session Setup   ║")
    print("╚══════════════════════════════════════════╝\n")

    if not accounts:
        print("❌ No accounts found in data/ directory.")
        print("   Add an account from the dashboard first, then run this script.")
        sys.exit(1)

    print("Available accounts:\n")
    for i, acc in enumerate(accounts):
        status = "✅ Authorized" if acc.get("authorized") else "❌ Not Authorized"
        playlists = len(acc.get("playlists", []))
        session_exists = os.path.exists(os.path.join(STORAGE_DIR, acc["id"], "session.json"))
        session_status = "📂 session.json exists" if session_exists else "⚠️  No session.json"
        print(f"  [{i + 1}] {acc['name']}")
        print(f"      ID: {acc['id']}  |  {status}  |  {playlists} playlists")
        print(f"      {session_status}")
        print()

    while True:
        try:
            choice = input(f"Pick an account (1-{len(accounts)}): ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(accounts):
                return accounts[idx]
            print(f"  → Please enter a number between 1 and {len(accounts)}")
        except (ValueError, KeyboardInterrupt):
            print("\nCancelled.")
            sys.exit(0)


# ─── Browser Session ─────────────────────────────────────────────────────────

def create_session(account: dict):
    """Open Firefox, navigate to Spotify, wait for login, save session."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("\n❌ Playwright is not installed.")
        print("   Run these commands first:")
        print("     pip install playwright")
        print("     playwright install firefox")
        sys.exit(1)

    account_id = account["id"]
    account_name = account["name"]
    session_dir = os.path.join(STORAGE_DIR, account_id)
    session_file = os.path.join(session_dir, "session.json")
    os.makedirs(session_dir, exist_ok=True)

    print(f"\n🚀 Setting up session for: {account_name} ({account_id})")
    print("   Opening Firefox → https://open.spotify.com")
    print("   Log in to your Spotify account in the browser window.")
    print("   This script will auto-detect when you're logged in.\n")

    with sync_playwright() as p:
        browser = p.firefox.launch(headless=False)
        context = browser.new_context(
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )

        # Anti-detection
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = { runtime: {} };
        """)

        page = context.new_page()
        page.goto("https://open.spotify.com", timeout=30_000)

        print("   ⏳ Waiting for login...")
        print("   (Browser is open — log in to Spotify now)\n")

        # Poll for login completion
        spotify_user = None
        while True:
            time.sleep(2)
            try:
                current_url = page.url

                # Still on login page — keep waiting
                if "accounts.spotify.com" in current_url or "login" in current_url:
                    continue

                # Spotify Web Player loaded — login is complete
                if "open.spotify.com" in current_url and "login" not in current_url:
                    # Give the page a moment to fully load user data
                    time.sleep(3)

                    # Try to capture the Spotify username from the DOM
                    try:
                        spotify_user = page.evaluate("""
                            () => {
                                const el = document.querySelector('[data-testid="user-widget-link"]');
                                return el ? el.textContent.trim() : null;
                            }
                        """)
                    except Exception:
                        spotify_user = None

                    break

            except Exception:
                # Page might be navigating — just wait
                continue

        # Save session state
        context.storage_state(path=session_file)
        print(f"   ✅ Login detected!")
        if spotify_user:
            print(f"   ♫ Spotify user: {spotify_user}")
            # Save username for the dashboard
            user_file = os.path.join(session_dir, "spotify_user.txt")
            with open(user_file, "w") as f:
                f.write(spotify_user)

        print(f"   📂 Session saved → {session_file}")

        browser.close()

    print(f"\n🎉 Done! Account '{account_name}' is ready for Docker Node.\n")
    print("Next steps:")
    print("  1. Push storage/accounts/ to your Codespace (or copy the file)")
    print("  2. In the dashboard, click 'Docker Node' to start the headless browser")
    print("  3. Open Mainframe to see the live feed\n")


# ─── Entry Point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    accounts = load_accounts()
    selected = pick_account(accounts)
    create_session(selected)
