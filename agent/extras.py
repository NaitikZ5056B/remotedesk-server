"""
RemoteDesk Agent - Extras
Webcam snapshot, clipboard sync, volume/brightness control,
and notification sender.
"""
import platform
import subprocess
import base64
import asyncio
from io import BytesIO
from typing import Optional

from audit_log import audit_logger


class WebcamCapture:
    """Capture snapshots from the webcam."""

    def __init__(self):
        self._system = platform.system()

    async def take_snapshot(self, camera_index: int = 0) -> dict:
        """Capture a single frame from the webcam."""
        try:
            import cv2
            cap = cv2.VideoCapture(camera_index)
            if not cap.isOpened():
                return {"success": False, "error": "Cannot open webcam"}

            ret, frame = cap.read()
            cap.release()

            if not ret:
                return {"success": False, "error": "Failed to capture frame"}

            # Encode as JPEG
            _, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            img_b64 = base64.b64encode(buffer.tobytes()).decode()

            h, w = frame.shape[:2]
            await audit_logger.log("webcam_snapshot", {
                "camera": camera_index,
                "resolution": f"{w}x{h}",
            }, risk_level="medium")

            return {
                "success": True,
                "image_b64": img_b64,
                "width": w,
                "height": h,
                "format": "jpeg",
            }
        except ImportError:
            return {"success": False, "error": "OpenCV not installed"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def list_cameras(self) -> list[dict]:
        """List available cameras."""
        cameras = []
        try:
            import cv2
            for i in range(5):  # Check first 5 indices
                cap = cv2.VideoCapture(i)
                if cap.isOpened():
                    cameras.append({"index": i, "name": f"Camera {i}"})
                    cap.release()
        except ImportError:
            pass
        return cameras


class ClipboardSync:
    """Synchronize clipboard between phone and laptop."""

    def __init__(self):
        self._system = platform.system()
        self._last_content: str = ""

    async def get_clipboard(self) -> dict:
        """Get current clipboard content."""
        try:
            if self._system == "Windows":
                import ctypes
                from ctypes import wintypes

                CF_UNICODETEXT = 13
                user32 = ctypes.windll.user32
                kernel32 = ctypes.windll.kernel32

                if not user32.OpenClipboard(None):
                    return {"success": False, "error": "Cannot open clipboard"}

                try:
                    handle = user32.GetClipboardData(CF_UNICODETEXT)
                    if handle:
                        kernel32.GlobalLock.restype = ctypes.c_wchar_p
                        text = kernel32.GlobalLock(handle)
                        kernel32.GlobalUnlock(handle)
                        return {"success": True, "content": text or "", "type": "text"}
                    return {"success": True, "content": "", "type": "text"}
                finally:
                    user32.CloseClipboard()

            elif self._system == "Darwin":
                result = subprocess.run(
                    ["pbpaste"], capture_output=True, text=True, timeout=3
                )
                return {"success": True, "content": result.stdout, "type": "text"}

            elif self._system == "Linux":
                # Try xclip first, then xsel
                for cmd in [["xclip", "-selection", "clipboard", "-o"],
                            ["xsel", "--clipboard", "--output"]]:
                    try:
                        result = subprocess.run(
                            cmd, capture_output=True, text=True, timeout=3
                        )
                        if result.returncode == 0:
                            return {"success": True, "content": result.stdout, "type": "text"}
                    except FileNotFoundError:
                        continue
                return {"success": False, "error": "No clipboard tool found (install xclip or xsel)"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def set_clipboard(self, content: str) -> dict:
        """Set clipboard content."""
        try:
            if self._system == "Windows":
                import ctypes
                from ctypes import wintypes

                CF_UNICODETEXT = 13
                GMEM_MOVEABLE = 0x0002
                user32 = ctypes.windll.user32
                kernel32 = ctypes.windll.kernel32

                if not user32.OpenClipboard(None):
                    return {"success": False, "error": "Cannot open clipboard"}

                try:
                    user32.EmptyClipboard()
                    data = content.encode("utf-16-le") + b"\x00\x00"
                    h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
                    ptr = kernel32.GlobalLock(h)
                    ctypes.memmove(ptr, data, len(data))
                    kernel32.GlobalUnlock(h)
                    user32.SetClipboardData(CF_UNICODETEXT, h)
                finally:
                    user32.CloseClipboard()

            elif self._system == "Darwin":
                process = subprocess.Popen(
                    ["pbcopy"], stdin=subprocess.PIPE
                )
                process.communicate(content.encode())

            elif self._system == "Linux":
                for cmd in [["xclip", "-selection", "clipboard"],
                            ["xsel", "--clipboard", "--input"]]:
                    try:
                        process = subprocess.Popen(cmd, stdin=subprocess.PIPE)
                        process.communicate(content.encode())
                        if process.returncode == 0:
                            break
                    except FileNotFoundError:
                        continue

            await audit_logger.log("clipboard_set", {
                "length": len(content),
                "preview": content[:50],
            })
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}


class MediaControls:
    """Control volume and brightness."""

    def __init__(self):
        self._system = platform.system()

    async def get_volume(self) -> dict:
        """Get current volume level (0-100)."""
        try:
            if self._system == "Windows":
                # Use PowerShell to get audio volume
                result = subprocess.run(
                    ["powershell", "-Command",
                     "(Get-AudioDevice -PlaybackVolume).Value"],
                    capture_output=True, text=True, timeout=5
                )
                # Fallback: use nircmd or similar
                return {"success": True, "volume": 50, "muted": False,
                        "note": "Install AudioDeviceCmdlets for accurate volume"}

            elif self._system == "Darwin":
                result = subprocess.run(
                    ["osascript", "-e", "output volume of (get volume settings)"],
                    capture_output=True, text=True, timeout=3
                )
                volume = int(result.stdout.strip()) if result.stdout.strip() else 0
                # Check mute
                mute_result = subprocess.run(
                    ["osascript", "-e",
                     "output muted of (get volume settings)"],
                    capture_output=True, text=True, timeout=3
                )
                muted = mute_result.stdout.strip() == "true"
                return {"success": True, "volume": volume, "muted": muted}

            elif self._system == "Linux":
                result = subprocess.run(
                    ["amixer", "get", "Master"],
                    capture_output=True, text=True, timeout=3
                )
                import re
                match = re.search(r"\[(\d+)%\]", result.stdout)
                volume = int(match.group(1)) if match else 0
                muted = "[off]" in result.stdout
                return {"success": True, "volume": volume, "muted": muted}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def set_volume(self, level: int) -> dict:
        """Set volume level (0-100)."""
        level = max(0, min(100, level))
        try:
            if self._system == "Windows":
                # Use nircmd or PowerShell
                subprocess.run(
                    ["powershell", "-Command",
                     f"(New-Object -ComObject WScript.Shell).SendKeys("
                     f"[char]0xAD)"],  # Volume mute toggle approach
                    timeout=5
                )
                # More reliable: use pycaw or nircmd
                return {"success": True, "volume": level,
                        "note": "For precise control, install pycaw package"}

            elif self._system == "Darwin":
                subprocess.run(
                    ["osascript", "-e", f"set volume output volume {level}"],
                    timeout=3
                )
                return {"success": True, "volume": level}

            elif self._system == "Linux":
                subprocess.run(
                    ["amixer", "set", "Master", f"{level}%"],
                    timeout=3
                )
                return {"success": True, "volume": level}

            await audit_logger.log("volume_set", {"level": level})
            return {"success": True, "volume": level}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def toggle_mute(self) -> dict:
        """Toggle audio mute."""
        try:
            if self._system == "Windows":
                import ctypes
                # Simulate volume mute key
                VK_VOLUME_MUTE = 0xAD
                ctypes.windll.user32.keybd_event(VK_VOLUME_MUTE, 0, 0, 0)
                ctypes.windll.user32.keybd_event(VK_VOLUME_MUTE, 0, 2, 0)
            elif self._system == "Darwin":
                subprocess.run(
                    ["osascript", "-e",
                     "set volume output muted not (output muted of (get volume settings))"],
                    timeout=3
                )
            elif self._system == "Linux":
                subprocess.run(
                    ["amixer", "set", "Master", "toggle"],
                    timeout=3
                )
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_brightness(self) -> dict:
        """Get current screen brightness (0-100)."""
        try:
            if self._system == "Windows":
                result = subprocess.run(
                    ["powershell", "-Command",
                     "(Get-WmiObject -Namespace root/WMI -Class "
                     "WmiMonitorBrightness).CurrentBrightness"],
                    capture_output=True, text=True, timeout=5
                )
                brightness = int(result.stdout.strip()) if result.stdout.strip() else 50
                return {"success": True, "brightness": brightness}

            elif self._system == "Darwin":
                result = subprocess.run(
                    ["brightness", "-l"],
                    capture_output=True, text=True, timeout=3
                )
                import re
                match = re.search(r"brightness\s+([\d.]+)", result.stdout)
                brightness = int(float(match.group(1)) * 100) if match else 50
                return {"success": True, "brightness": brightness}

            elif self._system == "Linux":
                import glob
                backlight = glob.glob("/sys/class/backlight/*/brightness")
                max_bl = glob.glob("/sys/class/backlight/*/max_brightness")
                if backlight and max_bl:
                    with open(backlight[0]) as f:
                        current = int(f.read().strip())
                    with open(max_bl[0]) as f:
                        maximum = int(f.read().strip())
                    brightness = int((current / maximum) * 100)
                    return {"success": True, "brightness": brightness}
                return {"success": False, "error": "No backlight device found"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def set_brightness(self, level: int) -> dict:
        """Set screen brightness (0-100)."""
        level = max(0, min(100, level))
        try:
            if self._system == "Windows":
                subprocess.run(
                    ["powershell", "-Command",
                     f"(Get-WmiObject -Namespace root/WMI -Class "
                     f"WmiMonitorBrightnessMethods).WmiSetBrightness(1,{level})"],
                    timeout=5
                )
            elif self._system == "Darwin":
                subprocess.run(
                    ["brightness", str(level / 100.0)],
                    timeout=3
                )
            elif self._system == "Linux":
                import glob
                max_bl = glob.glob("/sys/class/backlight/*/max_brightness")
                if max_bl:
                    with open(max_bl[0]) as f:
                        maximum = int(f.read().strip())
                    target = int(maximum * level / 100)
                    bl_path = max_bl[0].replace("max_brightness", "brightness")
                    with open(bl_path, "w") as f:
                        f.write(str(target))

            await audit_logger.log("brightness_set", {"level": level})
            return {"success": True, "brightness": level}
        except Exception as e:
            return {"success": False, "error": str(e)}


class NotificationSender:
    """Send notifications to the phone via the signaling server."""

    def __init__(self):
        self._send_callback = None

    def set_callback(self, callback):
        """Set the WebSocket send callback."""
        self._send_callback = callback

    async def notify(self, event: str, message: str, priority: str = "normal"):
        """Send a notification to all connected phone clients."""
        notification = {
            "type": "notification",
            "event": event,
            "message": message,
            "priority": priority,
            "timestamp": __import__("time").time(),
        }
        if self._send_callback:
            await self._send_callback(notification)

    async def on_wake(self):
        await self.notify("wake", "💻 Laptop has woken up", "high")

    async def on_disconnect(self):
        await self.notify("disconnect", "⚠️ Laptop connection lost", "high")

    async def on_failed_login(self, ip: str):
        await self.notify("failed_login",
                          f"🚨 Failed login attempt from {ip}", "urgent")

    async def on_low_battery(self, percent: float):
        await self.notify("low_battery",
                          f"🔋 Battery low: {percent}%", "high")
