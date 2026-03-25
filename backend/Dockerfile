# ─── Backend Orchestrator Dockerfile ─────────────────────────────────────────
# Slimmed Flask backend. Does NOT need Playwright or a VPN client.
# Lives on the host network, communicates with workers via Docker socket.
#
# Build context: project root (so app.py is accessible)
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

LABEL description="Spotify Playlist Bot — Flask Orchestrator"

WORKDIR /app

# System deps (curl for health checks)
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies from backend/requirements.txt
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy only the files the Flask app needs
COPY app.py .
COPY backend/ ./backend/

# Create data and storage directories
RUN mkdir -p /app/data /app/storage/accounts

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "120", "app:app"]
