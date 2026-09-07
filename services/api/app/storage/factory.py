from app.core.config import settings
from app.storage.base import StorageProvider
from app.storage.local import LocalStorageProvider


def get_storage_provider() -> StorageProvider:
    """Instantiates configured StorageProvider based on ARGUS_STORAGE_BACKEND settings."""
    backend = settings.ARGUS_STORAGE_BACKEND.lower().strip()
    if backend == "local":
        return LocalStorageProvider(settings.ARGUS_STORAGE_LOCAL_PATH)
    raise ValueError(f"Unsupported storage backend: '{settings.ARGUS_STORAGE_BACKEND}'")
