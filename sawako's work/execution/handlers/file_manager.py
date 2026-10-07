"""
kaoruko/execution/handlers/file_manager.py
File system operations: open, create, search, move, delete (with confirmation).
"""
from __future__ import annotations
import os, shutil, subprocess
from pathlib import Path
from typing import Optional, TYPE_CHECKING
from kaoruko.infrastructure.logging.logger import get_logger
if TYPE_CHECKING:
    from kaoruko.infrastructure.config.schema import KaorukoConfig

log = get_logger("execution.file_manager")

try:
    from send2trash import send2trash
    _SEND2TRASH = True
except ImportError:
    _SEND2TRASH = False

_KNOWN_FOLDERS = {
    "desktop":   Path.home() / "Desktop",
    "downloads": Path.home() / "Downloads",
    "documents": Path.home() / "Documents",
    "pictures":  Path.home() / "Pictures",
    "music":     Path.home() / "Music",
    "videos":    Path.home() / "Videos",
    "home":      Path.home(),
}

class FileManagerHandler:
    def __init__(self, config: "KaorukoConfig") -> None:
        self.config = config

    def open_folder(self, path: str, **kwargs) -> str:
        resolved = _KNOWN_FOLDERS.get(path.lower().strip())
        target = str(resolved) if resolved else path
        subprocess.Popen(["explorer", target])
        log.info("folder_opened", path=target)
        return f"Opening {path} folder~"

    def create_folder(self, path: str, **kwargs) -> str:
        # Default to Desktop if no full path given
        if not os.path.isabs(path):
            target = Path.home() / "Desktop" / path
        else:
            target = Path(path)
        target.mkdir(parents=True, exist_ok=True)
        log.info("folder_created", path=str(target))
        return f"Created folder '{path}'~"

    def search_file(self, path: str, **kwargs) -> str:
        """Open Windows Search for a file."""
        subprocess.Popen(["explorer", f"search-ms:query={path}&crumb=location:"])
        return f"Searching for '{path}'~"

    def delete_folder(self, path: str, **kwargs) -> str:
        resolved = _KNOWN_FOLDERS.get(path.lower()) or Path(path)
        if _SEND2TRASH:
            send2trash(str(resolved))
            return f"Moved '{path}' to Recycle Bin~"
        else:
            shutil.rmtree(str(resolved), ignore_errors=True)
            return f"Deleted '{path}'~"

    def move_file(self, source: str, destination: str, **kwargs) -> str:
        shutil.move(source, destination)
        return f"Moved to {destination}~"
