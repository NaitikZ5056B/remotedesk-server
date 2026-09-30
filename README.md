# RemoteDesk

> Secure, full-featured remote desktop control — access your laptop from your phone, anywhere in the world.

![Status](https://img.shields.io/badge/status-alpha-blueviolet) ![License](https://img.shields.io/badge/license-MIT-green)

---

## What Is RemoteDesk?

RemoteDesk is a self-hosted remote desktop system that lets you control your laptop from your phone over the internet. It works behind NAT (no port forwarding required) and is end-to-end encrypted.

### Key Features

| Feature | Description |
|---|---|
| **Live Screen Share** | WebRTC (low-latency) + WebSocket JPEG fallback. Adjustable FPS & quality. Multi-monitor. |
| **Full Remote Control** | Mouse, keyboard, scroll, right-click, touch gestures (tap=click, two-finger=scroll, long-press=right-click, pinch=zoom). |
| **System Controls** | Lock / Unlock / Sleep / Restart / Shutdown with confirmation prompts. |
| **System Dashboard** | CPU, RAM, disk, battery, network, processes (with kill), active window title. |
| **File Manager** | Browse, download, upload, rename, delete (with confirmation). |
| **Remote Terminal** | Multi-session shell access from your phone. |
| **Webcam Snapshot** | Capture a photo from the laptop's webcam. |
| **Clipboard Sync** | Copy/paste between phone and laptop. |
| **Volume & Brightness** | Remote media controls. |
| **Wake-on-LAN** | Wake your laptop from sleep/shutdown (if hardware supports it). |
| **Notifications** | Phone alerts on laptop wake, disconnect, or failed login. |
| **Panic Button** | Kill ALL sessions instantly. |

### Security

- **Device pairing** via QR code scanned from the laptop
- **End-to-end encrypted**: WebRTC DTLS-SRTP + TLS on signaling
- **Password + TOTP 2FA** (+ biometric on phone via PWA)
- **JWT sessions** with 24h expiry
- **Rate limiting** (5 failed attempts / 5 minutes)
- **IP & device allow-list**
- **Audit log** of every action
- **Confirmation prompts** for dangerous actions (shutdown, file delete)
- **No hardcoded secrets** — everything in `.env`

---

## Architecture

```
┌─────────────────┐         ┌──────────────────┐         ┌─────────────────┐
│   Phone (PWA)   │◄───────►│  Signaling Server │◄───────►│  Laptop Agent   │
│   Browser App   │  WSS    │  (Node.js + WS)   │  WSS    │  (Python)       │
│                 │         │  Render / Fly.io   │         │  Background svc │
│  Touch gestures │         │  + REST API        │         │  Screen capture │
│  Screen display │         │  + TURN relay      │         │  Input control  │
│  File manager   │         └──────────────────┘         │  System monitor │
│  Terminal       │                │                      │  File manager   │
│  Dashboard      │                │ TURN                 │  Terminal       │
│  Controls       │◄──── WebRTC P2P (direct) ────────────►│  + more         │
└─────────────────┘         (when NAT allows)             └─────────────────┘
```

- **Agent** makes OUTBOUND connections only → no port forwarding needed
- **STUN** servers (Google) for NAT traversal
- **TURN** server (coturn) as relay when direct P2P fails
- **WebSocket** fallback for screen streaming if WebRTC fails

---

## Quick Start

### Prerequisites

- **Laptop**: Python 3.10+, pip
- **Server**: Node.js 18+, npm (or deploy to Render/Fly.io)
- **Phone**: Any modern browser (Chrome, Safari, Firefox)

### 1. Set Up the Signaling Server

```bash
cd server
cp .env.example .env
# Edit .env: set JWT_SECRET to a random 64-char string
npm install
npm start
```

Server runs at `http://localhost:4000`. For production, deploy to [Render](https://render.com), [Fly.io](https://fly.io), or any VPS.

### 2. Set Up the Laptop Agent

```bash
cd agent
cp .env.example .env
# Edit .env:
#   SIGNAL_SERVER_URL=wss://your-deployed-server.onrender.com
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

pip install -r requirements.txt
python main.py
```

On first run, you'll be prompted to:
1. Set an access password
2. Set up 2FA (TOTP)
3. Generate a pairing QR code

### 3. Install as System Service (auto-start on boot)

```bash
python install_service.py install
```

### 4. Connect from Your Phone

1. Open the PWA URL in your phone browser: `https://your-server.onrender.com`
2. Tap **"Add to Home Screen"** to install as an app
3. Enter the server URL, Agent ID, password, and 2FA code
4. Or scan the pairing QR code displayed during agent setup

---

## Detailed Setup Guide

See [`docs/SETUP_GUIDE.md`](docs/SETUP_GUIDE.md) for:
- Deploying the server to Render (free tier)
- Setting up coturn TURN server
- Configuring Wake-on-LAN
- Security best practices

---

## Project Structure

```
remotedesk/
├── agent/                      # Python laptop agent
│   ├── main.py                 # Entry point & orchestrator
│   ├── config.py               # Configuration from .env
│   ├── connection.py           # WebSocket + WebRTC management
│   ├── screen_capture.py       # Screen capture & streaming
│   ├── input_controller.py     # Mouse, keyboard, gesture handling
│   ├── system_monitor.py       # CPU, RAM, disk, battery, processes
│   ├── system_controls.py      # Lock/unlock/sleep/restart/shutdown + WoL
│   ├── file_manager.py         # File browsing, upload, download, delete
│   ├── terminal_handler.py     # Remote shell sessions
│   ├── extras.py               # Webcam, clipboard, volume, brightness, notifications
│   ├── security.py             # Auth, JWT, TOTP, pairing, encryption
│   ├── audit_log.py            # Action audit logging
│   ├── install_service.py      # OS service installer
│   ├── requirements.txt        # Python dependencies
│   └── .env.example            # Environment template
│
├── server/                     # Node.js signaling server
│   ├── server.js               # WebSocket relay + REST API
│   ├── package.json            # Node dependencies
│   ├── coturn.conf             # TURN server config template
│   └── .env.example            # Environment template
│
├── pwa/                        # Progressive Web App (phone)
│   ├── index.html              # App shell (5-tab layout)
│   ├── css/styles.css          # Premium dark theme
│   ├── js/app.js               # All app logic
│   ├── manifest.json           # PWA manifest
│   └── sw.js                   # Service worker
│
├── docs/
│   ├── SETUP_GUIDE.md          # Detailed setup instructions
│   └── TEST_PLAN.md            # Testing plan
│
├── .gitignore
└── README.md                   # This file
```

---

## Unlock Screen — Limitations & Risks

| OS | Method | Limitations |
|---|---|---|
| **Windows** | `keybd_event` to wake display + credential helper approach | Full auto-unlock requires a custom Credential Provider DLL running as SYSTEM. The agent can wake the display and present the login screen, but typing the password into the secure desktop requires elevated privileges. |
| **macOS** | `caffeinate` + `osascript` keystroke injection | Works for screensaver unlock. Does NOT bypass FileVault or the secure login window on modern macOS. |
| **Linux** | `loginctl unlock-session` or `xdg-screensaver reset` | Works with systemd-logind. May not work with all display managers or Wayland. |

**⚠️ Security Warning**: Storing the user's login password (even encrypted at rest) increases the attack surface. Only use on personal machines with full-disk encryption enabled.

---

## Technology Choices

| Component | Technology | Why |
|---|---|---|
| Agent | Python 3.10+ | Best libraries for screen capture (`mss`), input control (`pynput`), system monitoring (`psutil`), and OS-level integration |
| Server | Node.js 18+ | Lightweight WebSocket server, easy to deploy for free, excellent async I/O |
| Phone App | PWA (HTML/CSS/JS) | Works on both Android and iPhone, installable, no app store needed |
| Streaming | WebRTC + WebSocket fallback | WebRTC for low latency when P2P works; WebSocket JPEG streaming as reliable fallback |
| NAT Traversal | STUN (Google) + TURN (coturn) | Free STUN; coturn for relay when symmetric NAT blocks P2P |
| Security | JWT + TOTP + bcrypt + Fernet | Industry-standard auth stack, no hardcoded secrets |

---

## License

MIT — Use freely for personal projects. Not intended for unauthorized access to other people's machines.
