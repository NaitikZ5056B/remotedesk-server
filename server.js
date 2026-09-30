/**
 * RemoteDesk - Signaling & Relay Server
 *
 * A lightweight Node.js server that:
 * 1. Relays WebSocket messages between agent (laptop) and client (phone)
 * 2. Handles WebRTC signaling (SDP offer/answer, ICE candidates)
 * 3. Provides REST API for auth, pairing, and TURN credentials
 * 4. Enforces JWT authentication, rate limiting, and audit logging
 *
 * Deploy to Render, Fly.io, Railway, or any VPS.
 */

require('dotenv').config();
const http = require('http');
const express = require('express');
const { WebSocketServer, WebSocket } = require('ws');
const jwt = require('jsonwebtoken');
const bcrypt = require('bcryptjs');
const { v4: uuidv4 } = require('uuid');
const cors = require('cors');
const helmet = require('helmet');
const rateLimit = require('express-rate-limit');

// ── Configuration ──
const PORT = parseInt(process.env.PORT || '4000');
const HOST = process.env.HOST || '0.0.0.0';
const JWT_SECRET = process.env.JWT_SECRET || 'change-me-in-production';
const BCRYPT_ROUNDS = parseInt(process.env.BCRYPT_ROUNDS || '12');

// ── Express App ──
const app = express();
app.use(helmet());
app.use(cors({
  origin: process.env.CORS_ORIGINS === '*' ? true : (process.env.CORS_ORIGINS || '').split(','),
}));
app.use(express.json({ limit: '50mb' }));

// Rate limiting
const limiter = rateLimit({
  windowMs: parseInt(process.env.RATE_LIMIT_WINDOW_MS || '900000'), // 15 min
  max: parseInt(process.env.RATE_LIMIT_MAX || '100'),
  message: { error: 'Too many requests, please try again later.' },
});
app.use('/api/', limiter);

// Stricter rate limit for auth
const authLimiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  max: 10,
  message: { error: 'Too many login attempts.' },
});

// ── In-Memory State ──
const agents = new Map();     // deviceId -> { ws, info }
const clients = new Map();    // clientId -> { ws, agentId, info }
const pairings = new Map();   // code -> { agentId, deviceId, deviceSecret, expiresAt }
const auditLog = [];          // { timestamp, action, details }
const MAX_AUDIT_LOG = 10000;

// ── Audit Logger ──
function audit(action, details = {}) {
  const entry = {
    timestamp: new Date().toISOString(),
    action,
    ...details,
  };
  auditLog.push(entry);
  if (auditLog.length > MAX_AUDIT_LOG) auditLog.shift();
  if (process.env.LOG_LEVEL === 'debug') {
    console.log(`[AUDIT] ${action}`, JSON.stringify(details));
  }
}

// ── JWT Helpers ──
function createToken(payload, expiresIn = '24h') {
  return jwt.sign(payload, JWT_SECRET, { expiresIn });
}

function verifyToken(token) {
  try {
    return jwt.verify(token, JWT_SECRET);
  } catch {
    return null;
  }
}

// ── REST API Routes ──

// Health check
app.get('/health', (req, res) => {
  res.json({
    status: 'ok',
    agents: agents.size,
    clients: clients.size,
    uptime: process.uptime(),
  });
});

// Get TURN credentials (time-limited)
app.get('/api/turn-credentials', (req, res) => {
  const token = req.headers.authorization?.replace('Bearer ', '');
  if (!token || !verifyToken(token)) {
    return res.status(401).json({ error: 'Unauthorized' });
  }

  const turnServer = process.env.TURN_SERVER;
  if (!turnServer) {
    return res.json({ servers: [] });
  }

  // Generate time-limited TURN credentials using shared secret
  const turnSecret = process.env.TURN_SECRET;
  if (turnSecret) {
    const crypto = require('crypto');
    const timestamp = Math.floor(Date.now() / 1000) + 24 * 3600; // 24h expiry
    const username = `${timestamp}:remotedesk`;
    const hmac = crypto.createHmac('sha1', turnSecret);
    hmac.update(username);
    const credential = hmac.digest('base64');

    return res.json({
      servers: [{
        urls: [turnServer, turnServer.replace('turn:', 'turns:').replace(':3478', ':5349')],
        username,
        credential,
      }],
    });
  }

  // Static credentials fallback
  res.json({
    servers: [{
      urls: [turnServer],
      username: process.env.TURN_USERNAME || '',
      credential: process.env.TURN_PASSWORD || '',
    }],
  });
});

// Pairing endpoint
app.post('/api/pair', authLimiter, (req, res) => {
  const { code, phone_device_id } = req.body;
  const pairing = pairings.get(code);

  if (!pairing) {
    audit('pair_failed', { reason: 'invalid_code', phone_device_id });
    return res.status(400).json({ error: 'Invalid or expired pairing code' });
  }

  if (Date.now() > pairing.expiresAt) {
    pairings.delete(code);
    return res.status(400).json({ error: 'Pairing code expired' });
  }

  // Complete pairing
  const token = createToken({
    sub: phone_device_id,
    agentId: pairing.agentId,
    type: 'client',
  });

  // Notify agent
  const agent = agents.get(pairing.agentId);
  if (agent && agent.ws.readyState === WebSocket.OPEN) {
    agent.ws.send(JSON.stringify({
      type: 'pair_complete',
      code,
      phone_device_id,
    }));
  }

  pairings.delete(code);
  audit('pair_success', { agentId: pairing.agentId, phone_device_id });

  res.json({
    success: true,
    token,
    agentId: pairing.agentId,
    deviceSecret: pairing.deviceSecret,
  });
});

// Get audit log
app.get('/api/audit', (req, res) => {
  const token = req.headers.authorization?.replace('Bearer ', '');
  if (!token || !verifyToken(token)) {
    return res.status(401).json({ error: 'Unauthorized' });
  }
  const count = parseInt(req.query.count || '50');
  res.json({ entries: auditLog.slice(-count) });
});

// Serve PWA static files
app.use(express.static('../pwa'));

// ── HTTP Server ──
const server = http.createServer(app);

// ── WebSocket Server ──
const wss = new WebSocketServer({ server, maxPayload: 50 * 1024 * 1024 });

wss.on('connection', (ws, req) => {
  const deviceId = req.headers['x-device-id'] || uuidv4();
  const deviceType = req.headers['x-device-type'] || 'unknown';
  const clientIp = req.headers['x-forwarded-for'] || req.socket.remoteAddress;
  const connectionId = uuidv4();

  console.log(`[WS] New ${deviceType} connection: ${deviceId} from ${clientIp}`);

  // Connection state
  let role = deviceType; // 'agent' or 'client'
  let authenticated = false;
  let linkedAgentId = null;
  let lastHeartbeat = Date.now();

  // Message handler
  ws.on('message', (rawMessage) => {
    try {
      // Handle binary messages (screen frames, file data)
      if (rawMessage instanceof Buffer && rawMessage[0] === 0x01) {
        // Screen frame: relay to all clients of this agent
        if (role === 'agent') {
          relayBinaryToClients(deviceId, rawMessage);
        }
        return;
      }

      const data = JSON.parse(rawMessage.toString());
      handleMessage(ws, data, {
        connectionId,
        deviceId,
        clientIp,
        role,
        authenticated,
        linkedAgentId,
      });

      // Update state based on message handling
      if (data.type === 'register') {
        role = data.role || role;
        if (role === 'agent') {
          agents.set(deviceId, {
            ws,
            info: { deviceId, ip: clientIp, connectedAt: new Date().toISOString() },
          });
          audit('agent_connected', { deviceId, ip: clientIp });
        }
      }

    } catch (err) {
      console.error(`[WS] Message error:`, err.message);
    }
  });

  ws.on('close', (code, reason) => {
    console.log(`[WS] Disconnected: ${deviceId} (${code})`);
    if (role === 'agent') {
      agents.delete(deviceId);
      // Notify all clients of this agent
      notifyClientsOfAgent(deviceId, {
        type: 'notification',
        event: 'agent_disconnected',
        message: '⚠️ Laptop disconnected',
      });
      audit('agent_disconnected', { deviceId });
    } else {
      clients.delete(connectionId);
      // Notify agent that client left
      if (linkedAgentId) {
        const agent = agents.get(linkedAgentId);
        if (agent && agent.ws.readyState === WebSocket.OPEN) {
          agent.ws.send(JSON.stringify({
            type: 'client_disconnected',
            client_id: connectionId,
          }));
        }
      }
      audit('client_disconnected', { connectionId, deviceId });
    }
  });

  ws.on('error', (err) => {
    console.error(`[WS] Error for ${deviceId}:`, err.message);
  });

  // Register client
  if (role === 'client' || deviceType === 'unknown') {
    clients.set(connectionId, {
      ws,
      agentId: null,
      info: { connectionId, deviceId, ip: clientIp },
    });
  }

  // Send welcome
  ws.send(JSON.stringify({
    type: 'welcome',
    connectionId,
    serverTime: Date.now(),
  }));
});

// ── Message Router ──
function handleMessage(ws, data, ctx) {
  const { type } = data;

  switch (type) {
    case 'register':
      handleRegister(ws, data, ctx);
      break;

    case 'auth':
      handleAuth(ws, data, ctx);
      break;

    case 'heartbeat':
      ws.send(JSON.stringify({ type: 'heartbeat_ack', timestamp: Date.now() }));
      break;

    case 'pong':
      break;

    // WebRTC signaling - relay between agent and client
    case 'webrtc_offer':
    case 'webrtc_answer':
    case 'webrtc_candidate':
      relayWebRTC(ws, data, ctx);
      break;

    // Register pairing code from agent
    case 'register_pairing':
      pairings.set(data.code, {
        agentId: ctx.deviceId,
        deviceId: data.device_id,
        deviceSecret: data.device_secret,
        expiresAt: Date.now() + (data.expires_in || 300) * 1000,
      });
      audit('pairing_registered', { agentId: ctx.deviceId });
      break;

    // Client connecting to specific agent
    case 'connect_to_agent':
      connectClientToAgent(ws, data, ctx);
      break;

    // Panic button - kill all sessions
    case 'panic':
      handlePanic(ws, data, ctx);
      break;

    // Default: relay to the appropriate target
    default:
      relayMessage(ws, data, ctx);
      break;
  }
}

function handleRegister(ws, data, ctx) {
  if (data.role === 'agent') {
    agents.set(ctx.deviceId, {
      ws,
      info: {
        deviceId: ctx.deviceId,
        ip: ctx.clientIp,
        connectedAt: new Date().toISOString(),
      },
    });
    ws.send(JSON.stringify({
      type: 'register_ack',
      status: 'ok',
      deviceId: ctx.deviceId,
    }));
    console.log(`[SERVER] Agent registered: ${ctx.deviceId}`);
  }
}

function handleAuth(ws, data, ctx) {
  // Relay auth to the agent
  const agentId = data.agent_id || ctx.linkedAgentId;
  const agent = agents.get(agentId);

  if (!agent || agent.ws.readyState !== WebSocket.OPEN) {
    ws.send(JSON.stringify({
      type: 'auth_response',
      success: false,
      error: 'Agent not connected',
    }));
    return;
  }

  // Forward to agent for verification
  data.client_id = ctx.connectionId;
  data.ip_address = ctx.clientIp;
  agent.ws.send(JSON.stringify(data));

  // Link client to agent
  const client = clients.get(ctx.connectionId);
  if (client) {
    client.agentId = agentId;
    ctx.linkedAgentId = agentId;
  }
}

function relayWebRTC(ws, data, ctx) {
  if (ctx.role === 'client') {
    // Client -> Agent
    const agentId = data.agent_id || ctx.linkedAgentId;
    const client = clients.get(ctx.connectionId);
    if (client) {
      ctx.linkedAgentId = agentId;
      client.agentId = agentId;
    }
    const agent = agents.get(agentId);
    if (agent && agent.ws.readyState === WebSocket.OPEN) {
      data.client_id = ctx.connectionId;
      agent.ws.send(JSON.stringify(data));
    }
  } else if (ctx.role === 'agent') {
    // Agent -> Client
    const clientId = data.client_id;
    const client = clients.get(clientId);
    if (client && client.ws.readyState === WebSocket.OPEN) {
      client.ws.send(JSON.stringify(data));
    }
  }
}

function relayMessage(ws, data, ctx) {
  if (ctx.role === 'client') {
    // Client -> Agent
    const agentId = data.agent_id || ctx.linkedAgentId;
    const agent = agents.get(agentId);
    if (agent && agent.ws.readyState === WebSocket.OPEN) {
      data.client_id = ctx.connectionId;
      data.ip_address = ctx.clientIp;
      agent.ws.send(JSON.stringify(data));
    } else {
      ws.send(JSON.stringify({
        type: 'error',
        error: 'Agent not connected',
      }));
    }
  } else if (ctx.role === 'agent') {
    // Agent -> Client (response)
    const clientId = data.client_id;
    if (clientId) {
      const client = clients.get(clientId);
      if (client && client.ws.readyState === WebSocket.OPEN) {
        client.ws.send(JSON.stringify(data));
      }
    } else {
      // Broadcast to all clients of this agent
      broadcastToClients(ctx.deviceId, data);
    }
  }
}

function relayBinaryToClients(agentId, data) {
  for (const [_, client] of clients) {
    if (client.agentId === agentId && client.ws.readyState === WebSocket.OPEN) {
      client.ws.send(data);
    }
  }
}

function broadcastToClients(agentId, data) {
  const msg = JSON.stringify(data);
  for (const [_, client] of clients) {
    if (client.agentId === agentId && client.ws.readyState === WebSocket.OPEN) {
      client.ws.send(msg);
    }
  }
}

function notifyClientsOfAgent(agentId, notification) {
  broadcastToClients(agentId, notification);
}

function connectClientToAgent(ws, data, ctx) {
  const agentId = data.agent_id;
  const agent = agents.get(agentId);

  if (!agent || agent.ws.readyState !== WebSocket.OPEN) {
    ws.send(JSON.stringify({
      type: 'connect_result',
      success: false,
      error: 'Agent is offline',
    }));
    return;
  }

  const client = clients.get(ctx.connectionId);
  if (client) {
    client.agentId = agentId;
    ctx.linkedAgentId = agentId;
  }

  ws.send(JSON.stringify({
    type: 'connect_result',
    success: true,
    agentId,
    agentInfo: agent.info,
  }));

  audit('client_connected_to_agent', {
    clientId: ctx.connectionId,
    agentId,
    ip: ctx.clientIp,
  });
}

function handlePanic(ws, data, ctx) {
  // Relay panic to agent
  const agentId = data.agent_id || ctx.linkedAgentId;
  const agent = agents.get(agentId);
  if (agent && agent.ws.readyState === WebSocket.OPEN) {
    agent.ws.send(JSON.stringify({ type: 'panic' }));
  }

  // Disconnect all clients of this agent
  for (const [id, client] of clients) {
    if (client.agentId === agentId && client.ws.readyState === WebSocket.OPEN) {
      client.ws.send(JSON.stringify({
        type: 'notification',
        event: 'panic',
        message: '🚨 All sessions terminated via panic button',
      }));
      client.ws.close(1000, 'Panic button activated');
    }
  }

  audit('panic_button', { agentId, triggeredBy: ctx.connectionId });
}

// ── Heartbeat Check (clean up stale connections) ──
setInterval(() => {
  wss.clients.forEach((ws) => {
    if (ws.readyState === WebSocket.OPEN) {
      ws.ping();
    }
  });
}, 30000);

// ── Start Server ──
server.listen(PORT, HOST, () => {
  console.log('');
  console.log('╔══════════════════════════════════════════════════╗');
  console.log('║          RemoteDesk Signaling Server             ║');
  console.log('╠══════════════════════════════════════════════════╣');
  console.log(`║  HTTP/WS: http://${HOST}:${PORT}                    ║`);
  console.log(`║  Health:  http://${HOST}:${PORT}/health              ║`);
  console.log('╚══════════════════════════════════════════════════╝');
  console.log('');
});
