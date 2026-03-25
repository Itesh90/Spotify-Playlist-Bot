# 🎵 Spotify Playlist Bot — V2 Fleet Architecture

A massively scalable, multi-account Spotify playlist automation framework. Deploy a centralized Next.js Fleet Command Center to orchestrate dozens of isolated Docker "Worker Pods", each running an independent Playwright browser, separate Spotipy instances, and isolated VPN tunnels.

![Next.js](https://img.shields.io/badge/Next.js-UI-black)
![React](https://img.shields.io/badge/React-Component-blue)
![Python](https://img.shields.io/badge/Python-Backend-3776AB)
![Docker](https://img.shields.io/badge/Docker-Orchestration-2496ED)
![Playwright](https://img.shields.io/badge/Playwright-Automated_Browser-2EAD33)

---

## 🚀 Features

- **Docker-Orchestrated Scaling**: The Python backend acts as an orchestrator, dynamically spinning up completely isolated container environments per Spotify account via the Docker Engine API.
- **True Isolation**: Each worker node maintains its own browser fingerprint, cookie cache, and optionally an isolated IP address (via WireGuard/OpenVPN built into the worker).
- **Fleet Command Center**: A stunning, high-performance Next.js dashboard featuring real-time container log streaming, Node status glowing indicators, and global kill switches.
- **Mainframe CCTV Grid**: Live visual monitoring of all headless Playwright browsers happening simultaneously in a multi-camera grid.
- **Interactive VNC Auth Flow**: Bypass complex captchas and multi-factor authentication by dropping into a live, GUI-based Interactive Setup session once per account. Cookies are cached globally and injected into headless runners.

---

## 🏗️ Architecture

```text
/spotify-playlist-bot
├── docker-compose.yml       # Orchestrates the Flask Backend + Next.js Frontend
├── backend/                 # Flask Orchestrator API (port 5000)
├── frontend/                # Next.js Command Center (port 3000)
├── worker/                  # Base image for dynamic Account Pods
└── storage/                 # Persistent volumes (Mounted to workers dynamically)
    └── accounts/            # Stores session.json for each account
```

---

## ⚙️ Quick Start (Deployment)

### 1. Requirements

- **Docker** and **Docker Compose** installed.
- (Windows users) Docker Desktop must be running with WSL2 integration enabled.
- A `.env` file at the root (copy from `.env.example`).

### 2. Build the Worker Node Image

Before starting the orchestrator, you must build the base `spb_worker` image which the backend will use to spawn temporary pods.

```bash
docker build -t spb_worker:latest ./worker
```

### 3. Launch the Fleet Orchestrator

Bring up the Flask Backend and the Next.js Frontend.

```bash
docker-compose up -d --build
```

### 4. Access the Command Center

- Open **http://localhost:3000** in your browser.
- Log in using the default passcode: `admin123`.

---

## 🎮 How to Use the Bot

1. **Register an Account**: In the UI, enter an Account Name and Spotify API credentials (Client ID/Secret).
2. **Interactive Setup (MFA Bypass)**: 
   - Click "Setup Node" on an account. The orchestrator will spawn a worker container with a visible browser.
   - Using a VNC client, connect to the container, log in manually to Spotify, and press complete in the terminal.
   - Your session cookies are permanently saved to `/storage/accounts/<id>/session.json`.
3. **Deploy Headless Fleet**: 
   - Click "Run Headless". The Orchestrator rips down the setup container and spawns a lightweight headless Playwright container using the saved session cookies.
   - It will automatically establish itself as the active Spotify device and execute your playlist queue sequentially.
4. **Monitor Mainframe**:
   - Access the Mainframe tab to watch physical screencasts of your headless fleet at work in real-time.

---

## 🛡️ License

MIT License
