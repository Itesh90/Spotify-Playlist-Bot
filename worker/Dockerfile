# ─── Worker Node Dockerfile ───────────────────────────────────────────────────
# Each Spotify account runs in a dedicated container based on this image.
# Includes: Python 3.11, Playwright (Chromium), WireGuard tools, OpenVPN.
# Build: docker build -t spb_worker:latest ./worker
# ─────────────────────────────────────────────────────────────────────────────

FROM mcr.microsoft.com/playwright/python:v1.43.0-jammy

LABEL maintainer="Spotify Playlist Bot"
LABEL description="Isolated worker node: Playwright browser + VPN + Spotipy"

# ── System dependencies ───────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    wireguard-tools \
    openresolv \
    openvpn \
    curl \
    dnsutils \
    xvfb \
    x11vnc \
    novnc \
    websockify \
    && rm -rf /var/lib/apt/lists/*

# ── noVNC path fixup ─────────────────────────────────────────────────────────
# On Ubuntu Jammy, novnc installs to /usr/share/novnc/web/ but websockify
# expects to serve from a single directory. Create a symlink so vnc.html
# is accessible at the root regardless of which path we pass to websockify.
RUN if [ -f /usr/share/novnc/web/vnc.html ] && [ ! -f /usr/share/novnc/vnc.html ]; then \
        ln -s /usr/share/novnc/web/vnc.html /usr/share/novnc/vnc.html && \
        ln -s /usr/share/novnc/web/core /usr/share/novnc/core && \
        ln -s /usr/share/novnc/web/vendor /usr/share/novnc/vendor; \
    fi && \
    echo "=== noVNC install path ===" && \
    find /usr/share/novnc -name "vnc.html" 2>/dev/null | head -5

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
