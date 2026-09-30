/**
 * RemoteDesk PWA — Main Application
 *
 * Single-file app logic: connection, auth, screen streaming (WebSocket + WebRTC),
 * touch gesture engine, dashboard, controls, file manager, terminal, and more.
 */

// ══════════════════════════════════════════════════════════════
//  GLOBAL STATE
// ══════════════════════════════════════════════════════════════
const state = {
  ws: null,
  pc: null,             // RTCPeerConnection
  dataChannel: null,
  serverUrl: '',
  agentId: '',
  token: '',
  deviceId: '',
  connected: false,
  streaming: false,
  currentTab: 'screen-view',
  currentPath: '',
  terminalSessionId: '',
  screenWidth: 1920,
  screenHeight: 1080,
  confirmCallback: null,
};

// ══════════════════════════════════════════════════════════════
//  INITIALIZATION
// ══════════════════════════════════════════════════════════════
document.addEventListener('DOMContentLoaded', () => {
  // Generate device ID
  state.deviceId = localStorage.getItem('rd_device_id');
  if (!state.deviceId) {
    state.deviceId = 'phone-' + crypto.randomUUID().slice(0, 16);
    localStorage.setItem('rd_device_id', state.deviceId);
  }

  // Load saved settings
  const saved = JSON.parse(localStorage.getItem('rd_settings') || '{}');
  if (saved.serverUrl) document.getElementById('server-url').value = saved.serverUrl;
  if (saved.agentId) document.getElementById('agent-id').value = saved.agentId;

  // Init all modules
  initSplash();
  initAuth();
  initNavigation();
  initScreen();
  initDashboard();
  initControls();
  initFiles();
  initTerminal();
  initSettings();
  initPanicButton();

  // Register service worker
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('sw.js').catch(() => {});
  }
});

// ══════════════════════════════════════════════════════════════
//  SPLASH
// ══════════════════════════════════════════════════════════════
function initSplash() {
  setTimeout(() => {
    const splash = document.getElementById('splash');
    splash.style.transition = 'opacity 0.5s';
    splash.style.opacity = '0';
    setTimeout(() => {
      splash.classList.add('hidden');
      // Check if we have a saved session
      const savedToken = localStorage.getItem('rd_token');
      const savedUrl = localStorage.getItem('rd_server_url');
      const savedAgent = localStorage.getItem('rd_agent_id');
      if (savedToken && savedUrl && savedAgent) {
        state.serverUrl = savedUrl;
        state.agentId = savedAgent;
        state.token = savedToken;
        connectWebSocket();
      } else {
        document.getElementById('login-screen').classList.remove('hidden');
      }
    }, 500);
  }, 1500);
}

// ══════════════════════════════════════════════════════════════
//  AUTHENTICATION
// ══════════════════════════════════════════════════════════════
function initAuth() {
  document.getElementById('login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = document.getElementById('login-btn');
    btn.disabled = true;
    btn.innerHTML = '<span class="material-icons-round">hourglass_top</span> Connecting...';

    state.serverUrl = document.getElementById('server-url').value.trim();
    state.agentId = document.getElementById('agent-id').value.trim();
    const password = document.getElementById('password').value;
    const totpCode = document.getElementById('totp-code').value;

    // Convert http(s) to ws(s) if needed
    state.serverUrl = state.serverUrl
      .replace(/^http:/, 'ws:')
      .replace(/^https:/, 'wss:');

    try {
      await connectWebSocket();

      // Send auth
      sendMessage({
        type: 'auth',
        agent_id: state.agentId,
        device_id: state.deviceId,
        password,
        totp_code: totpCode,
      });

      // Wait for auth response (handled in message router)
      setTimeout(() => {
        btn.disabled = false;
        btn.innerHTML = '<span class="material-icons-round">login</span> Connect';
      }, 3000);

    } catch (err) {
      showLoginError(err.message || 'Connection failed');
      btn.disabled = false;
      btn.innerHTML = '<span class="material-icons-round">login</span> Connect';
    }
  });

  // QR scanner button
  document.getElementById('scan-qr-btn').addEventListener('click', () => {
    toast('QR scanning requires the native app. Enter details manually.', 'info');
  });
}

function showLoginError(msg) {
  const el = document.getElementById('login-error');
  el.textContent = msg;
  el.classList.remove('hidden');
  setTimeout(() => el.classList.add('hidden'), 5000);
}

function onAuthSuccess(data) {
  state.token = data.token;
  localStorage.setItem('rd_token', data.token);
  localStorage.setItem('rd_server_url', state.serverUrl);
  localStorage.setItem('rd_agent_id', state.agentId);
  localStorage.setItem('rd_settings', JSON.stringify({
    serverUrl: state.serverUrl,
    agentId: state.agentId,
  }));

  document.getElementById('login-screen').classList.add('hidden');
  document.getElementById('app').classList.remove('hidden');
  toast('Connected to laptop!', 'success');

  // Connect to agent
  sendMessage({ type: 'connect_to_agent', agent_id: state.agentId });

  // Request initial data
  requestDashboard();
}

// ══════════════════════════════════════════════════════════════
//  WEBSOCKET CONNECTION
// ══════════════════════════════════════════════════════════════
function connectWebSocket() {
  return new Promise((resolve, reject) => {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      resolve(); return;
    }

    try {
      state.ws = new WebSocket(state.serverUrl);
      state.ws.binaryType = 'arraybuffer';
    } catch (e) {
      reject(new Error('Invalid server URL'));
      return;
    }

    const timeout = setTimeout(() => {
      reject(new Error('Connection timeout'));
      state.ws.close();
    }, 10000);

    state.ws.onopen = () => {
      clearTimeout(timeout);
      state.connected = true;
      updateConnectionBadge(true);
      resolve();
    };

    state.ws.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) {
        handleBinaryMessage(event.data);
      } else {
        try {
          const data = JSON.parse(event.data);
          handleMessage(data);
        } catch (e) {
          console.error('Invalid message:', e);
        }
      }
    };

    state.ws.onclose = () => {
      state.connected = false;
      updateConnectionBadge(false);
      // Auto-reconnect if we were authenticated
      if (state.token) {
        setTimeout(() => {
          connectWebSocket().then(() => {
            sendMessage({ type: 'connect_to_agent', agent_id: state.agentId });
          }).catch(() => {});
        }, 3000);
      }
    };

    state.ws.onerror = (err) => {
      clearTimeout(timeout);
      reject(new Error('WebSocket error'));
    };
  });
}

function sendMessage(data) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    data.token = state.token;
    data.agent_id = state.agentId;
    state.ws.send(JSON.stringify(data));
  }
}

function updateConnectionBadge(connected) {
  const badge = document.getElementById('connection-badge');
  if (connected) {
    badge.classList.remove('disconnected');
    badge.querySelector('span').textContent = 'Connected';
  } else {
    badge.classList.add('disconnected');
    badge.querySelector('span').textContent = 'Disconnected';
  }
}

// ══════════════════════════════════════════════════════════════
//  MESSAGE ROUTER
// ══════════════════════════════════════════════════════════════
function handleMessage(data) {
  const type = data.type || data.request_type || '';

  switch (type) {
    // Auth
    case 'auth':
      if (data.success) onAuthSuccess(data);
      else showLoginError(data.error || 'Authentication failed');
      break;

    // Connection
    case 'welcome':
      break;
    case 'connect_result':
      if (data.success) {
        if (!document.getElementById('app').classList.contains('hidden')) break;
        document.getElementById('login-screen').classList.add('hidden');
        document.getElementById('app').classList.remove('hidden');
      }
      break;

    // Screen
    case 'screen_started':
      state.streaming = true;
      state.screenWidth = data.monitors?.[0]?.width || 1920;
      state.screenHeight = data.monitors?.[0]?.height || 1080;
      document.getElementById('screen-overlay').classList.add('hidden');
      document.getElementById('screen-controls').classList.remove('hidden');
      break;
    case 'screen_stopped':
      state.streaming = false;
      break;
    case 'monitors':
      updateMonitorList(data.monitors);
      break;
    case 'screen_settings_updated':
      break;

    // WebRTC signaling
    case 'webrtc_answer':
      handleWebRTCAnswer(data);
      break;
    case 'webrtc_candidate':
      handleICECandidate(data);
      break;

    // Dashboard
    case 'dashboard':
      updateDashboard(data.data);
      break;
    case 'processes':
      updateProcessList(data.data);
      break;
    case 'kill_result':
      toast(data.success ? `Killed process ${data.name}` : data.error, data.success ? 'success' : 'error');
      sendMessage({ type: 'processes' });
      break;

    // System controls
    case 'confirm_required':
      showConfirmDialog(data.message, () => {
        sendMessage({ type: data.action, confirmed: true });
      });
      break;

    // File manager
    case 'file_list':
      if (data.success !== false) updateFileList(data);
      break;
    case 'drives':
      updateDrives(data.drives);
      break;
    case 'file_download':
      if (data.success) downloadFileToPhone(data);
      else toast(data.error, 'error');
      break;
    case 'confirm_required':
      break;
    case 'file_delete':
      if (data.action === 'confirm_required') {
        showConfirmDialog(data.message, () => {
          sendMessage({ type: 'file_delete_confirm', token: data.confirmation_token });
        });
      }
      break;

    // Terminal
    case 'terminal_output':
      appendTerminalOutput(data);
      break;
    case 'terminal_create':
      if (data.success !== false) onTerminalCreated(data);
      break;

    // Clipboard
    case 'clipboard_get':
      if (data.success) document.getElementById('clipboard-text').value = data.content;
      break;

    // Webcam
    case 'webcam_snapshot':
      if (data.success) showWebcamSnapshot(data);
      break;

    // Volume/Brightness
    case 'volume_get':
      if (data.success) {
        document.getElementById('volume-slider').value = data.volume;
        document.getElementById('volume-value').textContent = data.volume + '%';
      }
      break;
    case 'brightness_get':
      if (data.success) {
        document.getElementById('brightness-slider').value = data.brightness;
        document.getElementById('brightness-value').textContent = data.brightness + '%';
      }
      break;

    // Notifications
    case 'notification':
      handleNotification(data);
      break;

    // Errors
    case 'error':
      toast(data.error || 'An error occurred', 'error');
      break;

    default:
      if (data.success === true) toast('Done', 'success');
      else if (data.success === false) toast(data.error || 'Failed', 'error');
  }
}

function handleBinaryMessage(buffer) {
  const view = new Uint8Array(buffer);
  if (view[0] === 0x01) {
    // Screen frame
    const frameData = buffer.slice(1);
    renderScreenFrame(frameData);
  }
}

// ══════════════════════════════════════════════════════════════
//  NAVIGATION
// ══════════════════════════════════════════════════════════════
function initNavigation() {
  document.querySelectorAll('.nav-item').forEach(btn => {
    btn.addEventListener('click', () => {
      const tab = btn.dataset.tab;
      switchTab(tab);
    });
  });
}

function switchTab(tabId) {
  // Deactivate all
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));

  // Activate selected
  document.getElementById(tabId).classList.add('active');
  document.querySelector(`.nav-item[data-tab="${tabId}"]`).classList.add('active');
  state.currentTab = tabId;

  // Tab-specific actions
  if (tabId === 'dashboard-view') requestDashboard();
  if (tabId === 'files-view' && !state.currentPath) {
    sendMessage({ type: 'file_drives' });
  }
  if (tabId === 'controls-view') {
    sendMessage({ type: 'volume_get' });
    sendMessage({ type: 'brightness_get' });
  }
}

// ══════════════════════════════════════════════════════════════
//  SCREEN VIEW & TOUCH GESTURES
// ══════════════════════════════════════════════════════════════
function initScreen() {
  const canvas = document.getElementById('screen-canvas');
  const container = document.getElementById('screen-container');
  const ctx = canvas.getContext('2d');

  // Start button
  document.getElementById('start-screen-btn').addEventListener('click', () => {
    sendMessage({ type: 'screen_start', fps: 30, quality: 80 });
  });

  // ── Touch gesture engine ──
  let touches = {};
  let lastTapTime = 0;
  let longPressTimer = null;
  let isDragging = false;
  let dragStartPos = null;

  container.addEventListener('touchstart', (e) => {
    if (!state.streaming) return;
    e.preventDefault();

    const touch = e.changedTouches[0];
    const pos = getTouchPosition(touch, canvas);
    touches[touch.identifier] = { start: pos, current: pos, startTime: Date.now() };

    // Long press detection
    clearTimeout(longPressTimer);
    if (e.touches.length === 1) {
      longPressTimer = setTimeout(() => {
        sendMessage({
          type: 'gesture',
          gesture_type: 'long_press',
          x: pos.x, y: pos.y,
        });
        toast('Right-click', 'info');
      }, 500);
    }

    // Two-finger detection
    if (e.touches.length === 2) {
      clearTimeout(longPressTimer);
    }
  }, { passive: false });

  container.addEventListener('touchmove', (e) => {
    if (!state.streaming) return;
    e.preventDefault();
    clearTimeout(longPressTimer);

    if (e.touches.length === 1) {
      const touch = e.changedTouches[0];
      const pos = getTouchPosition(touch, canvas);
      const start = touches[touch.identifier]?.start;

      if (start) {
        const dx = pos.x - (touches[touch.identifier].current?.x || pos.x);
        const dy = pos.y - (touches[touch.identifier].current?.y || pos.y);

        // Move cursor
        sendMessage({
          type: 'mouse_move',
          x: Math.round(pos.x * state.screenWidth),
          y: Math.round(pos.y * state.screenHeight),
        });

        touches[touch.identifier].current = pos;
        isDragging = true;
      }
    } else if (e.touches.length === 2) {
      // Two-finger scroll
      const t1 = e.touches[0];
      const t2 = e.touches[1];
      const pos1 = getTouchPosition(t1, canvas);
      const prev1 = touches[t1.identifier]?.current || pos1;

      const dy = (pos1.y - prev1.y) * 10;
      if (Math.abs(dy) > 0.01) {
        sendMessage({
          type: 'mouse_scroll',
          dx: 0,
          dy: Math.round(dy > 0 ? -1 : 1),
        });
      }

      touches[t1.identifier] = { ...touches[t1.identifier], current: pos1 };
    }
  }, { passive: false });

  container.addEventListener('touchend', (e) => {
    if (!state.streaming) return;
    e.preventDefault();
    clearTimeout(longPressTimer);

    const touch = e.changedTouches[0];
    const touchData = touches[touch.identifier];

    if (touchData) {
      const duration = Date.now() - touchData.startTime;
      const pos = touchData.start;

      if (!isDragging && duration < 300) {
        const now = Date.now();
        if (now - lastTapTime < 300) {
          // Double tap → double click
          sendMessage({
            type: 'mouse_click',
            button: 'left', count: 2,
            x: Math.round(pos.x * state.screenWidth),
            y: Math.round(pos.y * state.screenHeight),
          });
          lastTapTime = 0;
        } else {
          // Single tap → click
          lastTapTime = now;
          setTimeout(() => {
            if (lastTapTime === now) {
              sendMessage({
                type: 'mouse_click',
                button: 'left', count: 1,
                x: Math.round(pos.x * state.screenWidth),
                y: Math.round(pos.y * state.screenHeight),
              });
            }
          }, 300);
        }
      }

      delete touches[touch.identifier];
      isDragging = false;
    }
  }, { passive: false });

  // ── Screen Control Buttons ──
  document.getElementById('sc-click').addEventListener('click', () => {
    sendMessage({ type: 'mouse_click', button: 'left', count: 1 });
  });
  document.getElementById('sc-rightclick').addEventListener('click', () => {
    sendMessage({ type: 'mouse_click', button: 'right', count: 1 });
  });
  document.getElementById('sc-scroll-up').addEventListener('click', () => {
    sendMessage({ type: 'mouse_scroll', dx: 0, dy: 3 });
  });
  document.getElementById('sc-scroll-down').addEventListener('click', () => {
    sendMessage({ type: 'mouse_scroll', dx: 0, dy: -3 });
  });

  // Keyboard toggle
  const hiddenInput = document.getElementById('hidden-keyboard');
  document.getElementById('keyboard-toggle').addEventListener('click', () => {
    hiddenInput.classList.toggle('hidden');
    hiddenInput.focus();
  });

  hiddenInput.addEventListener('input', (e) => {
    const text = e.target.value;
    if (text) {
      sendMessage({ type: 'type_text', text });
      e.target.value = '';
    }
  });

  hiddenInput.addEventListener('keydown', (e) => {
    const specialKeys = ['Enter', 'Backspace', 'Delete', 'Tab', 'Escape',
      'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'];
    if (specialKeys.includes(e.key)) {
      e.preventDefault();
      const keyMap = {
        'Enter': 'enter', 'Backspace': 'backspace', 'Delete': 'delete',
        'Tab': 'tab', 'Escape': 'escape',
        'ArrowUp': 'up', 'ArrowDown': 'down',
        'ArrowLeft': 'left', 'ArrowRight': 'right',
      };
      sendMessage({ type: 'key_press', key: keyMap[e.key] || e.key.toLowerCase() });
    }
  });

  // Settings button
  document.getElementById('sc-settings').addEventListener('click', () => {
    document.getElementById('settings-drawer').classList.remove('hidden');
  });

  // Fullscreen
  document.getElementById('fullscreen-btn').addEventListener('click', () => {
    if (document.fullscreenElement) {
      document.exitFullscreen();
    } else {
      document.documentElement.requestFullscreen().catch(() => {});
    }
  });
}

function getTouchPosition(touch, canvas) {
  const rect = canvas.getBoundingClientRect();
  return {
    x: Math.max(0, Math.min(1, (touch.clientX - rect.left) / rect.width)),
    y: Math.max(0, Math.min(1, (touch.clientY - rect.top) / rect.height)),
  };
}

// Screen frame rendering
const screenImage = new Image();
let frameBlob = null;

function renderScreenFrame(frameData) {
  const canvas = document.getElementById('screen-canvas');
  const ctx = canvas.getContext('2d');

  if (frameBlob) URL.revokeObjectURL(frameBlob);
  const blob = new Blob([frameData], { type: 'image/jpeg' });
  const url = URL.createObjectURL(blob);

  screenImage.onload = () => {
    if (canvas.width !== screenImage.width || canvas.height !== screenImage.height) {
      canvas.width = screenImage.width;
      canvas.height = screenImage.height;
    }
    ctx.drawImage(screenImage, 0, 0);
    URL.revokeObjectURL(url);
  };
  screenImage.src = url;
}

// WebRTC
async function startWebRTC() {
  const config = {
    iceServers: [
      { urls: 'stun:stun.l.google.com:19302' },
      { urls: 'stun:stun1.l.google.com:19302' },
    ],
  };

  // Try to get TURN credentials
  try {
    const resp = await fetch(state.serverUrl.replace('ws', 'http') + '/api/turn-credentials', {
      headers: { 'Authorization': `Bearer ${state.token}` },
    });
    const turnData = await resp.json();
    if (turnData.servers) config.iceServers.push(...turnData.servers);
  } catch (e) { /* TURN not available */ }

  state.pc = new RTCPeerConnection(config);

  state.pc.ontrack = (event) => {
    const video = document.getElementById('screen-video');
    video.srcObject = event.streams[0];
    video.classList.remove('hidden');
    document.getElementById('screen-canvas').classList.add('hidden');
    document.getElementById('screen-overlay').classList.add('hidden');
    document.getElementById('screen-controls').classList.remove('hidden');
    state.streaming = true;
  };

  // Create data channel for control
  state.dataChannel = state.pc.createDataChannel('control', { ordered: true });
  state.dataChannel.onopen = () => toast('Data channel open', 'success');

  state.pc.onicecandidate = (event) => {
    if (event.candidate) {
      sendMessage({
        type: 'webrtc_candidate',
        candidate: event.candidate.toJSON(),
      });
    }
  };

  // Create offer
  const offer = await state.pc.createOffer({ offerToReceiveVideo: true });
  await state.pc.setLocalDescription(offer);

  sendMessage({
    type: 'webrtc_offer',
    sdp: offer.sdp,
    sdp_type: offer.type,
  });
}

async function handleWebRTCAnswer(data) {
  if (state.pc) {
    await state.pc.setRemoteDescription(new RTCSessionDescription({
      sdp: data.sdp, type: data.sdp_type,
    }));
  }
}

async function handleICECandidate(data) {
  if (state.pc && data.candidate) {
    await state.pc.addIceCandidate(new RTCIceCandidate(data.candidate));
  }
}

function updateMonitorList(monitors) {
  const select = document.getElementById('setting-monitor');
  select.innerHTML = '';
  (monitors || []).forEach(m => {
    const opt = document.createElement('option');
    opt.value = m.index;
    opt.textContent = `Monitor ${m.index} (${m.width}×${m.height})`;
    select.appendChild(opt);
  });
}

// ══════════════════════════════════════════════════════════════
//  DASHBOARD
// ══════════════════════════════════════════════════════════════
function initDashboard() {
  document.getElementById('refresh-dashboard').addEventListener('click', requestDashboard);
  document.getElementById('refresh-processes').addEventListener('click', () => {
    sendMessage({ type: 'processes', sort_by: 'cpu', limit: 30 });
  });
}

function requestDashboard() {
  sendMessage({ type: 'dashboard' });
  sendMessage({ type: 'processes', sort_by: 'cpu', limit: 30 });
}

function updateDashboard(data) {
  if (!data) return;

  // CPU
  if (data.cpu) {
    const pct = Math.round(data.cpu.percent);
    document.getElementById('cpu-value').textContent = pct + '%';
    document.getElementById('cpu-ring').setAttribute('stroke-dasharray', `${pct} ${100 - pct}`);
  }

  // RAM
  if (data.memory) {
    const pct = Math.round(data.memory.percent);
    document.getElementById('ram-value').textContent = pct + '%';
    document.getElementById('ram-ring').setAttribute('stroke-dasharray', `${pct} ${100 - pct}`);
  }

  // Disk
  if (data.disk && data.disk.length) {
    const pct = Math.round(data.disk[0].percent);
    document.getElementById('disk-value').textContent = pct + '%';
    document.getElementById('disk-ring').setAttribute('stroke-dasharray', `${pct} ${100 - pct}`);
  }

  // Battery
  if (data.battery) {
    const pct = Math.round(data.battery.percent);
    document.getElementById('battery-value').textContent = pct + '%';
    document.getElementById('battery-ring').setAttribute('stroke-dasharray', `${pct} ${100 - pct}`);
    const icon = data.battery.power_plugged ? 'battery_charging_full' : 'battery_full';
    document.querySelector('#battery-card .stat-icon .material-icons-round').textContent = icon;
  }

  // System Info
  if (data.system_info) {
    const si = data.system_info;
    document.getElementById('system-info').innerHTML = `
      <div class="info-item"><span class="info-label">OS</span><span class="info-value">${si.os} ${si.os_release}</span></div>
      <div class="info-item"><span class="info-label">Host</span><span class="info-value">${si.hostname}</span></div>
      <div class="info-item"><span class="info-label">CPU</span><span class="info-value">${si.processor || 'N/A'}</span></div>
      <div class="info-item"><span class="info-label">Uptime</span><span class="info-value">${data.uptime?.uptime_human || 'N/A'}</span></div>
    `;
  }

  // Active Window
  if (data.active_window) {
    document.getElementById('active-window').textContent = data.active_window.title;
  }

  // Network
  if (data.network) {
    const net = data.network;
    document.getElementById('network-info').innerHTML = `
      <div class="info-item"><span class="info-label">Sent</span><span class="info-value">${net.bytes_sent_mb} MB</span></div>
      <div class="info-item"><span class="info-label">Received</span><span class="info-value">${net.bytes_recv_mb} MB</span></div>
    `;
  }
}

function updateProcessList(processes) {
  const el = document.getElementById('process-list');
  if (!processes || !processes.length) {
    el.innerHTML = '<p class="placeholder">No processes</p>';
    return;
  }

  el.innerHTML = processes.map(p => `
    <div class="process-item">
      <span class="process-name" title="${p.name}">${p.name}</span>
      <span class="process-cpu">${p.cpu_percent.toFixed(1)}%</span>
      <span class="process-mem">${p.memory_percent.toFixed(1)}%</span>
      <button class="process-kill" onclick="killProcess(${p.pid}, '${p.name}')" title="Kill">
        <span class="material-icons-round">close</span>
      </button>
    </div>
  `).join('');
}

function killProcess(pid, name) {
  showConfirmDialog(`Kill process "${name}" (PID ${pid})?`, () => {
    sendMessage({ type: 'kill_process', pid });
  });
}

// ══════════════════════════════════════════════════════════════
//  SYSTEM CONTROLS
// ══════════════════════════════════════════════════════════════
function initControls() {
  const actions = {
    'ctrl-lock': () => sendMessage({ type: 'lock' }),
    'ctrl-unlock': () => sendMessage({ type: 'unlock' }),
    'ctrl-sleep': () => showConfirmDialog('Put laptop to sleep?', () => sendMessage({ type: 'sleep' })),
    'ctrl-restart': () => sendMessage({ type: 'restart' }),
    'ctrl-shutdown': () => sendMessage({ type: 'shutdown' }),
    'ctrl-cancel-shutdown': () => sendMessage({ type: 'cancel_shutdown' }),
  };

  Object.entries(actions).forEach(([id, handler]) => {
    document.getElementById(id).addEventListener('click', handler);
  });

  // Volume
  const volSlider = document.getElementById('volume-slider');
  volSlider.addEventListener('input', (e) => {
    document.getElementById('volume-value').textContent = e.target.value + '%';
  });
  volSlider.addEventListener('change', (e) => {
    sendMessage({ type: 'volume_set', level: parseInt(e.target.value) });
  });
  document.getElementById('mute-btn').addEventListener('click', () => {
    sendMessage({ type: 'volume_mute' });
  });

  // Brightness
  const brSlider = document.getElementById('brightness-slider');
  brSlider.addEventListener('input', (e) => {
    document.getElementById('brightness-value').textContent = e.target.value + '%';
  });
  brSlider.addEventListener('change', (e) => {
    sendMessage({ type: 'brightness_set', level: parseInt(e.target.value) });
  });

  // Clipboard
  document.getElementById('clipboard-get').addEventListener('click', () => {
    sendMessage({ type: 'clipboard_get' });
  });
  document.getElementById('clipboard-set').addEventListener('click', () => {
    const content = document.getElementById('clipboard-text').value;
    sendMessage({ type: 'clipboard_set', content });
    toast('Clipboard updated', 'success');
  });

  // Webcam
  document.getElementById('webcam-snap').addEventListener('click', () => {
    sendMessage({ type: 'webcam_snapshot', camera: 0 });
  });

  // WoL
  document.getElementById('wol-send').addEventListener('click', () => {
    const mac = document.getElementById('wol-mac').value.trim();
    if (mac) sendMessage({ type: 'wake_on_lan', mac_address: mac });
    else toast('Enter a MAC address', 'warning');
  });
}

function showWebcamSnapshot(data) {
  const preview = document.getElementById('webcam-preview');
  preview.innerHTML = `<img src="data:image/jpeg;base64,${data.image_b64}" alt="Webcam snapshot">`;
}

// ══════════════════════════════════════════════════════════════
//  FILE MANAGER
// ══════════════════════════════════════════════════════════════
function initFiles() {
  document.getElementById('file-back').addEventListener('click', () => {
    if (state.currentPath) {
      const parent = state.currentPath.replace(/[/\\][^/\\]*$/, '') || '/';
      browsePath(parent);
    }
  });

  document.getElementById('file-home').addEventListener('click', () => {
    sendMessage({ type: 'file_drives' });
    state.currentPath = '';
    document.getElementById('file-drives').classList.remove('hidden');
    document.getElementById('file-list').innerHTML = '<p class="placeholder">Select a drive</p>';
    document.getElementById('file-path').textContent = '/';
  });

  document.getElementById('file-upload-btn').addEventListener('click', () => {
    document.getElementById('file-upload-input').click();
  });

  document.getElementById('file-upload-input').addEventListener('change', async (e) => {
    const files = e.target.files;
    for (const file of files) {
      const reader = new FileReader();
      reader.onload = () => {
        const b64 = reader.result.split(',')[1];
        const path = state.currentPath + '/' + file.name;
        sendMessage({ type: 'file_upload', path, data_b64: b64, overwrite: false });
        toast(`Uploading ${file.name}...`, 'info');
      };
      reader.readAsDataURL(file);
    }
    e.target.value = '';
  });

  document.getElementById('file-mkdir-btn').addEventListener('click', () => {
    const name = prompt('New folder name:');
    if (name) {
      sendMessage({ type: 'file_mkdir', path: state.currentPath + '/' + name });
      setTimeout(() => browsePath(state.currentPath), 500);
    }
  });
}

function browsePath(path) {
  state.currentPath = path;
  document.getElementById('file-path').textContent = path;
  document.getElementById('file-drives').classList.add('hidden');
  sendMessage({ type: 'file_list', path });
}

function updateDrives(drives) {
  const el = document.getElementById('file-drives');
  el.classList.remove('hidden');
  el.innerHTML = drives.map(d => `
    <button class="file-drive" onclick="browsePath('${d.path.replace(/\\/g, '\\\\')}')" title="${d.label}">
      <span class="material-icons-round" style="font-size:18px;color:var(--accent)">storage</span>
      ${d.label}
      ${d.total_gb ? `<br><small style="color:var(--text-muted)">${d.free_gb || '?'}/${d.total_gb} GB</small>` : ''}
    </button>
  `).join('');
}

function updateFileList(data) {
  const el = document.getElementById('file-list');
  state.currentPath = data.path;
  document.getElementById('file-path').textContent = data.path;

  if (!data.items || !data.items.length) {
    el.innerHTML = '<p class="placeholder">Empty directory</p>';
    return;
  }

  el.innerHTML = data.items.map(item => {
    const escapedPath = item.path.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    const icon = item.is_dir
      ? '<span class="material-icons-round fi-icon-folder">folder</span>'
      : '<span class="material-icons-round fi-icon-file">description</span>';
    const meta = item.is_dir ? '' : (item.size_human || '');

    return `
      <div class="file-item" onclick="${item.is_dir ? `browsePath('${escapedPath}')` : ''}">
        ${icon}
        <div class="file-item-info">
          <div class="file-item-name">${item.name}</div>
          <div class="file-item-meta">${meta}</div>
        </div>
        <div class="file-item-actions">
          ${!item.is_dir ? `<button class="icon-btn small" onclick="event.stopPropagation(); downloadFile('${escapedPath}')" title="Download"><span class="material-icons-round">download</span></button>` : ''}
          <button class="icon-btn small" onclick="event.stopPropagation(); deleteFile('${escapedPath}')" title="Delete"><span class="material-icons-round">delete</span></button>
        </div>
      </div>
    `;
  }).join('');
}

function downloadFile(path) {
  sendMessage({ type: 'file_download', path });
  toast('Downloading...', 'info');
}

function downloadFileToPhone(data) {
  const bytes = atob(data.data_b64);
  const arr = new Uint8Array(bytes.length);
  for (let i = 0; i < bytes.length; i++) arr[i] = bytes.charCodeAt(i);
  const blob = new Blob([arr], { type: data.mime_type || 'application/octet-stream' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = data.name; a.click();
  URL.revokeObjectURL(url);
  toast(`Downloaded ${data.name}`, 'success');
}

function deleteFile(path) {
  sendMessage({ type: 'file_delete', path });
}

// ══════════════════════════════════════════════════════════════
//  TERMINAL
// ══════════════════════════════════════════════════════════════
function initTerminal() {
  document.getElementById('terminal-new').addEventListener('click', () => {
    sendMessage({ type: 'terminal_create' });
  });

  document.getElementById('terminal-close').addEventListener('click', () => {
    if (state.terminalSessionId) {
      sendMessage({ type: 'terminal_close', session_id: state.terminalSessionId });
      state.terminalSessionId = '';
      document.getElementById('terminal-output').innerHTML = '';
    }
  });

  document.getElementById('terminal-ctrl-c').addEventListener('click', () => {
    if (state.terminalSessionId) {
      sendMessage({ type: 'terminal_signal', session_id: state.terminalSessionId, signal: 'SIGINT' });
    }
  });

  const input = document.getElementById('terminal-input');
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      const cmd = input.value;
      if (state.terminalSessionId && cmd) {
        sendMessage({ type: 'terminal_input', session_id: state.terminalSessionId, command: cmd });
        appendTerminalLine('$ ' + cmd, 'stdin');
        input.value = '';
      }
    }
  });

  document.getElementById('terminal-send').addEventListener('click', () => {
    const cmd = input.value;
    if (state.terminalSessionId && cmd) {
      sendMessage({ type: 'terminal_input', session_id: state.terminalSessionId, command: cmd });
      appendTerminalLine('$ ' + cmd, 'stdin');
      input.value = '';
    }
  });
}

function onTerminalCreated(data) {
  state.terminalSessionId = data.session_id;
  const select = document.getElementById('terminal-sessions');
  const opt = document.createElement('option');
  opt.value = data.session_id;
  opt.textContent = `${data.session_id} (${data.shell})`;
  opt.selected = true;
  select.appendChild(opt);
  document.getElementById('terminal-output').innerHTML = '';
  toast('Terminal session started', 'success');
}

function appendTerminalOutput(data) {
  if (data.session_id === state.terminalSessionId || !state.terminalSessionId) {
    const cls = data.type === 'stderr' ? 'stderr' : 'stdout';
    appendTerminalLine(data.data, cls);
  }
}

function appendTerminalLine(text, cls = 'stdout') {
  const output = document.getElementById('terminal-output');
  const span = document.createElement('span');
  span.className = cls;
  span.textContent = text;
  output.appendChild(span);
  output.scrollTop = output.scrollHeight;
}

// ══════════════════════════════════════════════════════════════
//  SETTINGS DRAWER
// ══════════════════════════════════════════════════════════════
function initSettings() {
  document.getElementById('menu-btn').addEventListener('click', () => {
    document.getElementById('settings-drawer').classList.remove('hidden');
    // Update connection info
    document.getElementById('conn-status-text').textContent = state.connected ? 'Connected' : 'Disconnected';
    document.getElementById('conn-agent-id').textContent = state.agentId;
  });

  document.getElementById('close-settings').addEventListener('click', () => {
    document.getElementById('settings-drawer').classList.add('hidden');
  });

  document.querySelector('.drawer-backdrop').addEventListener('click', () => {
    document.getElementById('settings-drawer').classList.add('hidden');
  });

  // Quality slider
  const qualSlider = document.getElementById('setting-quality');
  qualSlider.addEventListener('input', (e) => {
    document.getElementById('setting-quality-val').textContent = e.target.value + '%';
  });
  qualSlider.addEventListener('change', (e) => {
    sendMessage({ type: 'screen_settings', quality: parseInt(e.target.value) });
  });

  // FPS slider
  const fpsSlider = document.getElementById('setting-fps');
  fpsSlider.addEventListener('input', (e) => {
    document.getElementById('setting-fps-val').textContent = e.target.value;
  });
  fpsSlider.addEventListener('change', (e) => {
    sendMessage({ type: 'screen_settings', fps: parseInt(e.target.value) });
  });

  // Monitor select
  document.getElementById('setting-monitor').addEventListener('change', (e) => {
    sendMessage({ type: 'screen_settings', monitor: parseInt(e.target.value) });
  });

  // Disconnect
  document.getElementById('setting-disconnect').addEventListener('click', () => {
    showConfirmDialog('Disconnect from laptop?', () => {
      localStorage.removeItem('rd_token');
      state.token = '';
      if (state.ws) state.ws.close();
      document.getElementById('app').classList.add('hidden');
      document.getElementById('login-screen').classList.remove('hidden');
      document.getElementById('settings-drawer').classList.add('hidden');
    });
  });

  // Sessions & Audit
  document.getElementById('setting-sessions').addEventListener('click', () => {
    sendMessage({ type: 'sessions_list' });
    toast('Check console for session data', 'info');
  });
  document.getElementById('setting-audit').addEventListener('click', () => {
    sendMessage({ type: 'audit_log', count: 50 });
    toast('Check console for audit data', 'info');
  });
}

// ══════════════════════════════════════════════════════════════
//  PANIC BUTTON
// ══════════════════════════════════════════════════════════════
function initPanicButton() {
  document.getElementById('panic-btn').addEventListener('click', () => {
    showConfirmDialog(
      '🚨 PANIC: Kill ALL sessions immediately? This will disconnect everything.',
      () => {
        sendMessage({ type: 'panic', agent_id: state.agentId });
        toast('All sessions terminated!', 'warning');
      }
    );
  });
}

// ══════════════════════════════════════════════════════════════
//  NOTIFICATIONS
// ══════════════════════════════════════════════════════════════
function handleNotification(data) {
  toast(data.message, data.priority === 'urgent' ? 'error' : 'warning');

  // Browser notification
  if ('Notification' in window && Notification.permission === 'granted') {
    new Notification('RemoteDesk', { body: data.message, icon: '🖥️' });
  } else if ('Notification' in window && Notification.permission !== 'denied') {
    Notification.requestPermission();
  }
}

// ══════════════════════════════════════════════════════════════
//  UI HELPERS
// ══════════════════════════════════════════════════════════════
function showConfirmDialog(message, callback) {
  const dialog = document.getElementById('confirm-dialog');
  document.getElementById('confirm-message').textContent = message;
  dialog.classList.remove('hidden');
  state.confirmCallback = callback;

  document.getElementById('confirm-ok').onclick = () => {
    dialog.classList.add('hidden');
    if (state.confirmCallback) state.confirmCallback();
    state.confirmCallback = null;
  };
  document.getElementById('confirm-cancel').onclick = () => {
    dialog.classList.add('hidden');
    state.confirmCallback = null;
  };
  document.querySelector('.dialog-backdrop').onclick = () => {
    dialog.classList.add('hidden');
    state.confirmCallback = null;
  };
}

function toast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  const icons = {
    success: 'check_circle', error: 'error', warning: 'warning', info: 'info',
  };
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.innerHTML = `<span class="material-icons-round">${icons[type] || 'info'}</span><span>${message}</span>`;
  container.appendChild(el);

  setTimeout(() => {
    el.classList.add('toast-exit');
    setTimeout(() => el.remove(), 300);
  }, 4000);
}

// Make functions available globally for inline onclick handlers
window.browsePath = browsePath;
window.downloadFile = downloadFile;
window.deleteFile = deleteFile;
window.killProcess = killProcess;
