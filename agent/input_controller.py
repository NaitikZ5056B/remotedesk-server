"""
RemoteDesk Agent - Input Controller
Handles remote mouse movements, clicks, keyboard input, scrolling,
and touch gesture mapping.
"""
import platform
import asyncio
from typing import Optional

from pynput.mouse import Button, Controller as MouseController
from pynput.keyboard import Key, Controller as KeyboardController

from audit_log import audit_logger


# Map of special key names to pynput Key objects
SPECIAL_KEYS = {
    "enter": Key.enter,
    "return": Key.enter,
    "tab": Key.tab,
    "space": Key.space,
    "backspace": Key.backspace,
    "delete": Key.delete,
    "escape": Key.esc,
    "esc": Key.esc,
    "shift": Key.shift,
    "shift_l": Key.shift_l,
    "shift_r": Key.shift_r,
    "ctrl": Key.ctrl_l,
    "ctrl_l": Key.ctrl_l,
    "ctrl_r": Key.ctrl_r,
    "alt": Key.alt_l,
    "alt_l": Key.alt_l,
    "alt_r": Key.alt_r,
    "cmd": Key.cmd if hasattr(Key, "cmd") else Key.ctrl_l,
    "meta": Key.cmd if hasattr(Key, "cmd") else Key.ctrl_l,
    "super": Key.cmd if hasattr(Key, "cmd") else Key.ctrl_l,
    "win": Key.cmd if hasattr(Key, "cmd") else Key.ctrl_l,
    "caps_lock": Key.caps_lock,
    "num_lock": Key.num_lock,
    "scroll_lock": Key.scroll_lock,
    "print_screen": Key.print_screen,
    "pause": Key.pause,
    "insert": Key.insert,
    "home": Key.home,
    "end": Key.end,
    "page_up": Key.page_up,
    "page_down": Key.page_down,
    "up": Key.up,
    "down": Key.down,
    "left": Key.left,
    "right": Key.right,
    "f1": Key.f1, "f2": Key.f2, "f3": Key.f3, "f4": Key.f4,
    "f5": Key.f5, "f6": Key.f6, "f7": Key.f7, "f8": Key.f8,
    "f9": Key.f9, "f10": Key.f10, "f11": Key.f11, "f12": Key.f12,
    "menu": Key.menu if hasattr(Key, "menu") else None,
}

# Mouse button mapping
MOUSE_BUTTONS = {
    "left": Button.left,
    "right": Button.right,
    "middle": Button.middle,
}


class InputController:
    """Handles all remote input: mouse, keyboard, and gesture interpretation."""

    def __init__(self, screen_width: int = 1920, screen_height: int = 1080):
        self._mouse = MouseController()
        self._keyboard = KeyboardController()
        self._screen_width = screen_width
        self._screen_height = screen_height
        self._pressed_keys: set = set()
        self._system = platform.system()

    def update_screen_size(self, width: int, height: int):
        """Update the reference screen dimensions (for coordinate mapping)."""
        self._screen_width = width
        self._screen_height = height

    # ── Mouse Actions ──

    async def mouse_move(self, x: int, y: int, relative: bool = False):
        """Move the mouse cursor."""
        if relative:
            self._mouse.move(x, y)
        else:
            # Clamp coordinates
            x = max(0, min(x, self._screen_width))
            y = max(0, min(y, self._screen_height))
            self._mouse.position = (x, y)

    async def mouse_click(self, button: str = "left", count: int = 1,
                          x: int = None, y: int = None):
        """Click the mouse. Optionally move to position first."""
        if x is not None and y is not None:
            await self.mouse_move(x, y)

        btn = MOUSE_BUTTONS.get(button, Button.left)
        self._mouse.click(btn, count)

        await audit_logger.log("mouse_click", {
            "button": button, "count": count,
            "x": x, "y": y
        })

    async def mouse_double_click(self, x: int = None, y: int = None):
        """Double-click at position."""
        await self.mouse_click("left", 2, x, y)

    async def mouse_right_click(self, x: int = None, y: int = None):
        """Right-click at position."""
        await self.mouse_click("right", 1, x, y)

    async def mouse_scroll(self, dx: int = 0, dy: int = 0):
        """Scroll the mouse wheel. dy>0 = scroll up, dy<0 = scroll down."""
        self._mouse.scroll(dx, dy)

    async def mouse_drag(self, start_x: int, start_y: int,
                         end_x: int, end_y: int, button: str = "left"):
        """Drag from start to end position."""
        btn = MOUSE_BUTTONS.get(button, Button.left)
        self._mouse.position = (start_x, start_y)
        self._mouse.press(btn)
        # Smooth drag in steps
        steps = 20
        for i in range(1, steps + 1):
            ix = start_x + (end_x - start_x) * i // steps
            iy = start_y + (end_y - start_y) * i // steps
            self._mouse.position = (ix, iy)
            await asyncio.sleep(0.01)
        self._mouse.release(btn)

        await audit_logger.log("mouse_drag", {
            "start": [start_x, start_y],
            "end": [end_x, end_y],
            "button": button,
        })

    async def mouse_down(self, button: str = "left"):
        """Press and hold mouse button."""
        btn = MOUSE_BUTTONS.get(button, Button.left)
        self._mouse.press(btn)

    async def mouse_up(self, button: str = "left"):
        """Release mouse button."""
        btn = MOUSE_BUTTONS.get(button, Button.left)
        self._mouse.release(btn)

    # ── Keyboard Actions ──

    async def key_press(self, key: str):
        """Press and release a single key."""
        k = self._resolve_key(key)
        if k:
            self._keyboard.press(k)
            self._keyboard.release(k)

    async def key_down(self, key: str):
        """Press and hold a key."""
        k = self._resolve_key(key)
        if k:
            self._keyboard.press(k)
            self._pressed_keys.add(key)

    async def key_up(self, key: str):
        """Release a held key."""
        k = self._resolve_key(key)
        if k:
            self._keyboard.release(k)
            self._pressed_keys.discard(key)

    async def type_text(self, text: str, interval: float = 0.02):
        """Type a string of text character by character."""
        for char in text:
            self._keyboard.type(char)
            if interval > 0:
                await asyncio.sleep(interval)

        await audit_logger.log("type_text", {
            "length": len(text),
            "preview": text[:20] + "..." if len(text) > 20 else text,
        })

    async def key_combo(self, keys: list[str]):
        """
        Press a keyboard shortcut (e.g., ["ctrl", "c"]).
        All keys are pressed in order, then released in reverse.
        """
        resolved = []
        for key in keys:
            k = self._resolve_key(key)
            if k:
                resolved.append(k)

        # Press all keys
        for k in resolved:
            self._keyboard.press(k)

        # Release in reverse order
        for k in reversed(resolved):
            self._keyboard.release(k)

        await audit_logger.log("key_combo", {
            "keys": keys,
        })

    async def release_all_keys(self):
        """Release all currently held keys (safety measure)."""
        for key in list(self._pressed_keys):
            await self.key_up(key)
        self._pressed_keys.clear()

    # ── Touch Gesture Mapping ──

    async def handle_gesture(self, gesture: dict):
        """
        Map phone touch gestures to mouse/keyboard actions.
        gesture = {
            "type": "tap|double_tap|long_press|scroll|pinch|drag",
            "x": normalized_x (0-1),
            "y": normalized_y (0-1),
            "dx": delta_x (for scroll/drag),
            "dy": delta_y (for scroll/drag),
            "scale": pinch_scale,
        }
        """
        gtype = gesture.get("type", "")
        # Convert normalized coordinates to screen coordinates
        x = int(gesture.get("x", 0) * self._screen_width)
        y = int(gesture.get("y", 0) * self._screen_height)

        if gtype == "tap":
            await self.mouse_click("left", 1, x, y)

        elif gtype == "double_tap":
            await self.mouse_double_click(x, y)

        elif gtype == "long_press":
            await self.mouse_right_click(x, y)

        elif gtype == "scroll":
            dx = int(gesture.get("dx", 0) * 5)
            dy = int(gesture.get("dy", 0) * 5)
            await self.mouse_move(x, y)
            await self.mouse_scroll(dx, dy)

        elif gtype == "two_finger_scroll":
            dy = int(gesture.get("dy", 0) * 3)
            await self.mouse_scroll(0, dy)

        elif gtype == "drag":
            end_x = int(gesture.get("end_x", 0) * self._screen_width)
            end_y = int(gesture.get("end_y", 0) * self._screen_height)
            await self.mouse_drag(x, y, end_x, end_y)

        elif gtype == "pinch":
            # Map pinch to Ctrl+scroll (zoom)
            scale = gesture.get("scale", 1.0)
            self._keyboard.press(Key.ctrl_l)
            if scale > 1:
                await self.mouse_scroll(0, 3)
            else:
                await self.mouse_scroll(0, -3)
            self._keyboard.release(Key.ctrl_l)

        elif gtype == "three_finger_tap":
            # Map to middle click
            await self.mouse_click("middle", 1, x, y)

        elif gtype == "move":
            await self.mouse_move(x, y)

    def _resolve_key(self, key: str):
        """Resolve a key string to a pynput Key or character."""
        key_lower = key.lower()
        if key_lower in SPECIAL_KEYS:
            resolved = SPECIAL_KEYS[key_lower]
            if resolved is not None:
                return resolved
        if len(key) == 1:
            return key
        return None

    def get_mouse_position(self) -> dict:
        """Get current mouse position."""
        x, y = self._mouse.position
        return {"x": x, "y": y}
