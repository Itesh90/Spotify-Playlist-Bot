# ─── Worker Node Dockerfile ───────────────────────────────────────────────────
# Each Spotify account runs in a dedicated container based on this image.
# Includes: Python 3.11, Playwright (Chromium), WireGuard tools, OpenVPN.
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
    && rm -rf /var/lib/apt/lists/*

# ── noVNC from GitHub (apt package has no HTML files on this base image) ──────
RUN curl -sL https://github.com/novnc/noVNC/archive/refs/tags/v1.4.0.tar.gz \
    | tar xz -C /opt \
    && mv /opt/noVNC-1.4.0 /opt/novnc \
    && echo "=== noVNC files ===" \
    && ls /opt/novnc/vnc.html /opt/novnc/vnc_lite.html

# ── Python dependencies ───────────────────────────────────────────────────────
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium firefox

# ── Application code ──────────────────────────────────────────────────────────
COPY . .

# ── Data directories ──────────────────────────────────────────────────────────
RUN mkdir -p /app/data /app/vpn

# ── Entrypoint ────────────────────────────────────────────────────────────────
# INTERACTIVE=1 → starts Firefox in visible mode for manual login setup.
# Default       → headless playback mode.
CMD ["python", "main.py"]
