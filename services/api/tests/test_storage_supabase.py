from pathlib import Path
import pytest
import httpx
from app.storage.supabase import SupabaseStorageProvider
from app.storage.factory import get_storage_provider
from app.core.config import Settings


class MockResponse:
    def __init__(self, status_code: int, content: bytes = b"", json_data: dict | None = None):
        self.status_code = status_code
        self.content = content
        self._json_data = json_data or {}
        self.text = content.decode("utf-8", errors="ignore") if content else ""

    def json(self):
        return self._json_data


def test_supabase_storage_key_normalization_and_path_traversal(tmp_path):
    provider = SupabaseStorageProvider(
        supabase_url="https://testproject.supabase.co",
        service_role_key="test-key-12345",
        local_fallback_path=tmp_path,
    )

    # Valid keys
    assert provider._normalize_key("tenders/123/doc.pdf") == "tenders/123/doc.pdf"
    assert provider._normalize_key("/tenders/123/doc.pdf") == "tenders/123/doc.pdf"
    assert provider._normalize_key("tenders\\123\\doc.pdf") == "tenders/123/doc.pdf"
    assert provider._normalize_key("C:/tenders/123/doc.pdf") == "tenders/123/doc.pdf"

    # Traversal attacks
    with pytest.raises(ValueError, match="Path traversal"):
        provider._normalize_key("../../etc/passwd")

    with pytest.raises(ValueError, match="Path traversal"):
        provider._normalize_key("tenders/../../evil.pdf")

    with pytest.raises(ValueError, match="cannot be empty"):
        provider._normalize_key("")


def test_supabase_storage_store_file_success(tmp_path, monkeypatch):
    stored_requests = []

    def mock_post(client, url, headers=None, content=None, json=None):
        stored_requests.append({"url": str(url), "content": content, "json": json})
        if "/bucket" in str(url):
            return MockResponse(200, json_data={"id": "documents"})
        return MockResponse(201, json_data={"Key": "documents/tenders/t1/doc1.pdf"})

    def mock_get(client, url, headers=None):
        # file does not exist initially
        return MockResponse(404)

    monkeypatch.setattr(httpx.Client, "post", mock_post)
    monkeypatch.setattr(httpx.Client, "get", mock_get)

    provider = SupabaseStorageProvider(
        supabase_url="https://testproject.supabase.co",
        service_role_key="secret-service-role-key",
        bucket_name="documents",
        local_fallback_path=tmp_path,
    )

    result_key = provider.store_file(b"test pdf content", "tenders/t1/doc1.pdf")
    assert result_key == "tenders/t1/doc1.pdf"
    assert any("tenders/t1/doc1.pdf" in r["url"] for r in stored_requests)


def test_supabase_storage_duplicate_protection(tmp_path, monkeypatch):
    def mock_post(client, url, headers=None, content=None, json=None):
        return MockResponse(200)

    def mock_get(client, url, headers=None):
        # file already exists
        return MockResponse(200, json_data={"id": "doc1"})

    monkeypatch.setattr(httpx.Client, "post", mock_post)
    monkeypatch.setattr(httpx.Client, "get", mock_get)

    provider = SupabaseStorageProvider(
        supabase_url="https://testproject.supabase.co",
        service_role_key="secret-service-role-key",
        local_fallback_path=tmp_path,
    )

    with pytest.raises(FileExistsError, match="already exists"):
        provider.store_file(b"test pdf content", "tenders/t1/doc1.pdf")


def test_supabase_storage_read_file_success(tmp_path, monkeypatch):
    expected_bytes = b"BINARY_PDF_PAYLOAD_12345"

    def mock_get(client, url, headers=None):
        if "tenders/t1/doc1.pdf" in str(url):
            return MockResponse(200, content=expected_bytes)
        return MockResponse(404)

    monkeypatch.setattr(httpx.Client, "get", mock_get)

    provider = SupabaseStorageProvider(
        supabase_url="https://testproject.supabase.co",
        service_role_key="secret-service-role-key",
        local_fallback_path=tmp_path,
    )

    content = provider.read_file("tenders/t1/doc1.pdf")
    assert content == expected_bytes


def test_supabase_storage_read_file_not_found(tmp_path, monkeypatch):
    def mock_get(client, url, headers=None):
        return MockResponse(404)

    monkeypatch.setattr(httpx.Client, "get", mock_get)

    provider = SupabaseStorageProvider(
        supabase_url="https://testproject.supabase.co",
        service_role_key="secret-service-role-key",
        local_fallback_path=tmp_path,
    )

    with pytest.raises(FileNotFoundError, match="File not found on storage"):
        provider.read_file("tenders/t1/missing.pdf")


def test_supabase_storage_backward_compatibility_local_fallback(tmp_path, monkeypatch):
    # Remote returns 404
    def mock_get(client, url, headers=None):
        return MockResponse(404)

    monkeypatch.setattr(httpx.Client, "get", mock_get)

    # Put a legacy file in the local fallback path
    legacy_file = tmp_path / "tenders" / "t1" / "legacy.pdf"
    legacy_file.parent.mkdir(parents=True, exist_ok=True)
    legacy_file.write_bytes(b"LEGACY_LOCAL_CONTENT")

    provider = SupabaseStorageProvider(
        supabase_url="https://testproject.supabase.co",
        service_role_key="secret-service-role-key",
        local_fallback_path=tmp_path,
    )

    # file_exists should detect local file
    assert provider.file_exists("tenders/t1/legacy.pdf") is True
    # read_file should read local fallback file
    assert provider.read_file("tenders/t1/legacy.pdf") == b"LEGACY_LOCAL_CONTENT"


def test_supabase_storage_file_exists(tmp_path, monkeypatch):
    def mock_get(client, url, headers=None):
        if "existing.pdf" in str(url):
            return MockResponse(200)
        return MockResponse(404)

    monkeypatch.setattr(httpx.Client, "get", mock_get)
    monkeypatch.setattr(httpx.Client, "head", lambda client, url, headers=None: MockResponse(404))

    provider = SupabaseStorageProvider(
        supabase_url="https://testproject.supabase.co",
        service_role_key="secret-service-role-key",
        local_fallback_path=tmp_path,
    )

    assert provider.file_exists("tenders/t1/existing.pdf") is True
    assert provider.file_exists("tenders/t1/nonexistent.pdf") is False


def test_supabase_storage_delete_file(tmp_path, monkeypatch):
    deleted_urls = []

    def mock_get(client, url, headers=None):
        return MockResponse(200)

    def mock_delete(client, url, headers=None):
        deleted_urls.append(str(url))
        return MockResponse(200)

    monkeypatch.setattr(httpx.Client, "get", mock_get)
    monkeypatch.setattr(httpx.Client, "delete", mock_delete)

    provider = SupabaseStorageProvider(
        supabase_url="https://testproject.supabase.co",
        service_role_key="secret-service-role-key",
        local_fallback_path=tmp_path,
    )

    assert provider.delete_file("tenders/t1/to_delete.pdf") is True
    assert any("to_delete.pdf" in u for u in deleted_urls)


def test_supabase_storage_get_file_path_caching(tmp_path, monkeypatch):
    test_bytes = b"CACHED_FILE_CONTENT_XYZ"

    def mock_get(client, url, headers=None):
        return MockResponse(200, content=test_bytes)

    monkeypatch.setattr(httpx.Client, "get", mock_get)

    provider = SupabaseStorageProvider(
        supabase_url="https://testproject.supabase.co",
        service_role_key="secret-service-role-key",
        local_fallback_path=tmp_path,
    )

    # First call caches file
    p1 = provider.get_file_path("tenders/t1/doc.pdf")
    assert p1.exists()
    assert p1.read_bytes() == test_bytes

    # Second call uses cache without downloading
    p2 = provider.get_file_path("tenders/t1/doc.pdf")
    assert p1 == p2


def test_supabase_storage_get_metadata(tmp_path, monkeypatch):
    def mock_get(client, url, headers=None):
        return MockResponse(
            200,
            json_data={
                "id": "123",
                "name": "doc.pdf",
                "metadata": {"size": 4096},
                "updated_at": "2026-09-13T04:00:00Z",
            },
        )

    monkeypatch.setattr(httpx.Client, "get", mock_get)

    provider = SupabaseStorageProvider(
        supabase_url="https://testproject.supabase.co",
        service_role_key="secret-service-role-key",
        local_fallback_path=tmp_path,
    )

    meta = provider.get_metadata("tenders/t1/doc.pdf")
    assert meta["size_bytes"] == 4096
    assert meta["storage_key"] == "tenders/t1/doc.pdf"


def test_supabase_storage_error_handling(tmp_path, monkeypatch):
    # Upload error 500
    def mock_post_500(client, url, headers=None, content=None, json=None):
        if "/bucket" in str(url):
            return MockResponse(200)
        return MockResponse(500, content=b"Internal Storage Error")

    def mock_get_404(client, url, headers=None):
        return MockResponse(404)

    monkeypatch.setattr(httpx.Client, "post", mock_post_500)
    monkeypatch.setattr(httpx.Client, "get", mock_get_404)

    provider = SupabaseStorageProvider(
        supabase_url="https://testproject.supabase.co",
        service_role_key="secret-service-role-key",
        local_fallback_path=tmp_path,
    )

    with pytest.raises(RuntimeError, match="Supabase storage upload failed with status 500"):
        provider.store_file(b"test", "tenders/t1/error.pdf")


def test_storage_factory_supabase(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "ARGUS_STORAGE_BACKEND", "supabase")
    monkeypatch.setattr(settings, "ARGUS_SUPABASE_URL", "https://xyz.supabase.co")
    monkeypatch.setattr(settings, "ARGUS_SUPABASE_SERVICE_ROLE_KEY", "test-key-321")

    provider = get_storage_provider()
    assert isinstance(provider, SupabaseStorageProvider)
    assert provider.supabase_url == "https://xyz.supabase.co"
    assert provider.service_role_key == "test-key-321"
