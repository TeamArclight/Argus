import mimetypes
from pathlib import Path
from typing import Any
import httpx

from app.core.logging import get_logger
from app.storage.base import StorageProvider

logger = get_logger("argus.storage.supabase")


class SupabaseStorageProvider(StorageProvider):
    """Supabase Object Storage provider implementing the StorageProvider interface."""

    def __init__(
        self,
        supabase_url: str,
        service_role_key: str,
        bucket_name: str = "documents",
        local_fallback_path: str | Path = "./data/uploads",
        timeout_seconds: float = 30.0,
        http_client: httpx.Client | None = None,
    ):
        if not supabase_url or not supabase_url.strip():
            raise ValueError("supabase_url must be a non-empty string.")
        if not service_role_key or not service_role_key.strip():
            raise ValueError("service_role_key must be a non-empty string.")

        self.supabase_url = supabase_url.strip().rstrip("/")
        self.service_role_key = service_role_key.strip()
        self.bucket_name = bucket_name.strip() or "documents"
        self.local_fallback_path = Path(local_fallback_path).resolve()
        self.timeout_seconds = timeout_seconds

        self._headers = {
            "Authorization": f"Bearer {self.service_role_key}",
            "apikey": self.service_role_key,
        }
        self._storage_base = f"{self.supabase_url}/storage/v1"
        self._client = http_client or httpx.Client(timeout=self.timeout_seconds)
        self._bucket_verified = False

    def _normalize_key(self, storage_key: str) -> str:
        """Sanitizes and normalizes key while preventing path traversal."""
        if not storage_key:
            raise ValueError("Storage key cannot be empty.")
        clean = str(storage_key).replace("\\", "/")
        if ":" in clean:
            clean = clean.split(":", 1)[-1]
        clean = clean.lstrip("/")
        parts = clean.split("/")
        clean_parts = [p for p in parts if p not in ("", ".")]
        if ".." in clean_parts:
            raise ValueError(f"Path traversal security violation: '{storage_key}' attempts to escape storage root.")
        clean = "/".join(clean_parts)
        if not clean:
            raise ValueError("Storage key cannot resolve to empty path.")
        return clean

    def _ensure_bucket(self) -> None:
        """Ensures private bucket exists in Supabase Storage."""
        if self._bucket_verified:
            return
        try:
            resp = self._client.post(
                f"{self._storage_base}/bucket",
                headers={**self._headers, "Content-Type": "application/json"},
                json={
                    "id": self.bucket_name,
                    "name": self.bucket_name,
                    "public": False,
                    "file_size_limit": 52428800,
                },
            )
            if resp.status_code in (200, 201, 400, 409):
                self._bucket_verified = True
            else:
                logger.warning(
                    "Unexpected status code when ensuring bucket %s: %s %s",
                    self.bucket_name,
                    resp.status_code,
                    resp.text,
                )
        except Exception as exc:
            logger.warning("Could not auto-create/verify bucket %s: %s", self.bucket_name, str(exc))

    def store_file(self, file_bytes: bytes, target_key: str) -> str:
        """Stores binary payload under target_key in Supabase Storage."""
        clean_key = self._normalize_key(target_key)
        self._ensure_bucket()

        # Prevent silent overwrite: check if file already exists
        if self.file_exists(clean_key):
            raise FileExistsError(f"Storage key already exists: '{clean_key}'")

        content_type, _ = mimetypes.guess_type(clean_key)
        content_type = content_type or "application/octet-stream"

        url = f"{self._storage_base}/object/{self.bucket_name}/{clean_key}"
        headers = {
            **self._headers,
            "Content-Type": content_type,
            "x-upsert": "false",
        }

        try:
            resp = self._client.post(url, headers=headers, content=file_bytes)
        except Exception as exc:
            logger.exception("Network error during Supabase upload for %s: %s", clean_key, str(exc))
            raise RuntimeError(f"Supabase storage network error: {exc}") from exc

        if resp.status_code in (400, 409):
            err_text = resp.text.lower()
            if "already exists" in err_text or "duplicate" in err_text:
                raise FileExistsError(f"Storage key already exists: '{clean_key}'")

        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"Supabase storage upload failed with status {resp.status_code}: {resp.text}"
            )

        return clean_key

    def read_file(self, storage_key: str) -> bytes:
        """Reads raw binary content for storage_key from Supabase Storage."""
        clean_key = self._normalize_key(storage_key)

        # Primary: Fetch from Supabase Storage authenticated object endpoint
        url = f"{self._storage_base}/object/authenticated/{self.bucket_name}/{clean_key}"
        try:
            resp = self._client.get(url, headers=self._headers)
            if resp.status_code == 200:
                return resp.content
            elif resp.status_code == 404:
                # Fallback: try standard object path
                alt_url = f"{self._storage_base}/object/{self.bucket_name}/{clean_key}"
                alt_resp = self._client.get(alt_url, headers=self._headers)
                if alt_resp.status_code == 200:
                    return alt_resp.content
        except Exception as exc:
            logger.exception("Error reading from Supabase for key %s: %s", clean_key, str(exc))
            raise RuntimeError(f"Supabase storage read error: {exc}") from exc

        # Backward compatibility fallback: Check local storage
        local_path = (self.local_fallback_path / clean_key).resolve()
        if local_path.is_file():
            try:
                with open(local_path, "rb") as f:
                    return f.read()
            except Exception:
                pass

        raise FileNotFoundError(f"File not found on storage for key: '{clean_key}'")

    def file_exists(self, storage_key: str) -> bool:
        """Returns True if file exists in Supabase Storage or local fallback."""
        try:
            clean_key = self._normalize_key(storage_key)
        except ValueError:
            return False

        url = f"{self._storage_base}/object/info/authenticated/{self.bucket_name}/{clean_key}"
        try:
            resp = self._client.get(url, headers=self._headers)
            if resp.status_code == 200:
                return True
            elif resp.status_code == 404:
                head_resp = self._client.head(
                    f"{self._storage_base}/object/authenticated/{self.bucket_name}/{clean_key}",
                    headers=self._headers,
                )
                if head_resp.status_code == 200:
                    return True
        except Exception:
            pass

        # Backward compatibility fallback: Check local filesystem
        try:
            local_path = (self.local_fallback_path / clean_key).resolve()
            return local_path.is_file()
        except Exception:
            return False

    def delete_file(self, storage_key: str) -> bool:
        """Deletes file from Supabase Storage and local cache if present."""
        clean_key = self._normalize_key(storage_key)
        existed = False

        if self.file_exists(clean_key):
            existed = True

        url = f"{self._storage_base}/object/{self.bucket_name}/{clean_key}"
        try:
            resp = self._client.delete(url, headers=self._headers)
            if resp.status_code in (200, 204):
                existed = True
        except Exception:
            pass

        # Clean local cache or fallback file if present
        cache_path = (self.local_fallback_path / ".cache" / clean_key).resolve()
        if cache_path.is_file():
            try:
                cache_path.unlink()
            except OSError:
                pass

        local_path = (self.local_fallback_path / clean_key).resolve()
        if local_path.is_file():
            try:
                local_path.unlink()
                existed = True
            except OSError:
                pass

        return existed

    def get_file_path(self, storage_key: str) -> Path:
        """Resolves local filesystem Path by downloading/caching from Supabase Storage."""
        clean_key = self._normalize_key(storage_key)
        cache_dir = self.local_fallback_path / ".cache"
        cache_path = (cache_dir / clean_key).resolve()

        if cache_path.is_file() and cache_path.stat().st_size > 0:
            return cache_path

        local_path = (self.local_fallback_path / clean_key).resolve()
        if local_path.is_file() and local_path.stat().st_size > 0:
            return local_path

        file_bytes = self.read_file(clean_key)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "wb") as f:
            f.write(file_bytes)
        return cache_path

    def get_metadata(self, storage_key: str) -> dict[str, Any]:
        """Returns metadata for storage_key from Supabase or local fallback."""
        clean_key = self._normalize_key(storage_key)
        url = f"{self._storage_base}/object/info/authenticated/{self.bucket_name}/{clean_key}"

        try:
            resp = self._client.get(url, headers=self._headers)
            if resp.status_code == 200:
                data = resp.json()
                size = (
                    data.get("metadata", {}).get("size")
                    or data.get("size")
                    or data.get("content_length")
                    or 0
                )
                modified = data.get("updated_at") or data.get("created_at")
                return {
                    "size_bytes": size,
                    "modified_at": modified,
                    "storage_key": clean_key,
                }
        except Exception:
            pass

        local_path = (self.local_fallback_path / clean_key).resolve()
        if local_path.is_file():
            stat = local_path.stat()
            return {
                "size_bytes": stat.st_size,
                "modified_at": stat.st_mtime,
                "storage_key": clean_key,
            }

        raise FileNotFoundError(f"File not found on storage for key: '{clean_key}'")
