"""
RemoteDesk Agent - Audit Logger
Records every action taken through the remote control with timestamps,
IP addresses, and action details for security review.
"""
import json
import time
import asyncio
import aiofiles
from pathlib import Path
from datetime import datetime, timezone
from config import Config


class AuditLogger:
    """Thread-safe audit logger that writes to a JSON-lines file."""

    def __init__(self):
        self._log_path = Path(Config.AUDIT_LOG_PATH)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._buffer: list[dict] = []
        self._flush_interval = 5  # seconds
        self._max_buffer = 50

    async def log(self, action: str, details: dict = None, user_id: str = "",
                  ip_address: str = "", success: bool = True, risk_level: str = "low"):
        """
        Record an audit event.

        Args:
            action: The action performed (e.g., "mouse_click", "file_delete", "shutdown")
            details: Additional details about the action
            user_id: ID of the user/device performing the action
            ip_address: Source IP address
            success: Whether the action succeeded
            risk_level: "low", "medium", "high", "critical"
        """
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "epoch": time.time(),
            "action": action,
            "user_id": user_id,
            "ip_address": ip_address,
            "success": success,
            "risk_level": risk_level,
            "details": details or {},
        }

        async with self._lock:
            self._buffer.append(entry)
            if len(self._buffer) >= self._max_buffer:
                await self._flush()

    async def _flush(self):
        """Write buffered entries to disk."""
        if not self._buffer:
            return
        try:
            async with aiofiles.open(self._log_path, "a") as f:
                for entry in self._buffer:
                    await f.write(json.dumps(entry) + "\n")
            self._buffer.clear()
        except Exception as e:
            print(f"[AUDIT] Failed to flush log: {e}")

    async def flush(self):
        """Public flush method."""
        async with self._lock:
            await self._flush()

    async def start_periodic_flush(self):
        """Background task to flush logs periodically."""
        while True:
            await asyncio.sleep(self._flush_interval)
            await self.flush()

    async def get_recent(self, count: int = 100, action_filter: str = None) -> list[dict]:
        """Read recent audit log entries."""
        await self.flush()
        entries = []
        try:
            async with aiofiles.open(self._log_path, "r") as f:
                async for line in f:
                    line = line.strip()
                    if line:
                        entry = json.loads(line)
                        if action_filter and entry.get("action") != action_filter:
                            continue
                        entries.append(entry)
        except FileNotFoundError:
            pass
        return entries[-count:]

    async def get_failed_logins(self, since_hours: int = 24) -> list[dict]:
        """Get failed login attempts in the last N hours."""
        cutoff = time.time() - (since_hours * 3600)
        entries = await self.get_recent(count=1000, action_filter="login")
        return [e for e in entries if not e["success"] and e.get("epoch", 0) > cutoff]


# Singleton
audit_logger = AuditLogger()
