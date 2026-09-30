"""
RemoteDesk Agent - System Monitor
Provides real-time CPU, RAM, disk, battery, network stats,
running processes (with kill capability), and active window title.
"""
import platform
import subprocess
import asyncio
from typing import Optional

import psutil


class SystemMonitor:
    """Collects system telemetry for the dashboard."""

    def __init__(self):
        self._system = platform.system()

    async def get_full_dashboard(self) -> dict:
        """Return complete system dashboard data."""
        return {
            "cpu": await self.get_cpu(),
            "memory": await self.get_memory(),
            "disk": await self.get_disk(),
            "battery": await self.get_battery(),
            "network": await self.get_network(),
            "system_info": self.get_system_info(),
            "active_window": await self.get_active_window(),
            "uptime": self.get_uptime(),
        }

    async def get_cpu(self) -> dict:
        """CPU usage and info."""
        cpu_percent = psutil.cpu_percent(interval=0.5)
        cpu_freq = psutil.cpu_freq()
        per_core = psutil.cpu_percent(percpu=True)
        return {
            "percent": cpu_percent,
            "per_core": per_core,
            "cores_physical": psutil.cpu_count(logical=False),
            "cores_logical": psutil.cpu_count(logical=True),
            "frequency_mhz": round(cpu_freq.current) if cpu_freq else None,
            "frequency_max_mhz": round(cpu_freq.max) if cpu_freq else None,
        }

    async def get_memory(self) -> dict:
        """RAM usage."""
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        return {
            "total_gb": round(mem.total / (1024 ** 3), 2),
            "used_gb": round(mem.used / (1024 ** 3), 2),
            "available_gb": round(mem.available / (1024 ** 3), 2),
            "percent": mem.percent,
            "swap_total_gb": round(swap.total / (1024 ** 3), 2),
            "swap_used_gb": round(swap.used / (1024 ** 3), 2),
            "swap_percent": swap.percent,
        }

    async def get_disk(self) -> list[dict]:
        """Disk usage for all partitions."""
        disks = []
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
                disks.append({
                    "device": part.device,
                    "mountpoint": part.mountpoint,
                    "fstype": part.fstype,
                    "total_gb": round(usage.total / (1024 ** 3), 2),
                    "used_gb": round(usage.used / (1024 ** 3), 2),
                    "free_gb": round(usage.free / (1024 ** 3), 2),
                    "percent": usage.percent,
                })
            except (PermissionError, OSError):
                continue
        return disks

    async def get_battery(self) -> dict | None:
        """Battery status. Returns None if no battery."""
        batt = psutil.sensors_battery()
        if not batt:
            return None
        return {
            "percent": round(batt.percent, 1),
            "power_plugged": batt.power_plugged,
            "seconds_left": batt.secsleft if batt.secsleft != psutil.POWER_TIME_UNLIMITED else -1,
            "time_left": self._format_seconds(batt.secsleft) if batt.secsleft > 0 else (
                "Charging" if batt.power_plugged else "Unknown"
            ),
        }

    async def get_network(self) -> dict:
        """Network stats and active connections."""
        net_io = psutil.net_io_counters()
        addrs = psutil.net_if_addrs()
        interfaces = {}
        for iface, addr_list in addrs.items():
            ips = []
            for addr in addr_list:
                if addr.family.name == "AF_INET":
                    ips.append(addr.address)
            if ips:
                interfaces[iface] = ips

        return {
            "bytes_sent": net_io.bytes_sent,
            "bytes_recv": net_io.bytes_recv,
            "bytes_sent_mb": round(net_io.bytes_sent / (1024 ** 2), 2),
            "bytes_recv_mb": round(net_io.bytes_recv / (1024 ** 2), 2),
            "packets_sent": net_io.packets_sent,
            "packets_recv": net_io.packets_recv,
            "interfaces": interfaces,
        }

    async def get_processes(self, sort_by: str = "cpu",
                            limit: int = 30) -> list[dict]:
        """List running processes sorted by CPU or memory usage."""
        procs = []
        for proc in psutil.process_iter(["pid", "name", "cpu_percent",
                                          "memory_percent", "status",
                                          "username", "create_time"]):
            try:
                info = proc.info
                procs.append({
                    "pid": info["pid"],
                    "name": info["name"],
                    "cpu_percent": info["cpu_percent"] or 0,
                    "memory_percent": round(info["memory_percent"] or 0, 1),
                    "status": info["status"],
                    "username": info["username"],
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        # Sort
        sort_key = "cpu_percent" if sort_by == "cpu" else "memory_percent"
        procs.sort(key=lambda p: p[sort_key], reverse=True)
        return procs[:limit]

    async def kill_process(self, pid: int, force: bool = False) -> dict:
        """Kill a process by PID."""
        try:
            proc = psutil.Process(pid)
            name = proc.name()
            if force:
                proc.kill()
            else:
                proc.terminate()
            return {"success": True, "pid": pid, "name": name}
        except psutil.NoSuchProcess:
            return {"success": False, "error": f"Process {pid} not found"}
        except psutil.AccessDenied:
            return {"success": False, "error": f"Access denied for PID {pid}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_active_window(self) -> dict:
        """Get the currently active/focused window title."""
        title = "Unknown"
        try:
            if self._system == "Windows":
                import ctypes
                hwnd = ctypes.windll.user32.GetForegroundWindow()
                length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(length + 1)
                ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value or "Desktop"
            elif self._system == "Darwin":
                result = subprocess.run(
                    ["osascript", "-e",
                     'tell application "System Events" to get name of first '
                     'application process whose frontmost is true'],
                    capture_output=True, text=True, timeout=3
                )
                title = result.stdout.strip() or "Unknown"
            elif self._system == "Linux":
                result = subprocess.run(
                    ["xdotool", "getactivewindow", "getwindowname"],
                    capture_output=True, text=True, timeout=3
                )
                title = result.stdout.strip() or "Unknown"
        except Exception:
            pass

        return {"title": title}

    def get_system_info(self) -> dict:
        """Static system information."""
        uname = platform.uname()
        return {
            "os": self._system,
            "os_version": uname.version,
            "os_release": uname.release,
            "machine": uname.machine,
            "hostname": uname.node,
            "processor": uname.processor or platform.processor(),
            "python_version": platform.python_version(),
        }

    def get_uptime(self) -> dict:
        """System uptime."""
        import time
        boot_time = psutil.boot_time()
        uptime_seconds = time.time() - boot_time
        return {
            "boot_time": boot_time,
            "uptime_seconds": int(uptime_seconds),
            "uptime_human": self._format_seconds(int(uptime_seconds)),
        }

    @staticmethod
    def _format_seconds(seconds: int) -> str:
        """Format seconds to human readable string."""
        if seconds < 0:
            return "Unknown"
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        minutes = (seconds % 3600) // 60
        parts = []
        if days:
            parts.append(f"{days}d")
        if hours:
            parts.append(f"{hours}h")
        parts.append(f"{minutes}m")
        return " ".join(parts)
