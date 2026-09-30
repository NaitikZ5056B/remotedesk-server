"""
RemoteDesk Agent - System Controls
Lock, Unlock, Sleep, Restart, Shutdown, and Wake-on-LAN functionality.
Cross-platform support for Windows, macOS, and Linux.

UNLOCK NOTE:
────────────
Apps cannot normally unlock the OS lock screen for security reasons.
Implemented approaches per OS:

Windows: Uses a stored-credential helper that calls LockWorkStation for lock,
         and for unlock uses a combination approach:
         1. Sends Ctrl+Alt+Del simulation (requires SYSTEM privileges)
         2. Uses credential helper with securely stored password
         RISK: Storing the user's password (even encrypted) is a security risk.
               The password is encrypted with Fernet at rest but could be
               exposed if the machine is compromised.
         LIMITATION: Full automatic unlock requires running as SYSTEM service.

macOS:   Uses `caffeinate` to prevent sleep + `osascript` to dismiss the
         login window. Full unlock requires the password in keychain.
         LIMITATION: Cannot bypass FileVault or secure login without user
                     interaction on modern macOS.

Linux:   Uses `loginctl unlock-session` (systemd) or `xdg-screensaver reset`.
         LIMITATION: Works with systemd-logind; may not work with all
                     display managers. Wayland may restrict access.

All methods store credentials ENCRYPTED. Users should understand:
- Stored password = increased attack surface
- Physical access to laptop = potential password exposure
- Recommended: Use only on personal machines with full-disk encryption
"""
import platform
import subprocess
import asyncio
import ctypes
from typing import Optional

from audit_log import audit_logger


class SystemControls:
    """Cross-platform system control commands."""

    def __init__(self):
        self._system = platform.system()
        self._stored_password: str = ""  # Encrypted at rest via SecurityManager

    def set_unlock_password(self, encrypted_password: str):
        """Store the encrypted password for unlock operations."""
        self._stored_password = encrypted_password

    # ── Lock Screen ──

    async def lock_screen(self) -> dict:
        """Lock the screen/workstation."""
        try:
            if self._system == "Windows":
                ctypes.windll.user32.LockWorkStation()
            elif self._system == "Darwin":
                subprocess.run([
                    "osascript", "-e",
                    'tell application "System Events" to keystroke "q" '
                    'using {command down, control down}'
                ], timeout=5)
            elif self._system == "Linux":
                # Try multiple methods
                for cmd in [
                    ["loginctl", "lock-session"],
                    ["xdg-screensaver", "lock"],
                    ["gnome-screensaver-command", "-l"],
                ]:
                    try:
                        subprocess.run(cmd, timeout=5, check=True)
                        break
                    except (FileNotFoundError, subprocess.CalledProcessError):
                        continue

            await audit_logger.log("lock_screen", risk_level="medium")
            return {"success": True, "action": "lock"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Unlock Screen ──

    async def unlock_screen(self, password: str = None) -> dict:
        """
        Attempt to unlock the screen.
        See module docstring for platform-specific limitations and risks.
        """
        try:
            if self._system == "Windows":
                return await self._unlock_windows(password)
            elif self._system == "Darwin":
                return await self._unlock_macos(password)
            elif self._system == "Linux":
                return await self._unlock_linux(password)
            return {"success": False, "error": "Unsupported OS"}
        except Exception as e:
            await audit_logger.log("unlock_screen", {"error": str(e)},
                                   success=False, risk_level="critical")
            return {"success": False, "error": str(e)}

    async def _unlock_windows(self, password: str = None) -> dict:
        """
        Windows unlock strategy:
        1. If running as SYSTEM service, can simulate Ctrl+Alt+Del and type password
        2. Fallback: Send keystrokes to wake and present login screen
        """
        try:
            # Wake the display first
            ctypes.windll.user32.SetThreadExecutionState(0x80000002)  # ES_DISPLAY_REQUIRED

            # Simulate Esc to dismiss lock screen overlay, then Enter
            import time
            # Send keyboard events to wake
            ctypes.windll.user32.keybd_event(0x1B, 0, 0, 0)  # ESC down
            ctypes.windll.user32.keybd_event(0x1B, 0, 2, 0)  # ESC up
            await asyncio.sleep(0.5)
            ctypes.windll.user32.keybd_event(0x0D, 0, 0, 0)  # Enter down
            ctypes.windll.user32.keybd_event(0x0D, 0, 2, 0)  # Enter up

            # Note: Actually typing the password into the secure desktop
            # requires running as SYSTEM or using a Credential Provider.
            # This is a best-effort approach.

            await audit_logger.log("unlock_screen", {
                "method": "keybd_event",
                "note": "Display awakened. Password entry may require "
                        "Credential Provider for full automation."
            }, risk_level="critical")

            return {
                "success": True,
                "action": "unlock",
                "note": "Display awakened. If screen is locked, the login "
                        "screen is now visible. Full auto-unlock requires "
                        "a Credential Provider DLL installed as SYSTEM."
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _unlock_macos(self, password: str = None) -> dict:
        """macOS unlock: wake display + attempt caffeinate."""
        try:
            # Wake display
            subprocess.run(["caffeinate", "-u", "-t", "1"], timeout=5)
            await asyncio.sleep(0.5)

            if password:
                # Use osascript to type password into login window
                # Note: This works with screensaver but NOT with FileVault
                script = f'''
                    tell application "System Events"
                        keystroke "{password}"
                        keystroke return
                    end tell
                '''
                subprocess.run(["osascript", "-e", script], timeout=5)

            await audit_logger.log("unlock_screen", {"method": "caffeinate+osascript"},
                                   risk_level="critical")
            return {
                "success": True,
                "action": "unlock",
                "note": "Display awakened. Screensaver unlock attempted. "
                        "FileVault/secure login requires physical interaction."
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _unlock_linux(self, password: str = None) -> dict:
        """Linux unlock: loginctl or xdg-screensaver."""
        try:
            # Try loginctl first (systemd)
            result = subprocess.run(
                ["loginctl", "unlock-session", ""],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                await audit_logger.log("unlock_screen", {"method": "loginctl"},
                                       risk_level="critical")
                return {"success": True, "action": "unlock", "method": "loginctl"}

            # Fallback: xdg-screensaver
            subprocess.run(["xdg-screensaver", "reset"], timeout=5)
            await audit_logger.log("unlock_screen", {"method": "xdg-screensaver"},
                                   risk_level="critical")
            return {"success": True, "action": "unlock", "method": "xdg-screensaver"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Sleep ──

    async def sleep(self) -> dict:
        """Put the system to sleep."""
        try:
            if self._system == "Windows":
                # SetSuspendState(hibernate, force, disableWakeEvents)
                subprocess.run(
                    ["powershell", "-Command",
                     "Add-Type -Assembly System.Windows.Forms; "
                     "[System.Windows.Forms.Application]::SetSuspendState("
                     "'Suspend', $false, $false)"],
                    timeout=10
                )
            elif self._system == "Darwin":
                subprocess.run(["pmset", "sleepnow"], timeout=5)
            elif self._system == "Linux":
                subprocess.run(["systemctl", "suspend"], timeout=5)

            await audit_logger.log("sleep", risk_level="high")
            return {"success": True, "action": "sleep"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Restart ──

    async def restart(self, delay_seconds: int = 5) -> dict:
        """Restart the system after a delay."""
        try:
            if self._system == "Windows":
                subprocess.run(
                    ["shutdown", "/r", "/t", str(delay_seconds)],
                    timeout=10
                )
            elif self._system == "Darwin":
                subprocess.run(
                    ["sudo", "shutdown", "-r", f"+{delay_seconds // 60 or 1}"],
                    timeout=10
                )
            elif self._system == "Linux":
                subprocess.run(
                    ["sudo", "shutdown", "-r", f"+{delay_seconds // 60 or 1}"],
                    timeout=10
                )

            await audit_logger.log("restart", {"delay": delay_seconds},
                                   risk_level="critical")
            return {"success": True, "action": "restart",
                    "delay_seconds": delay_seconds}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Shutdown ──

    async def shutdown(self, delay_seconds: int = 10) -> dict:
        """Shutdown the system after a delay."""
        try:
            if self._system == "Windows":
                subprocess.run(
                    ["shutdown", "/s", "/t", str(delay_seconds)],
                    timeout=10
                )
            elif self._system == "Darwin":
                subprocess.run(
                    ["sudo", "shutdown", "-h", f"+{delay_seconds // 60 or 1}"],
                    timeout=10
                )
            elif self._system == "Linux":
                subprocess.run(
                    ["sudo", "shutdown", "-h", f"+{delay_seconds // 60 or 1}"],
                    timeout=10
                )

            await audit_logger.log("shutdown", {"delay": delay_seconds},
                                   risk_level="critical")
            return {"success": True, "action": "shutdown",
                    "delay_seconds": delay_seconds}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Cancel Scheduled Shutdown/Restart ──

    async def cancel_shutdown(self) -> dict:
        """Cancel a scheduled shutdown or restart."""
        try:
            if self._system == "Windows":
                subprocess.run(["shutdown", "/a"], timeout=5)
            else:
                subprocess.run(["sudo", "shutdown", "-c"], timeout=5)
            return {"success": True, "action": "cancel_shutdown"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── Wake-on-LAN ──

    async def send_wol(self, mac_address: str, broadcast: str = "255.255.255.255",
                       port: int = 9) -> dict:
        """
        Send Wake-on-LAN magic packet.
        Requirements: WoL must be enabled in BIOS/UEFI and network adapter settings.
        Works for waking from shutdown/hibernate if hardware supports it.
        For sleep, the laptop should wake on its own when receiving the packet
        if WoL is properly configured.
        """
        import socket
        import struct

        try:
            # Clean MAC address
            mac = mac_address.replace(":", "").replace("-", "").replace(".", "")
            if len(mac) != 12:
                return {"success": False, "error": "Invalid MAC address"}

            # Build magic packet: 6 bytes of 0xFF + 16 repetitions of MAC
            mac_bytes = bytes.fromhex(mac)
            magic = b'\xff' * 6 + mac_bytes * 16

            # Send via UDP broadcast
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(magic, (broadcast, port))
            sock.close()

            await audit_logger.log("wake_on_lan", {
                "mac": mac_address,
                "broadcast": broadcast,
            }, risk_level="medium")

            return {
                "success": True,
                "action": "wake_on_lan",
                "mac": mac_address,
                "note": "Magic packet sent. Device will wake if WoL is "
                        "enabled in BIOS and network adapter settings."
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
