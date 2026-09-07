import os
from pathlib import Path
from typing import Any
from app.storage.base import StorageProvider


class LocalStorageProvider(StorageProvider):
    """Local filesystem storage provider with path traversal containment checks."""

    def __init__(self, base_path: str | Path):
        self.root_path = Path(base_path).resolve()
        self.root_path.mkdir(parents=True, exist_ok=True)

    def _resolve_safe_path(self, storage_key: str) -> Path:
        """Strictly resolves target_key within root_path, raising ValueError on path traversal."""
        if not storage_key:
            raise ValueError("Storage key cannot be empty.")
        
        # Replace backslashes with forward slashes and strip leading separators / drive letters
        clean_key = str(storage_key).replace("\\", "/")
        # Remove any leading drive letters (e.g. C:) or root slashes
        if ":" in clean_key:
            clean_key = clean_key.split(":", 1)[-1]
        clean_key = clean_key.lstrip("/")
        
        resolved = (self.root_path / clean_key).resolve()
        
        # Strict containment check using is_relative_to
        if not resolved.is_relative_to(self.root_path):
            raise ValueError(f"Path traversal security violation: '{storage_key}' attempts to escape storage root.")
            
        return resolved

    def store_file(self, file_bytes: bytes, target_key: str) -> str:
        target_path = self._resolve_safe_path(target_key)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Atomic write via temporary file in target directory
        temp_path = target_path.parent / f".tmp_{target_path.name}"
        try:
            with open(temp_path, "wb") as f:
                f.write(file_bytes)
            os.replace(temp_path, target_path)
        except Exception:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass
            raise

        # Return clean relative storage key string
        return target_key.replace("\\", "/").lstrip("/")

    def delete_file(self, storage_key: str) -> bool:
        try:
            target_path = self._resolve_safe_path(storage_key)
            if target_path.exists() and target_path.is_file():
                target_path.unlink()
                return True
            return False
        except ValueError:
            return False

    def file_exists(self, storage_key: str) -> bool:
        try:
            target_path = self._resolve_safe_path(storage_key)
            return target_path.exists() and target_path.is_file()
        except ValueError:
            return False

    def get_file_path(self, storage_key: str) -> Path:
        return self._resolve_safe_path(storage_key)

    def read_file(self, storage_key: str) -> bytes:
        target_path = self._resolve_safe_path(storage_key)
        if not target_path.exists() or not target_path.is_file():
            raise FileNotFoundError(f"File not found for key: '{storage_key}'")
        with open(target_path, "rb") as f:
            return f.read()

    def get_metadata(self, storage_key: str) -> dict[str, Any]:
        target_path = self._resolve_safe_path(storage_key)
        if not target_path.exists() or not target_path.is_file():
            raise FileNotFoundError(f"File not found for key: '{storage_key}'")
        stat = target_path.stat()
        return {
            "size_bytes": stat.st_size,
            "modified_at": stat.st_mtime,
            "storage_key": storage_key,
        }
