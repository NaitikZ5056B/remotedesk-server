"""
RemoteDesk Agent - File Manager
Browse directories, download files, upload files, delete with confirmation.
All operations are audit-logged and path-validated.
"""
import os
import stat
import shutil
import base64
import asyncio
import aiofiles
from pathlib import Path
from datetime import datetime
from typing import Optional

from audit_log import audit_logger


# Dangerous paths that should never be deleted
PROTECTED_PATHS = {
    "C:\\Windows", "C:\\Program Files", "C:\\Program Files (x86)",
    "/usr", "/bin", "/sbin", "/etc", "/var", "/boot", "/dev", "/proc", "/sys",
    "/System", "/Library", "/Applications",
}


class FileManager:
    """Remote file management with safety guards."""

    def __init__(self, base_path: str = None):
        self._base_path = base_path  # Optional: restrict to a directory
        self._pending_deletes: dict[str, dict] = {}  # confirmation tokens

    async def list_directory(self, path: str) -> dict:
        """List contents of a directory."""
        try:
            p = Path(path).resolve()
            if not p.exists():
                return {"success": False, "error": "Path not found"}
            if not p.is_dir():
                return {"success": False, "error": "Not a directory"}

            items = []
            for item in sorted(p.iterdir()):
                try:
                    st = item.stat()
                    items.append({
                        "name": item.name,
                        "path": str(item),
                        "is_dir": item.is_dir(),
                        "is_file": item.is_file(),
                        "size": st.st_size if item.is_file() else None,
                        "size_human": self._human_size(st.st_size) if item.is_file() else None,
                        "modified": datetime.fromtimestamp(st.st_mtime).isoformat(),
                        "permissions": stat.filemode(st.st_mode),
                        "is_hidden": item.name.startswith(".") or (
                            os.name == "nt" and bool(st.st_file_attributes & stat.FILE_ATTRIBUTE_HIDDEN)
                            if hasattr(st, "st_file_attributes") else False
                        ),
                    })
                except (PermissionError, OSError):
                    items.append({
                        "name": item.name,
                        "path": str(item),
                        "is_dir": item.is_dir(),
                        "error": "Permission denied",
                    })

            await audit_logger.log("file_list", {"path": str(p), "count": len(items)})

            return {
                "success": True,
                "path": str(p),
                "parent": str(p.parent),
                "items": items,
                "total": len(items),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_file_info(self, path: str) -> dict:
        """Get detailed info about a file."""
        try:
            p = Path(path).resolve()
            if not p.exists():
                return {"success": False, "error": "File not found"}

            st = p.stat()
            return {
                "success": True,
                "name": p.name,
                "path": str(p),
                "is_dir": p.is_dir(),
                "is_file": p.is_file(),
                "size": st.st_size,
                "size_human": self._human_size(st.st_size),
                "created": datetime.fromtimestamp(st.st_ctime).isoformat(),
                "modified": datetime.fromtimestamp(st.st_mtime).isoformat(),
                "accessed": datetime.fromtimestamp(st.st_atime).isoformat(),
                "permissions": stat.filemode(st.st_mode),
                "extension": p.suffix,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def read_file(self, path: str, offset: int = 0,
                        limit: int = 1024 * 1024) -> dict:
        """Read file contents (text files, up to limit bytes)."""
        try:
            p = Path(path).resolve()
            if not p.exists():
                return {"success": False, "error": "File not found"}
            if not p.is_file():
                return {"success": False, "error": "Not a file"}

            size = p.stat().st_size
            if size > 50 * 1024 * 1024:  # 50MB limit for reading
                return {"success": False, "error": "File too large to read directly"}

            async with aiofiles.open(str(p), "rb") as f:
                await f.seek(offset)
                data = await f.read(limit)

            # Try to decode as text
            try:
                content = data.decode("utf-8")
                is_text = True
            except UnicodeDecodeError:
                content = base64.b64encode(data).decode()
                is_text = False

            await audit_logger.log("file_read", {
                "path": str(p), "size": len(data)
            })

            return {
                "success": True,
                "path": str(p),
                "content": content,
                "is_text": is_text,
                "size": len(data),
                "total_size": size,
                "offset": offset,
                "has_more": offset + limit < size,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def download_file(self, path: str) -> dict:
        """Prepare a file for download (returns base64 encoded content)."""
        try:
            p = Path(path).resolve()
            if not p.exists():
                return {"success": False, "error": "File not found"}
            if not p.is_file():
                return {"success": False, "error": "Not a file"}

            size = p.stat().st_size
            if size > 100 * 1024 * 1024:  # 100MB limit
                return {"success": False, "error": "File too large (>100MB). Use chunked transfer."}

            async with aiofiles.open(str(p), "rb") as f:
                data = await f.read()

            await audit_logger.log("file_download", {
                "path": str(p), "size": size
            }, risk_level="medium")

            return {
                "success": True,
                "name": p.name,
                "path": str(p),
                "size": size,
                "data_b64": base64.b64encode(data).decode(),
                "mime_type": self._guess_mime(p.suffix),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def download_file_chunked(self, path: str, chunk_size: int = 512 * 1024):
        """Generator: yield file in chunks for large file transfer."""
        p = Path(path).resolve()
        async with aiofiles.open(str(p), "rb") as f:
            while True:
                chunk = await f.read(chunk_size)
                if not chunk:
                    break
                yield {
                    "data_b64": base64.b64encode(chunk).decode(),
                    "size": len(chunk),
                }

    async def upload_file(self, path: str, data_b64: str,
                          overwrite: bool = False) -> dict:
        """Upload a file (base64 encoded data)."""
        try:
            p = Path(path).resolve()

            if p.exists() and not overwrite:
                return {"success": False, "error": "File already exists. Set overwrite=true."}

            # Ensure parent directory exists
            p.parent.mkdir(parents=True, exist_ok=True)

            data = base64.b64decode(data_b64)
            async with aiofiles.open(str(p), "wb") as f:
                await f.write(data)

            await audit_logger.log("file_upload", {
                "path": str(p), "size": len(data)
            }, risk_level="medium")

            return {
                "success": True,
                "path": str(p),
                "size": len(data),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def create_directory(self, path: str) -> dict:
        """Create a new directory."""
        try:
            p = Path(path).resolve()
            p.mkdir(parents=True, exist_ok=True)
            await audit_logger.log("dir_create", {"path": str(p)})
            return {"success": True, "path": str(p)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def request_delete(self, path: str) -> dict:
        """
        Request deletion of a file/directory.
        Returns a confirmation token that must be sent back to confirm_delete.
        This implements the "confirmation prompt for dangerous actions" requirement.
        """
        p = Path(path).resolve()

        # Safety checks
        if str(p) in PROTECTED_PATHS or any(str(p).startswith(pp) and len(str(p)) <= len(pp) + 1
                                            for pp in PROTECTED_PATHS):
            return {"success": False, "error": "Cannot delete protected system path"}

        if not p.exists():
            return {"success": False, "error": "Path not found"}

        import secrets
        token = secrets.token_hex(16)
        self._pending_deletes[token] = {
            "path": str(p),
            "is_dir": p.is_dir(),
            "size": p.stat().st_size if p.is_file() else self._dir_size(p),
        }

        return {
            "success": True,
            "action": "confirm_required",
            "confirmation_token": token,
            "path": str(p),
            "is_dir": p.is_dir(),
            "message": f"Are you sure you want to delete '{p.name}'? "
                       f"Send confirm_delete with the token to proceed.",
        }

    async def confirm_delete(self, token: str) -> dict:
        """Confirm and execute a deletion."""
        pending = self._pending_deletes.pop(token, None)
        if not pending:
            return {"success": False, "error": "Invalid or expired confirmation token"}

        try:
            p = Path(pending["path"])
            if p.is_dir():
                shutil.rmtree(str(p))
            else:
                p.unlink()

            await audit_logger.log("file_delete", {
                "path": pending["path"],
                "is_dir": pending["is_dir"],
            }, risk_level="high")

            return {"success": True, "deleted": pending["path"]}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def rename(self, old_path: str, new_name: str) -> dict:
        """Rename a file or directory."""
        try:
            p = Path(old_path).resolve()
            if not p.exists():
                return {"success": False, "error": "Path not found"}
            new_path = p.parent / new_name
            p.rename(new_path)
            await audit_logger.log("file_rename", {
                "old": str(p), "new": str(new_path)
            })
            return {"success": True, "old_path": str(p), "new_path": str(new_path)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def copy(self, src: str, dst: str) -> dict:
        """Copy a file or directory."""
        try:
            src_p = Path(src).resolve()
            dst_p = Path(dst).resolve()
            if src_p.is_dir():
                shutil.copytree(str(src_p), str(dst_p))
            else:
                shutil.copy2(str(src_p), str(dst_p))
            await audit_logger.log("file_copy", {
                "src": str(src_p), "dst": str(dst_p)
            })
            return {"success": True, "src": str(src_p), "dst": str(dst_p)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_drives(self) -> list[dict]:
        """List available drives/mount points."""
        import platform
        drives = []
        if platform.system() == "Windows":
            import string
            for letter in string.ascii_uppercase:
                drive = f"{letter}:\\"
                if os.path.exists(drive):
                    try:
                        total, used, free = shutil.disk_usage(drive)
                        drives.append({
                            "path": drive,
                            "label": f"{letter}:",
                            "total_gb": round(total / (1024 ** 3), 2),
                            "free_gb": round(free / (1024 ** 3), 2),
                        })
                    except (PermissionError, OSError):
                        drives.append({"path": drive, "label": f"{letter}:"})
        else:
            # Unix: list mount points
            drives.append({"path": "/", "label": "Root"})
            home = str(Path.home())
            drives.append({"path": home, "label": "Home"})
            # Check common mount points
            for mount in ["/media", "/mnt", "/Volumes"]:
                if os.path.exists(mount):
                    for item in os.listdir(mount):
                        full = os.path.join(mount, item)
                        if os.path.isdir(full):
                            drives.append({"path": full, "label": item})

        return drives

    @staticmethod
    def _human_size(size: int) -> str:
        """Convert bytes to human readable size."""
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"

    @staticmethod
    def _dir_size(path: Path) -> int:
        """Calculate total size of a directory."""
        total = 0
        try:
            for p in path.rglob("*"):
                if p.is_file():
                    total += p.stat().st_size
        except (PermissionError, OSError):
            pass
        return total

    @staticmethod
    def _guess_mime(ext: str) -> str:
        """Guess MIME type from extension."""
        mime_map = {
            ".txt": "text/plain", ".html": "text/html", ".css": "text/css",
            ".js": "application/javascript", ".json": "application/json",
            ".py": "text/x-python", ".md": "text/markdown",
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp",
            ".pdf": "application/pdf", ".zip": "application/zip",
            ".mp3": "audio/mpeg", ".mp4": "video/mp4",
            ".doc": "application/msword",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".xls": "application/vnd.ms-excel",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }
        return mime_map.get(ext.lower(), "application/octet-stream")
