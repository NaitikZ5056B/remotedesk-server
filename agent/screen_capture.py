"""
RemoteDesk Agent - Screen Capture & WebRTC Streaming
Captures screen frames using mss, streams via WebRTC VideoStreamTrack.
Supports multi-monitor, adjustable FPS/quality, and fallback WebSocket streaming.
"""
import asyncio
import time
import fractions
from typing import Optional

import mss
import numpy as np
from PIL import Image
from av import VideoFrame

try:
    from aiortc import MediaStreamTrack
    HAS_WEBRTC = True
except ImportError:
    HAS_WEBRTC = False
    MediaStreamTrack = object  # Fallback

from config import Config


class ScreenCaptureTrack(MediaStreamTrack if HAS_WEBRTC else object):
    """
    WebRTC video track that captures the screen.
    Feeds screen frames into the WebRTC pipeline for low-latency streaming.
    """
    kind = "video"

    def __init__(self, monitor: int = None, fps: int = None, quality: int = None):
        if HAS_WEBRTC:
            super().__init__()
        self._sct = mss.mss()
        self._monitor = monitor or Config.DEFAULT_MONITOR
        self._fps = fps or Config.DEFAULT_FPS
        self._quality = quality or Config.DEFAULT_QUALITY
        self._running = True
        self._frame_count = 0
        self._start_time = time.time()
        self._last_frame_time = 0
        self._frame_interval = 1.0 / self._fps

    @property
    def monitors(self) -> list[dict]:
        """List available monitors with dimensions."""
        result = []
        for i, m in enumerate(self._sct.monitors):
            if i == 0:
                continue  # Skip combined monitor
            result.append({
                "index": i,
                "left": m["left"],
                "top": m["top"],
                "width": m["width"],
                "height": m["height"],
            })
        return result

    def set_monitor(self, index: int):
        """Switch to a different monitor."""
        if 1 <= index <= len(self._sct.monitors) - 1:
            self._monitor = index

    def set_fps(self, fps: int):
        """Adjust frame rate."""
        self._fps = max(Config.MIN_FPS, min(Config.MAX_FPS, fps))
        self._frame_interval = 1.0 / self._fps

    def set_quality(self, quality: int):
        """Adjust JPEG quality (10-100)."""
        self._quality = max(Config.MIN_QUALITY, min(Config.MAX_QUALITY, quality))

    def capture_frame(self) -> np.ndarray:
        """Capture a single frame as numpy array (BGRA)."""
        monitor = self._sct.monitors[self._monitor]
        img = self._sct.grab(monitor)
        return np.array(img)

    def capture_frame_jpeg(self) -> bytes:
        """Capture a frame and encode as JPEG bytes (for WebSocket fallback)."""
        frame = self.capture_frame()
        # Convert BGRA to RGB
        img = Image.fromarray(frame[:, :, :3][:, :, ::-1])
        from io import BytesIO
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=self._quality, optimize=True)
        return buf.getvalue()

    def capture_frame_webp(self) -> bytes:
        """Capture a frame and encode as WebP bytes (better compression)."""
        frame = self.capture_frame()
        img = Image.fromarray(frame[:, :, :3][:, :, ::-1])
        from io import BytesIO
        buf = BytesIO()
        img.save(buf, format="WEBP", quality=self._quality)
        return buf.getvalue()

    async def recv(self) -> VideoFrame:
        """WebRTC: called by aiortc to get the next video frame."""
        # Throttle to target FPS
        now = time.time()
        sleep_time = self._frame_interval - (now - self._last_frame_time)
        if sleep_time > 0:
            await asyncio.sleep(sleep_time)
        self._last_frame_time = time.time()

        # Capture screen
        frame_data = self.capture_frame()

        # Convert BGRA -> BGR for VideoFrame
        bgr = frame_data[:, :, :3]

        # Create VideoFrame
        frame = VideoFrame.from_ndarray(bgr, format="bgr24")

        # Set timestamps
        self._frame_count += 1
        frame.pts = self._frame_count
        frame.time_base = fractions.Fraction(1, self._fps)

        return frame

    def stop(self):
        """Stop capturing."""
        self._running = False
        if HAS_WEBRTC and hasattr(super(), 'stop'):
            super().stop()

    def get_stats(self) -> dict:
        """Return capture statistics."""
        elapsed = time.time() - self._start_time
        return {
            "frames_captured": self._frame_count,
            "elapsed_seconds": round(elapsed, 1),
            "actual_fps": round(self._frame_count / max(elapsed, 0.001), 1),
            "target_fps": self._fps,
            "quality": self._quality,
            "monitor": self._monitor,
            "monitors_available": len(self._sct.monitors) - 1,
        }


class ScreenStreamer:
    """
    Manages screen streaming via WebSocket fallback.
    Used when WebRTC is not available or as an initial connection method.
    """

    def __init__(self):
        self._capture = ScreenCaptureTrack()
        self._running = False
        self._clients: list = []

    async def start_streaming(self, send_callback):
        """
        Start streaming frames via a callback function.
        send_callback receives bytes of the encoded frame.
        """
        self._running = True
        while self._running:
            try:
                start = time.time()
                frame_bytes = self._capture.capture_frame_jpeg()

                await send_callback(frame_bytes)

                # Maintain FPS
                elapsed = time.time() - start
                sleep_time = self._capture._frame_interval - elapsed
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
            except Exception as e:
                print(f"[SCREEN] Streaming error: {e}")
                await asyncio.sleep(0.1)

    def stop_streaming(self):
        """Stop the stream."""
        self._running = False

    @property
    def capture(self) -> ScreenCaptureTrack:
        return self._capture
