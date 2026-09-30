# RemoteDesk — Test Plan

## Overview

Testing is organized by phase, matching the build order. Each phase should be validated before moving to the next.

---

## Phase 1: Screen Share + Mouse/Keyboard

### 1.1 Server Connection
| # | Test | Steps | Expected Result |
|---|------|-------|-----------------|
| 1 | Server starts | Run `npm start` in server/ | Server logs show listening on port 4000 |
| 2 | Health check | GET `http://localhost:4000/health` | JSON response with `status: "ok"` |
| 3 | Agent connects | Run agent `python main.py` | Agent logs "Connected to signaling server" |
| 4 | WebSocket relay | Send test message from agent | Server logs show the message was routed |

### 1.2 Screen Sharing
| # | Test | Steps | Expected Result |
|---|------|-------|-----------------|
| 5 | WS frame streaming | Start screen share from PWA | JPEG frames appear on canvas |
| 6 | FPS control | Set FPS to 10 vs 30 | Visible smoothness difference |
| 7 | Quality control | Set quality to 30 vs 80 | Visible compression difference |
| 8 | Multi-monitor | If >1 monitor, switch monitor | Screen switches to the other display |
| 9 | WebRTC stream | Initiate WebRTC offer/answer | Video element shows live screen |

### 1.3 Input Control
| # | Test | Steps | Expected Result |
|---|------|-------|-----------------|
| 10 | Mouse move | Drag finger on screen | Cursor moves on laptop |
| 11 | Single tap → click | Tap screen once | Left click at position |
| 12 | Double tap → double-click | Double tap | Double-click (opens folder, etc.) |
| 13 | Long press → right-click | Hold finger 500ms+ | Context menu appears on laptop |
| 14 | Two-finger scroll | Swipe with two fingers | Page scrolls on laptop |
| 15 | Keyboard input | Toggle keyboard, type text | Text appears in focused app on laptop |
| 16 | Special keys | Press Enter, Backspace, arrows | Keys register correctly |
| 17 | Key combos | Send Ctrl+C, Ctrl+V via app | Shortcut executes on laptop |

---

## Phase 2: System Controls + Dashboard

### 2.1 Dashboard
| # | Test | Steps | Expected Result |
|---|------|-------|-----------------|
| 18 | CPU display | Open Dashboard tab | CPU percentage and ring update |
| 19 | RAM display | Open Dashboard tab | RAM usage shows correct values |
| 20 | Disk display | Open Dashboard tab | Disk usage for primary drive |
| 21 | Battery display | Open Dashboard tab | Battery % (or hidden if desktop) |
| 22 | Network stats | Open Dashboard tab | Bytes sent/received shown |
| 23 | Active window | Open Dashboard tab | Shows current foreground window title |
| 24 | System info | Open Dashboard tab | OS, hostname, CPU, uptime shown |
| 25 | Process list | View processes | Top 30 processes by CPU shown |
| 26 | Kill process | Click kill on a test process | Process terminates, confirmation dialog works |
| 27 | Dashboard refresh | Click refresh button | Data updates to current values |

### 2.2 System Controls
| # | Test | Steps | Expected Result |
|---|------|-------|-----------------|
| 28 | Lock screen | Tap Lock button | Laptop screen locks |
| 29 | Unlock screen | Tap Unlock button | Display wakes (see limitations in README) |
| 30 | Sleep | Tap Sleep (after confirmation) | Laptop goes to sleep |
| 31 | Restart | Tap Restart (requires confirmation) | Confirmation dialog appears, then system restarts |
| 32 | Shutdown | Tap Shutdown (requires confirmation) | Confirmation dialog appears, then system shuts down |
| 33 | Cancel shutdown | Tap Cancel after scheduling shutdown | Pending shutdown is cancelled |
| 34 | Dangerous action confirmation | Tap Restart without confirming | Confirmation dialog blocks the action |

---

## Phase 3: Files / Terminal / Notifications

### 3.1 File Manager
| # | Test | Steps | Expected Result |
|---|------|-------|-----------------|
| 35 | List drives | Open Files tab | Available drives/mounts shown |
| 36 | Browse directory | Tap a drive, then folders | Directory contents listed correctly |
| 37 | Navigate back | Tap back arrow | Returns to parent directory |
| 38 | File download | Tap download on a small file | File downloads to phone |
| 39 | File upload | Tap upload, select file | File appears in target directory on laptop |
| 40 | Create folder | Tap new folder, enter name | Folder created on laptop |
| 41 | Delete file | Tap delete on a test file | Confirmation dialog, then file deleted |
| 42 | Protected path | Try deleting C:\Windows or /usr | Error: "Cannot delete protected system path" |

### 3.2 Terminal
| # | Test | Steps | Expected Result |
|---|------|-------|-----------------|
| 43 | Create session | Tap + button | New terminal session created, shell prompt appears |
| 44 | Run command | Type `echo hello` + Enter | Output "hello" appears |
| 45 | Interactive command | Run `python` or `node` | REPL starts, can type commands |
| 46 | Ctrl+C | Tap cancel button | Running command interrupted |
| 47 | Multiple sessions | Create 2+ sessions | Can switch between them |
| 48 | Close session | Tap close button | Session terminated |

### 3.3 Extras
| # | Test | Steps | Expected Result |
|---|------|-------|-----------------|
| 49 | Clipboard get | Tap "Get" clipboard | Shows laptop clipboard content |
| 50 | Clipboard set | Enter text, tap "Set" | Laptop clipboard updated |
| 51 | Volume get/set | Move volume slider | Laptop volume changes |
| 52 | Volume mute | Tap mute button | Laptop audio mutes/unmutes |
| 53 | Brightness | Move brightness slider | Laptop screen brightness changes |
| 54 | Webcam snapshot | Tap "Capture" | Photo from laptop webcam displayed |
| 55 | Wake-on-LAN | Enter MAC, tap Wake | Magic packet sent (verify with Wireshark) |

### 3.4 Notifications
| # | Test | Steps | Expected Result |
|---|------|-------|-----------------|
| 56 | Agent disconnect | Kill agent process | Phone shows "Laptop disconnected" toast |
| 57 | Failed login | Enter wrong password 3 times | Notification about failed attempts |

---

## Phase 4: Security Hardening

### 4.1 Authentication
| # | Test | Steps | Expected Result |
|---|------|-------|-----------------|
| 58 | Valid login | Enter correct password + TOTP | Login succeeds, app opens |
| 59 | Wrong password | Enter incorrect password | "Invalid credentials" error |
| 60 | Wrong TOTP | Enter expired/wrong TOTP code | "Invalid 2FA code" error |
| 61 | Rate limiting | Fail login 6 times in 5 min | "Rate limited" error on 6th attempt |
| 62 | JWT expiry | Wait 24h (or set short expiry) | Token expires, redirected to login |
| 63 | Session list | Check active sessions in settings | Shows current session details |

### 4.2 Pairing
| # | Test | Steps | Expected Result |
|---|------|-------|-----------------|
| 64 | QR generation | Agent generates pairing code | QR code PNG saved to data/ |
| 65 | Pairing flow | Enter pairing code on phone | Device paired, credentials exchanged |
| 66 | Expired pairing | Wait >5min, try pairing | "Expired" error |

### 4.3 Panic Button
| # | Test | Steps | Expected Result |
|---|------|-------|-----------------|
| 67 | Panic button | Tap panic button (confirm) | All sessions killed, streaming stops |
| 68 | Post-panic state | Check agent after panic | No active connections, terminals closed |

### 4.4 Audit Log
| # | Test | Steps | Expected Result |
|---|------|-------|-----------------|
| 69 | Actions logged | Perform various actions | All appear in audit log |
| 70 | Login attempts | Failed + successful logins | Both recorded with IPs and timestamps |
| 71 | Dangerous actions | Delete file, restart | Logged with risk_level="high"/"critical" |

### 4.5 Encryption
| # | Test | Steps | Expected Result |
|---|------|-------|-----------------|
| 72 | WebSocket TLS | Connect via wss:// | Connection uses TLS (check DevTools) |
| 73 | No plaintext secrets | Check .env not in repo | .gitignore excludes .env |
| 74 | Stored credentials | Check credentials.json | Password is bcrypt-hashed, not plaintext |

---

## Performance Benchmarks

| Metric | Target | How to Measure |
|---|---|---|
| Screen latency (WebSocket) | < 200ms | Stopwatch between laptop action and phone display |
| Screen latency (WebRTC) | < 100ms | Same as above |
| Dashboard refresh | < 500ms | Time from button click to data update |
| File download (1MB) | < 3s on fast connection | Time from click to download complete |
| Terminal round-trip | < 100ms | Time from Enter key to output |

---

## Browser Compatibility

| Browser | Platform | Status |
|---|---|---|
| Chrome 90+ | Android | ✅ Full support |
| Safari 15+ | iOS | ✅ Full support (limited WebRTC) |
| Firefox 90+ | Android | ✅ Full support |
| Chrome 90+ | Desktop | ✅ For testing |
| Edge 90+ | Desktop | ✅ For testing |
