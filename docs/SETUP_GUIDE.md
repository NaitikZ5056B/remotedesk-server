# RemoteDesk — Step-by-Step Setup Guide

## Table of Contents
1. [Laptop Agent Setup](#1-laptop-agent-setup)
2. [Signaling Server Deployment](#2-signaling-server-deployment)
3. [TURN Server Setup (Optional but Recommended)](#3-turn-server-setup)
4. [Phone App Setup](#4-phone-app-setup)
5. [Pairing Your Phone](#5-pairing-your-phone)
6. [Security Configuration](#6-security-configuration)
7. [Wake-on-LAN Setup](#7-wake-on-lan-setup)
8. [Troubleshooting](#8-troubleshooting)

---

## 1. Laptop Agent Setup

### Windows

```powershell
# 1. Install Python 3.10+ from https://python.org
# 2. Open PowerShell and navigate to the agent directory
cd remotedesk\agent

# 3. Create and activate a virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1

# 4. Install dependencies
pip install -r requirements.txt

# 5. Copy and configure .env
copy .env.example .env
# Edit .env with your text editor:
#   SIGNAL_SERVER_URL=wss://your-server-url.onrender.com

# 6. Run the agent (first-time setup will begin)
python main.py

# 7. Follow the prompts:
#    - Set a password
#    - Set up 2FA (scan QR code with authenticator app)
#    - Note the Device ID and pairing QR code

# 8. (Optional) Install as auto-start service
python install_service.py install
```

### macOS

```bash
# 1. Install Python 3.10+ (via Homebrew)
brew install python@3.12

# 2. Navigate and setup
cd remotedesk/agent
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
nano .env  # Set SIGNAL_SERVER_URL

# 4. Run
python main.py

# 5. Auto-start on login
python install_service.py install
```

### Ubuntu/Linux

```bash
# 1. Install Python and dependencies
sudo apt update
sudo apt install python3 python3-venv python3-pip xdotool xclip

# 2. Navigate and setup
cd remotedesk/agent
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
nano .env  # Set SIGNAL_SERVER_URL

# 4. Run
python main.py

# 5. Auto-start as systemd service
python install_service.py install
```

---

## 2. Signaling Server Deployment

### Option A: Deploy to Render (Free)

1. **Create a Render account** at https://render.com
2. **Create a New Web Service**
   - Source: Connect your Git repo, or use Docker
   - Runtime: Node
   - Build Command: `cd server && npm install`
   - Start Command: `cd server && npm start`
3. **Set Environment Variables** in the Render dashboard:
   - `PORT` = `4000` (Render sets this automatically)
   - `JWT_SECRET` = (generate a random 64-char string: `node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"`)
   - `CORS_ORIGINS` = `*`
4. **Deploy** — Note the URL (e.g., `https://remotedesk-xxxx.onrender.com`)
5. Update your agent's `.env`:
   ```
   SIGNAL_SERVER_URL=wss://remotedesk-xxxx.onrender.com
   ```

### Option B: Deploy to Fly.io (Free Tier)

```bash
cd server
fly launch   # Follow prompts
fly secrets set JWT_SECRET=$(openssl rand -hex 32)
fly deploy
```

### Option C: Run on a VPS

```bash
# SSH into your VPS
cd remotedesk/server
cp .env.example .env
nano .env  # Configure JWT_SECRET, etc.
npm install
npm start

# For production, use PM2:
npm install -g pm2
pm2 start server.js --name remotedesk-server
pm2 startup
pm2 save
```

### Option D: Run Locally (Testing)

```bash
cd server
cp .env.example .env
npm install
npm start
# Server runs at ws://localhost:4000
```

---

## 3. TURN Server Setup

A TURN server is needed when direct P2P connections fail (symmetric NAT, strict firewalls). Without it, WebRTC screen sharing may not work in some network configurations.

### Using coturn on a VPS

```bash
# 1. Install coturn
sudo apt install coturn

# 2. Enable it
sudo nano /etc/default/coturn
# Uncomment: TURNSERVER_ENABLED=1

# 3. Configure
sudo cp remotedesk/server/coturn.conf /etc/turnserver.conf
sudo nano /etc/turnserver.conf
# Change: static-auth-secret=YOUR_RANDOM_SECRET
# Change: realm=your-domain.com

# 4. (Optional) Add TLS with Let's Encrypt
sudo certbot certonly --standalone -d turn.your-domain.com
# Uncomment cert/pkey lines in turnserver.conf

# 5. Start
sudo systemctl restart coturn
sudo systemctl enable coturn

# 6. Test: Visit https://webrtc.github.io/samples/src/content/peerconnection/trickle-ice/
#    Add your TURN server and check if it resolves
```

### Update Agent & Server Config

In agent `.env`:
```
TURN_SERVER=turn:your-vps-ip:3478
TURN_USERNAME=remotedesk
TURN_PASSWORD=your-turn-password
```

In server `.env`:
```
TURN_SERVER=turn:your-vps-ip:3478
TURN_SECRET=YOUR_RANDOM_SECRET
```

### Free TURN Alternatives

- **Metered.ca**: Free tier with 500MB/month
- **Twilio**: Free trial with TURN support
- **Open Relay**: Community TURN servers (less reliable)

---

## 4. Phone App Setup

The phone app is a Progressive Web App (PWA) that works in any modern browser.

### Serving the PWA

The signaling server automatically serves the PWA from the `../pwa` directory. Once your server is deployed, access the PWA at:

```
https://your-server-url.onrender.com/
```

### Installing as an App

**Android (Chrome):**
1. Open the PWA URL in Chrome
2. Tap the ⋮ menu → "Add to Home screen"
3. Tap "Install"

**iPhone (Safari):**
1. Open the PWA URL in Safari
2. Tap the Share button → "Add to Home Screen"
3. Tap "Add"

The PWA will now appear as a standalone app with no browser chrome.

---

## 5. Pairing Your Phone

### Method 1: Manual Entry

1. Open the PWA on your phone
2. Enter:
   - **Server URL**: `wss://your-server.onrender.com`
   - **Agent ID**: (shown during agent first-time setup, e.g., `agent-a1b2c3d4`)
   - **Password**: (the one you set during setup)
   - **2FA Code**: (from your authenticator app)
3. Tap **Connect**

### Method 2: QR Code (Recommended)

1. During first-time agent setup, a QR code is generated
2. The QR code image is saved to `agent/data/pairing_qr.png`
3. Scan this QR code from the PWA login screen (or enter the data manually)

---

## 6. Security Configuration

### Password Requirements
- Use a strong, unique password (12+ characters)
- The password is bcrypt-hashed and never stored in plaintext

### 2FA (TOTP)
- Scan the QR code with Google Authenticator, Authy, or any TOTP app
- The TOTP secret is stored encrypted in the agent's `.env`

### Device & IP Allow-Lists
Edit `agent/data/credentials.json`:
```json
{
  "allowed_devices": ["phone-abc123"],
  "allowed_ips": ["1.2.3.4"]
}
```
Leave arrays empty to allow any device/IP.

### JWT Token Expiry
Tokens expire after 24 hours by default. The phone will auto-reconnect with saved credentials.

### Audit Log
Every action is logged to `agent/logs/audit.json` (JSON Lines format):
```bash
tail -f agent/logs/audit.json | python -m json.tool
```

---

## 7. Wake-on-LAN Setup

### Enable WoL in BIOS/UEFI
1. Enter BIOS (usually F2, F12, or DEL on boot)
2. Find **Power Management** or **Wake-on-LAN**
3. Enable **Wake on PCI-E** or **Wake on LAN**

### Enable WoL in OS

**Windows:**
```powershell
# Open Device Manager → Network adapters → your adapter → Properties
# Power Management tab → Check "Allow this device to wake the computer"
# Advanced tab → Set "Wake on Magic Packet" to Enabled
```

**Linux:**
```bash
sudo ethtool -s eth0 wol g
# Make persistent: add to /etc/network/interfaces or NetworkManager
```

### Get Your MAC Address
```bash
# Windows
ipconfig /all  # Look for "Physical Address"

# Linux/macOS
ip link show  # or ifconfig
```

### Send WoL from Phone
1. Go to **Controls** tab in the PWA
2. Enter your laptop's MAC address (e.g., `AA:BB:CC:DD:EE:FF`)
3. Tap **Wake**

> **Note**: WoL only works on the same local network unless you configure WoL over the internet (requires router port forwarding for UDP port 9).

---

## 8. Troubleshooting

| Issue | Solution |
|---|---|
| **Agent can't connect to server** | Check `SIGNAL_SERVER_URL` in `.env`. Ensure the URL uses `wss://` for HTTPS servers. |
| **Screen share is black** | On macOS, grant Screen Recording permission in System Preferences → Privacy. On Linux, ensure `DISPLAY=:0` is set. |
| **Input control doesn't work** | On macOS, grant Accessibility permission. On Linux, the user must have access to the X server. |
| **WebRTC doesn't connect** | This usually means both sides are behind symmetric NAT. Set up a TURN server. The WebSocket fallback should still work. |
| **Phone can't install PWA** | Ensure the server uses HTTPS. PWAs require a secure context. |
| **High latency** | Reduce FPS and quality in Settings. Use WebRTC instead of WebSocket streaming. |
| **Agent crashes on Windows** | Install `pywin32`: `pip install pywin32`. Run as Administrator for system control features. |
| **Wake-on-LAN doesn't work** | WoL must be enabled in both BIOS and OS network adapter settings. Only works on the same LAN. |
