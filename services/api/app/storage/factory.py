from app.core.config import settings
from app.storage.base import StorageProvider
from app.storage.local import LocalStorageProvider
from app.storage.supabase import SupabaseStorageProvider


def get_storage_provider() -> StorageProvider:
    """Instantiates configured StorageProvider based on ARGUS_STORAGE_BACKEND settings."""
    backend = settings.ARGUS_STORAGE_BACKEND.lower().strip()
    if backend == "local":
        return LocalStorageProvider(settings.ARGUS_STORAGE_LOCAL_PATH)
    elif backend == "supabase":
        url = settings.get_supabase_url()
        key = settings.ARGUS_SUPABASE_SERVICE_ROLE_KEY
        if not url:
            raise ValueError(
                "ARGUS_SUPABASE_URL (or DATABASE_URL containing Supabase project) is required when ARGUS_STORAGE_BACKEND='supabase'"
            )
        if not key:
            raise ValueError(
                "ARGUS_SUPABASE_SERVICE_ROLE_KEY is required when ARGUS_STORAGE_BACKEND='supabase'"
            )
        return SupabaseStorageProvider(
            supabase_url=url,
            service_role_key=key,
            bucket_name=settings.ARGUS_STORAGE_SUPABASE_BUCKET,
            local_fallback_path=settings.ARGUS_STORAGE_LOCAL_PATH,
            timeout_seconds=settings.ARGUS_STORAGE_SUPABASE_TIMEOUT_SECONDS,
        )
    raise ValueError(f"Unsupported storage backend: '{settings.ARGUS_STORAGE_BACKEND}'")
