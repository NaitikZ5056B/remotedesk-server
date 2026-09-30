"""
RemoteDesk Agent - Terminal Handler
Provides a remote shell (PTY) accessible from the phone.
Supports Windows (cmd/PowerShell), macOS (zsh/bash), and Linux (bash).
"""
import os
import sys
import platform
import asyncio
import subprocess
import signal
from typing import Optional, Callable
from datetime import datetime

from audit_log import audit_logger


class TerminalSession:
    """Manages a single terminal/shell session."""

    def __init__(self, session_id: str, shell: str = None):
        self.session_id = session_id
        self._system = platform.system()
        self._shell = shell or self._default_shell()
        self._process: Optional[asyncio.subprocess.Process] = None
        self._output_callback: Optional[Callable] = None
        self._running = False
        self._history: list[dict] = []
        self._created_at = datetime.utcnow().isoformat()

    def _default_shell(self) -> str:
        """Determine the default shell for the OS."""
        if self._system == "Windows":
            return "powershell.exe"
        elif self._system == "Darwin":
            return os.environ.get("SHELL", "/bin/zsh")
        else:
            return os.environ.get("SHELL", "/bin/bash")

    async def start(self, output_callback: Callable):
        """Start the shell process."""
        self._output_callback = output_callback
        self._running = True

        try:
            if self._system == "Windows":
                self._process = await asyncio.create_subprocess_exec(
                    self._shell,
                    "-NoLogo", "-NoProfile",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW
                    if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
                )
            else:
                self._process = await asyncio.create_subprocess_exec(
                    self._shell,
                    "-i",  # Interactive mode
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env={**os.environ, "TERM": "xterm-256color"},
                )

            # Start reading output
            asyncio.create_task(self._read_stdout())
            asyncio.create_task(self._read_stderr())

            await audit_logger.log("terminal_start", {
                "session_id": self.session_id,
                "shell": self._shell,
            }, risk_level="high")

            return True
        except Exception as e:
            print(f"[TERMINAL] Failed to start: {e}")
            return False

    async def _read_stdout(self):
        """Read stdout from the process and send to callback."""
        try:
            while self._running and self._process and self._process.stdout:
                data = await self._process.stdout.read(4096)
                if not data:
                    break
                text = data.decode("utf-8", errors="replace")
                if self._output_callback:
                    await self._output_callback({
                        "session_id": self.session_id,
                        "type": "stdout",
                        "data": text,
                    })
        except Exception as e:
            if self._running:
                print(f"[TERMINAL] stdout read error: {e}")

    async def _read_stderr(self):
        """Read stderr from the process and send to callback."""
        try:
            while self._running and self._process and self._process.stderr:
                data = await self._process.stderr.read(4096)
                if not data:
                    break
                text = data.decode("utf-8", errors="replace")
                if self._output_callback:
                    await self._output_callback({
                        "session_id": self.session_id,
                        "type": "stderr",
                        "data": text,
                    })
        except Exception as e:
            if self._running:
                print(f"[TERMINAL] stderr read error: {e}")

    async def send_input(self, command: str):
        """Send input/command to the shell."""
        if not self._process or not self._process.stdin:
            return False

        try:
            # Add newline if not present
            if not command.endswith("\n"):
                command += "\n"

            self._process.stdin.write(command.encode("utf-8"))
            await self._process.stdin.drain()

            self._history.append({
                "timestamp": datetime.utcnow().isoformat(),
                "command": command.strip(),
            })

            await audit_logger.log("terminal_input", {
                "session_id": self.session_id,
                "command": command.strip()[:200],  # Truncate for audit
            }, risk_level="high")

            return True
        except Exception as e:
            print(f"[TERMINAL] send error: {e}")
            return False

    async def send_signal(self, sig: str):
        """Send a signal to the process (e.g., SIGINT for Ctrl+C)."""
        if not self._process:
            return False

        try:
            if sig == "SIGINT" or sig == "ctrl_c":
                if self._system == "Windows":
                    # Windows: send Ctrl+C
                    self._process.send_signal(signal.CTRL_C_EVENT)
                else:
                    self._process.send_signal(signal.SIGINT)
            elif sig == "SIGTERM":
                self._process.terminate()
            elif sig == "SIGKILL":
                self._process.kill()
            return True
        except Exception as e:
            print(f"[TERMINAL] signal error: {e}")
            return False

    async def resize(self, rows: int, cols: int):
        """Resize the terminal (Unix PTY only, no-op on Windows subprocess)."""
        # Note: For full PTY support on Unix, consider using the `pty` module
        pass

    async def stop(self):
        """Stop the shell session."""
        self._running = False
        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()
            except Exception:
                pass

        await audit_logger.log("terminal_stop", {
            "session_id": self.session_id,
        }, risk_level="medium")

    @property
    def is_running(self) -> bool:
        return self._running and self._process is not None and self._process.returncode is None

    def get_info(self) -> dict:
        return {
            "session_id": self.session_id,
            "shell": self._shell,
            "running": self.is_running,
            "created_at": self._created_at,
            "history_count": len(self._history),
        }


class TerminalManager:
    """Manages multiple terminal sessions."""

    def __init__(self, max_sessions: int = 5):
        self._sessions: dict[str, TerminalSession] = {}
        self._max_sessions = max_sessions
        self._counter = 0

    async def create_session(self, output_callback: Callable,
                              shell: str = None) -> dict:
        """Create a new terminal session."""
        if len(self._sessions) >= self._max_sessions:
            return {"success": False, "error": "Max terminal sessions reached"}

        self._counter += 1
        session_id = f"term-{self._counter}"
        session = TerminalSession(session_id, shell)

        if await session.start(output_callback):
            self._sessions[session_id] = session
            return {
                "success": True,
                "session_id": session_id,
                "shell": session._shell,
            }
        else:
            return {"success": False, "error": "Failed to start shell"}

    async def send_input(self, session_id: str, command: str) -> bool:
        """Send input to a session."""
        session = self._sessions.get(session_id)
        if session:
            return await session.send_input(command)
        return False

    async def send_signal(self, session_id: str, sig: str) -> bool:
        """Send signal to a session."""
        session = self._sessions.get(session_id)
        if session:
            return await session.send_signal(sig)
        return False

    async def close_session(self, session_id: str) -> bool:
        """Close a terminal session."""
        session = self._sessions.pop(session_id, None)
        if session:
            await session.stop()
            return True
        return False

    async def close_all(self):
        """Close all terminal sessions."""
        for session in self._sessions.values():
            await session.stop()
        self._sessions.clear()

    def list_sessions(self) -> list[dict]:
        """List all active sessions."""
        return [s.get_info() for s in self._sessions.values()]
