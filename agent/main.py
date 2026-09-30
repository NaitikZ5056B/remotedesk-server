"""
RemoteDesk Agent - Main Entry Point
Orchestrates all modules: screen capture, input control, system monitoring,
file management, terminal, extras, security, and connections.
Auto-starts, auto-reconnects, and runs as a background service.
"""
import sys
import json
import asyncio
import logging
import signal
import platform
from pathlib import Path

from config import Config
from connection import ConnectionManager
from screen_capture import ScreenCaptureTrack, ScreenStreamer
from input_controller import InputController
from system_monitor import SystemMonitor
from system_controls import SystemControls
from file_manager import FileManager
from terminal_handler import TerminalManager
from extras import WebcamCapture, ClipboardSync, MediaControls, NotificationSender
from security import SecurityManager
from audit_log import audit_logger

# ── Logging Setup ──
logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(Config.LOGS_DIR / "agent.log", encoding="utf-8"),
    ]
)
logger = logging.getLogger("remotedesk")


class RemoteDeskAgent:
    """Main agent that ties all modules together."""

    def __init__(self):
        Config.init_dirs()

        # Core modules
        self.connection = ConnectionManager()
        self.screen = ScreenCaptureTrack()
        self.streamer = ScreenStreamer()
        self.input = InputController()
        self.monitor = SystemMonitor()
        self.controls = SystemControls()
        self.files = FileManager()
        self.terminal = TerminalManager()
        self.webcam = WebcamCapture()
        self.clipboard = ClipboardSync()
        self.media = MediaControls()
        self.notifications = NotificationSender()
        self.security = SecurityManager()

        # State
        self._running = False
        self._streaming = False
        self._ws_stream_task = None

        # Wire up
        self.connection.set_message_handler(self._handle_message)
        self.connection.set_screen_capture(self.screen)
        self.notifications.set_callback(self.connection.send_message)

    async def start(self):
        """Start the agent."""
        logger.info("=" * 60)
        logger.info("RemoteDesk Agent starting...")
        logger.info(f"OS: {platform.system()} {platform.release()}")
        logger.info(f"Device ID: {Config.DEVICE_ID or 'Not paired yet'}")
        logger.info(f"Server: {Config.SIGNAL_SERVER_URL}")
        logger.info("=" * 60)

        self._running = True

        # First-time setup check
        if not Config.DEVICE_ID:
            await self._first_time_setup()

        # Start audit log flusher
        asyncio.create_task(audit_logger.start_periodic_flush())

        # Connect to signaling server
        await self.connection.connect()

    async def _first_time_setup(self):
        """Interactive first-time setup."""
        import secrets
        print("\n" + "=" * 60)
        print("  RemoteDesk - First Time Setup")
        print("=" * 60)

        # Generate device identity
        device_id = f"agent-{secrets.token_hex(8)}"
        device_secret = secrets.token_hex(32)
        Config.save_device_identity(device_id, device_secret)

        # Set password
        print("\nSet an access password (used from phone to connect):")
        import getpass
        password = getpass.getpass("Password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Passwords don't match!")
            sys.exit(1)
        self.security.set_password(password)

        # Setup 2FA
        print("\nSetting up 2FA (TOTP)...")
        totp_data = self.security.setup_totp()
        print(f"TOTP Secret: {totp_data['secret']}")
        print("Scan the QR code with your authenticator app (Google Authenticator, Authy, etc.)")
        print(f"Or use this URI: {totp_data['provisioning_uri']}")

        # Generate pairing QR
        print("\nGenerating pairing QR code...")
        pairing = self.security.generate_pairing_code()
        print(f"Pairing data: {json.dumps(pairing['pairing_data'], indent=2)}")
        print("\nScan the QR code from the RemoteDesk phone app to pair.")
        print(f"Pairing code expires in {pairing['expires_in']} seconds.")

        # Save QR code to file
        import base64
        qr_path = Config.DATA_DIR / "pairing_qr.png"
        with open(qr_path, "wb") as f:
            f.write(base64.b64decode(pairing["qr_code_b64"]))
        print(f"QR code saved to: {qr_path}")

        print("\n" + "=" * 60)
        print(f"  Device ID: {device_id}")
        print(f"  Ready to connect!")
        print("=" * 60 + "\n")

        await audit_logger.log("first_time_setup", {
            "device_id": device_id,
        }, risk_level="high")

    async def _handle_message(self, data: dict):
        """Route incoming messages to appropriate handlers."""
        msg_type = data.get("type", "")
        client_id = data.get("client_id", "")

        # Messages requiring authentication
        authenticated_types = {
            "auth", "pair_complete", "ping", "register_ack",
        }
        if msg_type not in authenticated_types:
            token = data.get("token", "")
            if token:
                payload = self.security.validate_session(token)
                if not payload:
                    await self.connection.send_message({
                        "type": "error",
                        "error": "Invalid or expired session",
                        "client_id": client_id,
                    })
                    return

        try:
            response = await self._dispatch(msg_type, data)
            if response:
                response["client_id"] = client_id
                response["request_type"] = msg_type
                await self.connection.send_message(response)
        except Exception as e:
            logger.error(f"Handler error for {msg_type}: {e}", exc_info=True)
            await self.connection.send_message({
                "type": "error",
                "error": str(e),
                "client_id": client_id,
            })

    async def _dispatch(self, msg_type: str, data: dict) -> dict | None:
        """Dispatch a message to the correct handler."""

        # ── Authentication ──
        if msg_type == "auth":
            return await self.security.authenticate(
                password=data.get("password", ""),
                totp_code=data.get("totp_code", ""),
                device_id=data.get("device_id", ""),
                ip_address=data.get("ip_address", ""),
            )

        if msg_type == "pair_complete":
            result = self.security.complete_pairing(
                code=data.get("code", ""),
                phone_device_id=data.get("phone_device_id", ""),
            )
            return {"type": "pair_result", "success": result is not None, "data": result}

        # ── Screen Control ──
        if msg_type == "screen_start":
            return await self._start_streaming(data)

        if msg_type == "screen_stop":
            self._stop_streaming()
            return {"type": "screen_stopped"}

        if msg_type == "screen_settings":
            if "fps" in data:
                self.screen.set_fps(data["fps"])
            if "quality" in data:
                self.screen.set_quality(data["quality"])
            if "monitor" in data:
                self.screen.set_monitor(data["monitor"])
            return {"type": "screen_settings_updated", "stats": self.screen.get_stats()}

        if msg_type == "screen_monitors":
            return {"type": "monitors", "monitors": self.screen.monitors}

        # ── Input Control ──
        if msg_type == "mouse_move":
            await self.input.mouse_move(data.get("x", 0), data.get("y", 0),
                                        data.get("relative", False))
            return None  # No response needed for frequent events

        if msg_type == "mouse_click":
            await self.input.mouse_click(
                data.get("button", "left"),
                data.get("count", 1),
                data.get("x"), data.get("y")
            )
            return None

        if msg_type == "mouse_scroll":
            await self.input.mouse_scroll(data.get("dx", 0), data.get("dy", 0))
            return None

        if msg_type == "mouse_drag":
            await self.input.mouse_drag(
                data["start_x"], data["start_y"],
                data["end_x"], data["end_y"],
                data.get("button", "left")
            )
            return None

        if msg_type == "key_press":
            await self.input.key_press(data["key"])
            return None

        if msg_type == "key_combo":
            await self.input.key_combo(data["keys"])
            return None

        if msg_type == "type_text":
            await self.input.type_text(data["text"])
            return None

        if msg_type == "gesture":
            await self.input.handle_gesture(data)
            return None

        # Also handle data channel messages (prefixed with dc_)
        if msg_type.startswith("dc_"):
            return await self._dispatch(msg_type[3:], data)

        # ── System Dashboard ──
        if msg_type == "dashboard":
            dashboard = await self.monitor.get_full_dashboard()
            return {"type": "dashboard", "data": dashboard}

        if msg_type == "processes":
            procs = await self.monitor.get_processes(
                data.get("sort_by", "cpu"),
                data.get("limit", 30)
            )
            return {"type": "processes", "data": procs}

        if msg_type == "kill_process":
            result = await self.monitor.kill_process(
                data["pid"], data.get("force", False)
            )
            return {"type": "kill_result", **result}

        # ── System Controls ──
        if msg_type == "lock":
            return await self.controls.lock_screen()

        if msg_type == "unlock":
            return await self.controls.unlock_screen(data.get("password"))

        if msg_type == "sleep":
            return await self.controls.sleep()

        if msg_type == "restart":
            # Dangerous action: require confirmation
            if not data.get("confirmed"):
                return {"type": "confirm_required",
                        "action": "restart",
                        "message": "Are you sure you want to restart?"}
            return await self.controls.restart(data.get("delay", 5))

        if msg_type == "shutdown":
            if not data.get("confirmed"):
                return {"type": "confirm_required",
                        "action": "shutdown",
                        "message": "Are you sure you want to shut down?"}
            return await self.controls.shutdown(data.get("delay", 10))

        if msg_type == "cancel_shutdown":
            return await self.controls.cancel_shutdown()

        if msg_type == "wake_on_lan":
            return await self.controls.send_wol(
                data["mac_address"],
                data.get("broadcast", "255.255.255.255"),
            )

        # ── File Manager ──
        if msg_type == "file_list":
            return await self.files.list_directory(data["path"])

        if msg_type == "file_info":
            return await self.files.get_file_info(data["path"])

        if msg_type == "file_read":
            return await self.files.read_file(
                data["path"],
                data.get("offset", 0),
                data.get("limit", 1024 * 1024),
            )

        if msg_type == "file_download":
            return await self.files.download_file(data["path"])

        if msg_type == "file_upload":
            return await self.files.upload_file(
                data["path"],
                data["data_b64"],
                data.get("overwrite", False),
            )

        if msg_type == "file_delete":
            return await self.files.request_delete(data["path"])

        if msg_type == "file_delete_confirm":
            return await self.files.confirm_delete(data["token"])

        if msg_type == "file_rename":
            return await self.files.rename(data["path"], data["new_name"])

        if msg_type == "file_copy":
            return await self.files.copy(data["src"], data["dst"])

        if msg_type == "file_mkdir":
            return await self.files.create_directory(data["path"])

        if msg_type == "file_drives":
            drives = await self.files.get_drives()
            return {"type": "drives", "drives": drives}

        # ── Terminal ──
        if msg_type == "terminal_create":
            return await self.terminal.create_session(
                output_callback=self._terminal_output,
                shell=data.get("shell"),
            )

        if msg_type == "terminal_input":
            success = await self.terminal.send_input(
                data["session_id"], data["command"]
            )
            return {"type": "terminal_input_ack", "success": success}

        if msg_type == "terminal_signal":
            success = await self.terminal.send_signal(
                data["session_id"], data.get("signal", "SIGINT")
            )
            return {"type": "terminal_signal_ack", "success": success}

        if msg_type == "terminal_close":
            success = await self.terminal.close_session(data["session_id"])
            return {"type": "terminal_closed", "success": success}

        if msg_type == "terminal_list":
            return {"type": "terminal_sessions",
                    "sessions": self.terminal.list_sessions()}

        # ── Extras ──
        if msg_type == "webcam_snapshot":
            return await self.webcam.take_snapshot(data.get("camera", 0))

        if msg_type == "webcam_list":
            cameras = await self.webcam.list_cameras()
            return {"type": "cameras", "cameras": cameras}

        if msg_type == "clipboard_get":
            return await self.clipboard.get_clipboard()

        if msg_type == "clipboard_set":
            return await self.clipboard.set_clipboard(data["content"])

        if msg_type == "volume_get":
            return await self.media.get_volume()

        if msg_type == "volume_set":
            return await self.media.set_volume(data["level"])

        if msg_type == "volume_mute":
            return await self.media.toggle_mute()

        if msg_type == "brightness_get":
            return await self.media.get_brightness()

        if msg_type == "brightness_set":
            return await self.media.set_brightness(data["level"])

        # ── Security / Session Management ──
        if msg_type == "panic":
            count = self.security.revoke_all_sessions()
            self._stop_streaming()
            await self.terminal.close_all()
            await audit_logger.log("panic_button", {"sessions_killed": count},
                                   risk_level="critical")
            return {"type": "panic_result", "sessions_killed": count}

        if msg_type == "sessions_list":
            return {"type": "sessions",
                    "sessions": self.security.get_active_sessions()}

        if msg_type == "session_revoke":
            self.security.revoke_session(data["jti"])
            return {"type": "session_revoked"}

        if msg_type == "audit_log":
            entries = await audit_logger.get_recent(
                data.get("count", 50),
                data.get("action_filter"),
            )
            return {"type": "audit_log", "entries": entries}

        if msg_type == "generate_pairing":
            pairing = self.security.generate_pairing_code()
            return {"type": "pairing_code", **pairing}

        # ── Connection Status ──
        if msg_type == "status":
            return {
                "type": "status",
                "connection": self.connection.get_status(),
                "screen": self.screen.get_stats(),
                "streaming": self._streaming,
            }

        logger.warning(f"Unknown message type: {msg_type}")
        return {"type": "error", "error": f"Unknown command: {msg_type}"}

    async def _start_streaming(self, data: dict) -> dict:
        """Start screen streaming via WebSocket fallback."""
        if self._streaming:
            return {"type": "screen_already_streaming"}

        # Apply settings
        if "fps" in data:
            self.streamer.capture.set_fps(data["fps"])
        if "quality" in data:
            self.streamer.capture.set_quality(data["quality"])
        if "monitor" in data:
            self.streamer.capture.set_monitor(data["monitor"])

        self._streaming = True

        async def send_frame(frame_bytes):
            """Send frame as binary WebSocket message with header."""
            # Prepend a 1-byte header to distinguish frame data
            header = b"\x01"  # 0x01 = screen frame
            await self.connection.send_binary(header + frame_bytes)

        self._ws_stream_task = asyncio.create_task(
            self.streamer.start_streaming(send_frame)
        )

        await audit_logger.log("screen_start", {
            "fps": self.streamer.capture._fps,
            "quality": self.streamer.capture._quality,
        })

        return {
            "type": "screen_started",
            "stats": self.streamer.capture.get_stats(),
            "monitors": self.screen.monitors,
        }

    def _stop_streaming(self):
        """Stop screen streaming."""
        self._streaming = False
        self.streamer.stop_streaming()
        if self._ws_stream_task:
            self._ws_stream_task.cancel()
            self._ws_stream_task = None

    async def _terminal_output(self, data: dict):
        """Callback for terminal output - send to phone."""
        await self.connection.send_message({
            "type": "terminal_output",
            **data,
        })

    async def stop(self):
        """Gracefully stop the agent."""
        logger.info("Stopping RemoteDesk Agent...")
        self._running = False
        self._stop_streaming()
        await self.terminal.close_all()
        await self.connection.disconnect()
        await audit_logger.flush()
        logger.info("Agent stopped.")


# ── Entry Point ──

async def main():
    agent = RemoteDeskAgent()

    # Handle graceful shutdown
    loop = asyncio.get_event_loop()

    def shutdown_handler():
        asyncio.create_task(agent.stop())

    if platform.system() != "Windows":
        loop.add_signal_handler(signal.SIGINT, shutdown_handler)
        loop.add_signal_handler(signal.SIGTERM, shutdown_handler)

    try:
        await agent.start()
    except KeyboardInterrupt:
        await agent.stop()


if __name__ == "__main__":
    asyncio.run(main())
