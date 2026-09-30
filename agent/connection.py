"""
RemoteDesk Agent - Connection Manager
Handles WebSocket signaling connection and WebRTC peer connections.
Manages reconnection, heartbeat, and message routing.
"""
import json
import asyncio
import time
import logging
from typing import Optional, Callable

import websockets
from websockets.exceptions import ConnectionClosed

try:
    from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer
    from aiortc.contrib.media import MediaRelay
    HAS_WEBRTC = True
except ImportError:
    HAS_WEBRTC = False

from config import Config
from screen_capture import ScreenCaptureTrack
from audit_log import audit_logger

logger = logging.getLogger("remotedesk.connection")


class ConnectionManager:
    """
    Manages the outbound WebSocket connection to the signaling server
    and WebRTC peer connections to phone clients.
    """

    def __init__(self):
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._peer_connections: dict[str, RTCPeerConnection] = {}
        self._data_channels: dict[str, object] = {}
        self._screen_capture: Optional[ScreenCaptureTrack] = None
        self._message_handler: Optional[Callable] = None
        self._connected = False
        self._reconnect_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._reconnect_count = 0

    def set_message_handler(self, handler: Callable):
        """Set the callback for incoming messages."""
        self._message_handler = handler

    def set_screen_capture(self, capture: ScreenCaptureTrack):
        """Set the screen capture track for WebRTC streaming."""
        self._screen_capture = capture

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ws is not None

    # ── WebSocket Connection ──

    async def connect(self):
        """Connect to the signaling server."""
        url = Config.SIGNAL_SERVER_URL
        logger.info(f"Connecting to signaling server: {url}")

        try:
            self._ws = await websockets.connect(
                url,
                extra_headers={
                    "X-Device-ID": Config.DEVICE_ID,
                    "X-Device-Type": "agent",
                },
                ping_interval=20,
                ping_timeout=10,
                max_size=50 * 1024 * 1024,  # 50MB max message
            )
            self._connected = True
            self._reconnect_count = 0
            logger.info("Connected to signaling server")

            # Register with server
            await self._send({
                "type": "register",
                "role": "agent",
                "device_id": Config.DEVICE_ID,
            })

            # Start heartbeat
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            # Listen for messages
            await self._listen()

        except Exception as e:
            logger.error(f"Connection failed: {e}")
            self._connected = False
            await self._schedule_reconnect()

    async def _listen(self):
        """Listen for incoming WebSocket messages."""
        try:
            async for message in self._ws:
                try:
                    if isinstance(message, bytes):
                        # Binary message (file transfer, etc.)
                        if self._message_handler:
                            await self._message_handler({
                                "type": "binary",
                                "data": message,
                            })
                    else:
                        data = json.loads(message)
                        await self._handle_message(data)
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON message received")
                except Exception as e:
                    logger.error(f"Error handling message: {e}")
        except ConnectionClosed as e:
            logger.warning(f"WebSocket closed: {e}")
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
        finally:
            self._connected = False
            await self._schedule_reconnect()

    async def _handle_message(self, data: dict):
        """Route incoming messages."""
        msg_type = data.get("type", "")

        if msg_type == "webrtc_offer":
            await self._handle_webrtc_offer(data)
        elif msg_type == "webrtc_answer":
            await self._handle_webrtc_answer(data)
        elif msg_type == "webrtc_candidate":
            await self._handle_ice_candidate(data)
        elif msg_type == "ping":
            await self._send({"type": "pong"})
        else:
            # Forward to the main message handler
            if self._message_handler:
                await self._message_handler(data)

    async def _send(self, data: dict):
        """Send a JSON message via WebSocket."""
        if self._ws and self._connected:
            try:
                await self._ws.send(json.dumps(data))
            except Exception as e:
                logger.error(f"Send failed: {e}")
                self._connected = False

    async def send_message(self, data: dict):
        """Public send method."""
        await self._send(data)

    async def send_binary(self, data: bytes):
        """Send binary data via WebSocket."""
        if self._ws and self._connected:
            try:
                await self._ws.send(data)
            except Exception as e:
                logger.error(f"Binary send failed: {e}")

    # ── WebRTC ──

    async def _handle_webrtc_offer(self, data: dict):
        """Handle an incoming WebRTC offer from a phone client."""
        if not HAS_WEBRTC:
            logger.warning("WebRTC not available (aiortc not installed)")
            await self._send({
                "type": "webrtc_error",
                "error": "WebRTC not available on agent",
                "client_id": data.get("client_id"),
            })
            return

        client_id = data.get("client_id", "unknown")
        logger.info(f"WebRTC offer from client: {client_id}")

        # Create ICE configuration
        ice_servers = []
        for server_config in Config.get_ice_servers():
            urls = server_config.get("urls", "")
            if "turn:" in urls:
                ice_servers.append(RTCIceServer(
                    urls=[urls],
                    username=server_config.get("username", ""),
                    credential=server_config.get("credential", ""),
                ))
            else:
                ice_servers.append(RTCIceServer(urls=[urls]))

        config = RTCConfiguration(iceServers=ice_servers)
        pc = RTCPeerConnection(config)
        self._peer_connections[client_id] = pc

        # Add screen capture track
        if self._screen_capture:
            pc.addTrack(self._screen_capture)

        # Handle data channel
        @pc.on("datachannel")
        def on_datachannel(channel):
            self._data_channels[client_id] = channel
            logger.info(f"Data channel opened: {channel.label}")

            @channel.on("message")
            async def on_message(message):
                try:
                    cmd = json.loads(message)
                    cmd["client_id"] = client_id
                    cmd["type"] = f"dc_{cmd.get('type', 'unknown')}"
                    if self._message_handler:
                        await self._message_handler(cmd)
                except Exception as e:
                    logger.error(f"Data channel message error: {e}")

        @pc.on("connectionstatechange")
        async def on_state_change():
            state = pc.connectionState
            logger.info(f"WebRTC connection state: {state}")
            if state in ("failed", "closed"):
                await self._cleanup_peer(client_id)

        # Set remote description and create answer
        offer = RTCSessionDescription(sdp=data["sdp"], type=data["sdp_type"])
        await pc.setRemoteDescription(offer)

        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        # Send answer back
        await self._send({
            "type": "webrtc_answer",
            "sdp": pc.localDescription.sdp,
            "sdp_type": pc.localDescription.type,
            "client_id": client_id,
        })

    async def _handle_webrtc_answer(self, data: dict):
        """Handle WebRTC answer (if agent initiated the offer)."""
        client_id = data.get("client_id", "unknown")
        pc = self._peer_connections.get(client_id)
        if pc:
            answer = RTCSessionDescription(sdp=data["sdp"], type=data["sdp_type"])
            await pc.setRemoteDescription(answer)

    async def _handle_ice_candidate(self, data: dict):
        """Handle incoming ICE candidate."""
        client_id = data.get("client_id", "unknown")
        pc = self._peer_connections.get(client_id)
        if pc and HAS_WEBRTC:
            from aiortc import RTCIceCandidate
            # aiortc handles ICE candidates internally through the SDP exchange
            # Additional trickle ICE candidates can be added here if needed
            pass

    async def send_to_datachannel(self, client_id: str, data: dict):
        """Send data through a WebRTC data channel."""
        channel = self._data_channels.get(client_id)
        if channel and channel.readyState == "open":
            channel.send(json.dumps(data))

    async def _cleanup_peer(self, client_id: str):
        """Clean up a peer connection."""
        pc = self._peer_connections.pop(client_id, None)
        if pc:
            await pc.close()
        self._data_channels.pop(client_id, None)

    # ── Heartbeat & Reconnection ──

    async def _heartbeat_loop(self):
        """Send periodic heartbeat to keep connection alive."""
        while self._connected:
            try:
                await self._send({
                    "type": "heartbeat",
                    "timestamp": time.time(),
                    "device_id": Config.DEVICE_ID,
                })
                await asyncio.sleep(15)
            except Exception:
                break

    async def _schedule_reconnect(self):
        """Schedule a reconnection attempt."""
        max_attempts = Config.MAX_RECONNECT_ATTEMPTS
        if max_attempts > 0 and self._reconnect_count >= max_attempts:
            logger.error("Max reconnection attempts reached")
            return

        self._reconnect_count += 1
        delay = min(Config.RECONNECT_INTERVAL * (2 ** min(self._reconnect_count - 1, 6)), 300)
        logger.info(f"Reconnecting in {delay}s (attempt {self._reconnect_count})")
        await asyncio.sleep(delay)
        asyncio.create_task(self.connect())

    # ── Cleanup ──

    async def disconnect(self):
        """Disconnect everything."""
        self._connected = False

        # Cancel tasks
        if self._heartbeat_task:
            self._heartbeat_task.cancel()

        # Close peer connections
        for client_id in list(self._peer_connections.keys()):
            await self._cleanup_peer(client_id)

        # Close WebSocket
        if self._ws:
            await self._ws.close()
            self._ws = None

        logger.info("Disconnected")

    def get_status(self) -> dict:
        """Get connection status."""
        return {
            "websocket_connected": self._connected,
            "peer_connections": len(self._peer_connections),
            "data_channels": len(self._data_channels),
            "reconnect_count": self._reconnect_count,
        }
