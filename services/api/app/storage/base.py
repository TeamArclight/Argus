from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class StorageProvider(ABC):
    """Abstract Storage Provider interface for ARGUS document persistence."""

    @abstractmethod
    def store_file(self, file_bytes: bytes, target_key: str) -> str:
        """Stores binary payload under target_key and returns final storage URI/key."""
        pass

    @abstractmethod
    def delete_file(self, storage_key: str) -> bool:
        """Deletes file at storage_key. Returns True if deleted, False if not found."""
        pass

    @abstractmethod
    def file_exists(self, storage_key: str) -> bool:
        """Returns True if file exists at storage_key."""
        pass

    @abstractmethod
    def get_file_path(self, storage_key: str) -> Path:
        """Resolves local filesystem path if available, enforcing root containment."""
        pass

    @abstractmethod
    def read_file(self, storage_key: str) -> bytes:
        """Reads raw binary content for storage_key."""
        pass

    @abstractmethod
    def get_metadata(self, storage_key: str) -> dict[str, Any]:
        """Returns file metadata (e.g. size_bytes, modified_at)."""
        pass
