"""
RemoteDesk Agent - Security Module
Handles device pairing (QR code), JWT tokens, TOTP 2FA,
password hashing, and session management.
"""
import os
import time
import json
import secrets
import hashlib
import hmac
import base64
from datetime import datetime, timezone, timedelta
from pathlib import Path

import jwt
import pyotp
import qrcode
import bcrypt
from cryptography.fernet import Fernet
from io import BytesIO

from config import Config
from audit_log import audit_logger


class SecurityManager:
    """Manages authentication, pairing, encryption, and sessions."""

    def __init__(self):
        self._sessions: dict[str, dict] = {}  # token -> session info
        self._pairing_codes: dict[str, dict] = {}  # code -> pairing info
        self._failed_attempts: dict[str, list] = {}  # ip -> [timestamps]
        self._rate_limit_window = 300  # 5 minutes
        self._max_attempts = 5
        self._device_allowlist: list[str] = Config.ALLOWED_DEVICES.copy()
        self._ip_allowlist: list[str] = Config.ALLOWED_IPS.copy()
        self._password_hash: str = ""
        self._fernet: Fernet | None = None
        self._load_credentials()

    def _load_credentials(self):
        """Load stored credentials from data directory."""
        creds_path = Config.DATA_DIR / "credentials.json"
        if creds_path.exists():
            with open(creds_path, "r") as f:
                data = json.load(f)
                self._password_hash = data.get("password_hash", "")
                self._device_allowlist = data.get("allowed_devices", [])
                self._ip_allowlist = data.get("allowed_ips", [])

        # Initialize encryption
        if Config.ENCRYPTION_KEY:
            self._fernet = Fernet(Config.ENCRYPTION_KEY.encode())
        else:
            key = Fernet.generate_key()
            self._fernet = Fernet(key)
            Config.ENCRYPTION_KEY = key.decode()

    def _save_credentials(self):
        """Persist credentials to disk."""
        Config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        creds_path = Config.DATA_DIR / "credentials.json"
        with open(creds_path, "w") as f:
            json.dump({
                "password_hash": self._password_hash,
                "allowed_devices": self._device_allowlist,
                "allowed_ips": self._ip_allowlist,
            }, f, indent=2)

    # ── Password Management ──

    def set_password(self, password: str):
        """Hash and store the access password."""
        self._password_hash = bcrypt.hashpw(
            password.encode(), bcrypt.gensalt()
        ).decode()
        self._save_credentials()

    def verify_password(self, password: str) -> bool:
        """Verify a password against the stored hash."""
        if not self._password_hash:
            return False
        return bcrypt.checkpw(password.encode(), self._password_hash.encode())

    # ── TOTP 2FA ──

    def setup_totp(self) -> dict:
        """Generate a new TOTP secret and return QR code data."""
        secret = pyotp.random_base32()
        Config.save_totp_secret(secret)
        totp = pyotp.TOTP(secret)
        provisioning_uri = totp.provisioning_uri(
            name="RemoteDesk",
            issuer_name="RemoteDesk Agent"
        )
        # Generate QR code as base64
        qr = qrcode.make(provisioning_uri)
        buf = BytesIO()
        qr.save(buf, format="PNG")
        qr_b64 = base64.b64encode(buf.getvalue()).decode()

        return {
            "secret": secret,
            "provisioning_uri": provisioning_uri,
            "qr_code_b64": qr_b64,
        }

    def verify_totp(self, code: str) -> bool:
        """Verify a TOTP code."""
        if not Config.TOTP_SECRET:
            return True  # 2FA not set up, skip
        totp = pyotp.TOTP(Config.TOTP_SECRET)
        return totp.verify(code, valid_window=1)

    # ── Device Pairing ──

    def generate_pairing_code(self) -> dict:
        """Generate a QR code for device pairing."""
        code = secrets.token_urlsafe(32)
        device_id = secrets.token_hex(16)
        device_secret = secrets.token_hex(32)

        self._pairing_codes[code] = {
            "device_id": device_id,
            "device_secret": device_secret,
            "created_at": time.time(),
            "expires_at": time.time() + 300,  # 5 minute expiry
        }

        pairing_data = {
            "server": Config.SIGNAL_SERVER_URL,
            "code": code,
            "device_id": device_id,
            "agent_id": Config.DEVICE_ID or "pending",
        }

        # Generate QR code
        qr = qrcode.make(json.dumps(pairing_data))
        buf = BytesIO()
        qr.save(buf, format="PNG")
        qr_b64 = base64.b64encode(buf.getvalue()).decode()

        return {
            "code": code,
            "qr_code_b64": qr_b64,
            "pairing_data": pairing_data,
            "expires_in": 300,
        }

    def complete_pairing(self, code: str, phone_device_id: str) -> dict | None:
        """Complete the pairing process. Returns credentials or None."""
        pairing = self._pairing_codes.get(code)
        if not pairing:
            return None
        if time.time() > pairing["expires_at"]:
            del self._pairing_codes[code]
            return None

        # Register the phone device
        self._device_allowlist.append(phone_device_id)
        self._save_credentials()

        # Generate long-lived device credentials
        device_secret = pairing["device_secret"]

        # Clean up
        del self._pairing_codes[code]

        return {
            "device_id": pairing["device_id"],
            "device_secret": device_secret,
            "agent_id": Config.DEVICE_ID,
        }

    # ── JWT Sessions ──

    def create_session(self, device_id: str, ip_address: str = "") -> str:
        """Create a new JWT session token."""
        now = datetime.now(timezone.utc)
        payload = {
            "sub": device_id,
            "iat": now.timestamp(),
            "exp": (now + timedelta(hours=24)).timestamp(),
            "jti": secrets.token_hex(16),
            "ip": ip_address,
        }
        token = jwt.encode(payload, Config.JWT_SECRET, algorithm="HS256")

        self._sessions[payload["jti"]] = {
            "device_id": device_id,
            "ip_address": ip_address,
            "created_at": now.isoformat(),
            "last_active": now.isoformat(),
        }

        return token

    def validate_session(self, token: str, ip_address: str = "") -> dict | None:
        """Validate a JWT token. Returns payload or None."""
        try:
            payload = jwt.decode(token, Config.JWT_SECRET, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            return None
        except jwt.InvalidTokenError:
            return None

        jti = payload.get("jti")
        if jti not in self._sessions:
            return None

        # Check IP allowlist (if configured)
        if self._ip_allowlist and ip_address and ip_address not in self._ip_allowlist:
            return None

        # Check device allowlist
        if self._device_allowlist and payload.get("sub") not in self._device_allowlist:
            return None

        # Update last active
        self._sessions[jti]["last_active"] = datetime.now(timezone.utc).isoformat()

        return payload

    def revoke_session(self, jti: str):
        """Revoke a specific session."""
        self._sessions.pop(jti, None)

    def revoke_all_sessions(self):
        """Panic button - revoke ALL sessions immediately."""
        count = len(self._sessions)
        self._sessions.clear()
        return count

    def get_active_sessions(self) -> list[dict]:
        """List all active sessions."""
        return [
            {"jti": jti, **info}
            for jti, info in self._sessions.items()
        ]

    # ── Rate Limiting ──

    def check_rate_limit(self, ip_address: str) -> bool:
        """Check if an IP is rate-limited. Returns True if allowed."""
        now = time.time()
        attempts = self._failed_attempts.get(ip_address, [])
        # Clean old attempts
        attempts = [t for t in attempts if now - t < self._rate_limit_window]
        self._failed_attempts[ip_address] = attempts
        return len(attempts) < self._max_attempts

    def record_failed_attempt(self, ip_address: str):
        """Record a failed login attempt."""
        if ip_address not in self._failed_attempts:
            self._failed_attempts[ip_address] = []
        self._failed_attempts[ip_address].append(time.time())

    # ── Encryption Helpers ──

    def encrypt(self, data: str) -> str:
        """Encrypt data using Fernet."""
        if self._fernet:
            return self._fernet.encrypt(data.encode()).decode()
        return data

    def decrypt(self, data: str) -> str:
        """Decrypt data using Fernet."""
        if self._fernet:
            return self._fernet.decrypt(data.encode()).decode()
        return data

    # ── Full Authentication Flow ──

    async def authenticate(self, password: str, totp_code: str,
                           device_id: str, ip_address: str) -> dict:
        """
        Full authentication: password + TOTP + device check.
        Returns {"success": bool, "token": str, "error": str}
        """
        # Rate limiting
        if not self.check_rate_limit(ip_address):
            await audit_logger.log(
                "login", {"reason": "rate_limited"},
                user_id=device_id, ip_address=ip_address,
                success=False, risk_level="high"
            )
            return {"success": False, "error": "Rate limited. Try again later."}

        # Password check
        if not self.verify_password(password):
            self.record_failed_attempt(ip_address)
            await audit_logger.log(
                "login", {"reason": "invalid_password"},
                user_id=device_id, ip_address=ip_address,
                success=False, risk_level="high"
            )
            return {"success": False, "error": "Invalid credentials."}

        # TOTP check
        if Config.TOTP_SECRET and not self.verify_totp(totp_code):
            self.record_failed_attempt(ip_address)
            await audit_logger.log(
                "login", {"reason": "invalid_totp"},
                user_id=device_id, ip_address=ip_address,
                success=False, risk_level="high"
            )
            return {"success": False, "error": "Invalid 2FA code."}

        # Device check
        if self._device_allowlist and device_id not in self._device_allowlist:
            await audit_logger.log(
                "login", {"reason": "device_not_allowed"},
                user_id=device_id, ip_address=ip_address,
                success=False, risk_level="critical"
            )
            return {"success": False, "error": "Device not authorized."}

        # Success!
        token = self.create_session(device_id, ip_address)
        await audit_logger.log(
            "login", {"reason": "success"},
            user_id=device_id, ip_address=ip_address,
            success=True, risk_level="low"
        )
        return {"success": True, "token": token}
