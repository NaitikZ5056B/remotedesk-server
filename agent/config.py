"""
RemoteDesk Agent - Configuration Management
Loads settings from .env and provides defaults.
"""
import os
import secrets
import json
from pathlib import Path
from dotenv import load_dotenv

# Load .env from agent directory
ENV_PATH = Path(__file__).parent / ".env"
load_dotenv(ENV_PATH)


class Config:
    """Centralized configuration for the RemoteDesk agent."""

    # ── Server ──
    SIGNAL_SERVER_URL: str = os.getenv("SIGNAL_SERVER_URL", "ws://localhost:4000")

    # ── Device Identity ──
    DEVICE_ID: str = os.getenv("DEVICE_ID", "")
    DEVICE_SECRET: str = os.getenv("DEVICE_SECRET", "")

    # ── Security ──
    JWT_SECRET: str = os.getenv("JWT_SECRET", secrets.token_hex(32))
    ENCRYPTION_KEY: str = os.getenv("ENCRYPTION_KEY", "")
    TOTP_SECRET: str = os.getenv("TOTP_SECRET", "")
    ALLOWED_IPS: list = json.loads(os.getenv("ALLOWED_IPS", "[]"))
    ALLOWED_DEVICES: list = json.loads(os.getenv("ALLOWED_DEVICES", "[]"))

    # ── Screen Capture ──
    DEFAULT_FPS: int = int(os.getenv("DEFAULT_FPS", "30"))
    DEFAULT_QUALITY: int = int(os.getenv("DEFAULT_QUALITY", "80"))
    DEFAULT_MONITOR: int = int(os.getenv("DEFAULT_MONITOR", "1"))
    MAX_FPS: int = 60
    MIN_FPS: int = 1
    MAX_QUALITY: int = 100
    MIN_QUALITY: int = 10

    # ── WebRTC ──
    TURN_SERVER: str = os.getenv("TURN_SERVER", "")
    TURN_USERNAME: str = os.getenv("TURN_USERNAME", "")
    TURN_PASSWORD: str = os.getenv("TURN_PASSWORD", "")
    STUN_SERVERS: list = [
        "stun:stun.l.google.com:19302",
        "stun:stun1.l.google.com:19302",
    ]

    # ── Reconnection ──
    RECONNECT_INTERVAL: int = int(os.getenv("RECONNECT_INTERVAL", "5"))
    MAX_RECONNECT_ATTEMPTS: int = int(os.getenv("MAX_RECONNECT_ATTEMPTS", "0"))  # 0 = infinite

    # ── Logging ──
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    AUDIT_LOG_PATH: str = os.getenv("AUDIT_LOG_PATH", str(Path(__file__).parent / "logs" / "audit.json"))

    # ── Paths ──
    BASE_DIR: Path = Path(__file__).parent
    LOGS_DIR: Path = BASE_DIR / "logs"
    CERTS_DIR: Path = BASE_DIR / "certs"
    DATA_DIR: Path = BASE_DIR / "data"

    @classmethod
    def init_dirs(cls):
        """Create required directories."""
        for d in [cls.LOGS_DIR, cls.CERTS_DIR, cls.DATA_DIR]:
            d.mkdir(parents=True, exist_ok=True)

    @classmethod
    def get_ice_servers(cls) -> list:
        """Return ICE server configuration for WebRTC."""
        servers = [{"urls": s} for s in cls.STUN_SERVERS]
        if cls.TURN_SERVER:
            servers.append({
                "urls": cls.TURN_SERVER,
                "username": cls.TURN_USERNAME,
                "credential": cls.TURN_PASSWORD,
            })
        return servers

    @classmethod
    def save_device_identity(cls, device_id: str, device_secret: str):
        """Persist device identity to .env file."""
        cls.DEVICE_ID = device_id
        cls.DEVICE_SECRET = device_secret
        _update_env("DEVICE_ID", device_id)
        _update_env("DEVICE_SECRET", device_secret)

    @classmethod
    def save_totp_secret(cls, secret: str):
        """Persist TOTP secret to .env file."""
        cls.TOTP_SECRET = secret
        _update_env("TOTP_SECRET", secret)


def _update_env(key: str, value: str):
    """Update or add a key in the .env file."""
    env_file = ENV_PATH
    lines = []
    found = False

    if env_file.exists():
        with open(env_file, "r") as f:
            for line in f:
                if line.strip().startswith(f"{key}="):
                    lines.append(f"{key}={value}\n")
                    found = True
                else:
                    lines.append(line)

    if not found:
        lines.append(f"{key}={value}\n")

    with open(env_file, "w") as f:
        f.writelines(lines)
