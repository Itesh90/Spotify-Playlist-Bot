# ─── Worker Node Dockerfile ───────────────────────────────────────────────────
# Each Spotify account runs in a dedicated container based on this image.
# Includes: Python 3.11, Playwright (Chromium + Chrome with Widevine DRM),
#           WireGuard tools, OpenVPN, noVNC for interactive login.
# Build: docker build -t spb_worker:latest ./worker
# ─────────────────────────────────────────────────────────────────────────────

FROM mcr.microsoft.com/playwright/python:v1.43.0-jammy

LABEL maintainer="Spotify Playlist Bot"
LABEL description="Isolated worker node: Playwright browser + VPN + Spotipy"

# ── System dependencies ───────────────────────────────────────────────────────
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    wireguard-tools \
    openresolv \
    openvpn \
    curl \
    dnsutils \
    xvfb \
    x11vnc \
    websockify \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# ── Install Google Chrome (includes Widevine DRM for Spotify playback) ────────
# Chromium alone does NOT include Widevine DRM. Spotify requires DRM to play
# audio. Google Chrome ships with Widevine built-in, so Spotify treats it as
# a real browser device ("Web Player (Chrome)") in Spotify Connect.
RUN curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
    | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
    > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# ── noVNC from GitHub (apt package has no HTML files on this base image) ──────
RUN curl -sL https://github.com/novnc/noVNC/archive/refs/tags/v1.4.0.tar.gz \
    | tar xz -C /opt \
    && mv /opt/noVNC-1.4.0 /opt/novnc \
    && echo "=== noVNC files ===" \
    && ls /opt/novnc/vnc.html /opt/novnc/vnc_lite.html

# ── Python dependencies ───────────────────────────────────────────────────────
WORKDIR /app
COPY worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Install Playwright browsers (Chromium + Firefox)
RUN python -m playwright install --with-deps chromium firefox

# ── Application code ──────────────────────────────────────────────────────────
COPY . .

# ── Data directories ──────────────────────────────────────────────────────────
RUN mkdir -p /app/data /app/vpn

# ── Entrypoint ────────────────────────────────────────────────────────────────
# INTERACTIVE=1 → starts Firefox in visible mode for manual login setup.
# Default       → headless playback mode using Chrome with Widevine DRM.
CMD ["python", "worker/main.py"]
