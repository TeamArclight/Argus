"""Fail-closed behaviour of the intelligence service.

Audit findings C-3 (unauthenticated by default) and C-4 (optional storage-root
allowlist). Both defaults were open; these tests pin them closed.
"""
import pytest
from fastapi.testclient import TestClient

from argus_ai.http_service import _anonymous_access_permitted, create_app
from argus_ai.storage import DocumentResolutionError, resolved_document


# ---------------------------------------------------------------------------
# C-3 — authentication
# ---------------------------------------------------------------------------

def test_anonymous_access_requires_both_optin_and_local_env(monkeypatch):
    monkeypatch.delenv("ARGUS_INTELLIGENCE_API_KEY", raising=False)

    monkeypatch.setenv("ARGUS_ALLOW_ANONYMOUS_INTELLIGENCE", "true")
    monkeypatch.setenv("APP_ENV", "development")
    assert _anonymous_access_permitted() is True

    # Opt-in alone is not enough outside a local environment.
    monkeypatch.setenv("APP_ENV", "production")
    assert _anonymous_access_permitted() is False

    monkeypatch.setenv("APP_ENV", "staging")
    assert _anonymous_access_permitted() is False

    # A local environment alone is not enough either.
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("ARGUS_ALLOW_ANONYMOUS_INTELLIGENCE", "false")
    assert _anonymous_access_permitted() is False


def test_missing_key_without_optin_rejects_requests(monkeypatch):
    """The shipped default — empty API key, no opt-in — must now reject."""
    monkeypatch.delenv("ARGUS_INTELLIGENCE_API_KEY", raising=False)
    monkeypatch.setenv("ARGUS_ALLOW_ANONYMOUS_INTELLIGENCE", "false")
    monkeypatch.setenv("APP_ENV", "development")

    client = TestClient(create_app())
    for path, body in [
        ("/rag-query", {"query": "turnover"}),
        ("/rag-delete", {"document_id": "doc-1"}),
        ("/classify-document", {"document_uri": "/etc/passwd"}),
        ("/extract-tender", {"tender_id": "t-1"}),
    ]:
        assert client.post(path, json=body).status_code == 401, path

    assert client.get("/health").status_code == 401


def test_liveness_probe_is_unauthenticated(monkeypatch):
    """Orchestrators must be able to probe liveness without the credential."""
    monkeypatch.delenv("ARGUS_INTELLIGENCE_API_KEY", raising=False)
    monkeypatch.setenv("ARGUS_ALLOW_ANONYMOUS_INTELLIGENCE", "false")
    monkeypatch.setenv("APP_ENV", "development")

    response = TestClient(create_app()).get("/livez")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    # Liveness must not leak provider or model configuration.
    assert set(response.json()) == {"status", "service"}


def test_configured_key_is_enforced(monkeypatch):
    monkeypatch.setenv("ARGUS_INTELLIGENCE_API_KEY", "s3cr3t-intelligence-key")
    monkeypatch.setenv("APP_ENV", "development")
    client = TestClient(create_app())

    assert client.get("/health").status_code == 401
    assert client.get("/health", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert (
        client.get("/health", headers={"Authorization": "Bearer s3cr3t-intelligence-key"}).status_code
        == 200
    )


def test_production_without_key_refuses_to_start(monkeypatch):
    monkeypatch.delenv("ARGUS_INTELLIGENCE_API_KEY", raising=False)
    monkeypatch.setenv("ARGUS_ALLOW_ANONYMOUS_INTELLIGENCE", "true")
    monkeypatch.setenv("APP_ENV", "production")

    with pytest.raises(RuntimeError, match="ARGUS_INTELLIGENCE_API_KEY is required"):
        create_app()


# ---------------------------------------------------------------------------
# C-4 — storage-root allowlist
# ---------------------------------------------------------------------------

def test_local_path_resolution_requires_configured_roots(tmp_path, monkeypatch):
    document = tmp_path / "tender.txt"
    document.write_text("content")

    monkeypatch.delenv("ARGUS_ALLOWED_STORAGE_ROOTS", raising=False)
    monkeypatch.setenv("ARGUS_ALLOW_UNRESTRICTED_LOCAL_PATHS", "false")

    with pytest.raises(DocumentResolutionError, match="requires ARGUS_ALLOWED_STORAGE_ROOTS"):
        with resolved_document(document):
            pass


def test_unrestricted_optin_is_ignored_outside_local_env(tmp_path, monkeypatch):
    document = tmp_path / "tender.txt"
    document.write_text("content")

    monkeypatch.delenv("ARGUS_ALLOWED_STORAGE_ROOTS", raising=False)
    monkeypatch.setenv("ARGUS_ALLOW_UNRESTRICTED_LOCAL_PATHS", "true")
    monkeypatch.setenv("APP_ENV", "production")

    with pytest.raises(DocumentResolutionError, match="requires ARGUS_ALLOWED_STORAGE_ROOTS"):
        with resolved_document(document):
            pass


def test_empty_roots_value_is_rejected(tmp_path, monkeypatch):
    document = tmp_path / "tender.txt"
    document.write_text("content")

    monkeypatch.setenv("ARGUS_ALLOWED_STORAGE_ROOTS", " , ")
    with pytest.raises(DocumentResolutionError, match="no usable path"):
        with resolved_document(document):
            pass


def test_configured_roots_still_contain_paths(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    inside = allowed / "doc.txt"
    inside.write_text("inside")
    outside = tmp_path / "secret.txt"
    outside.write_text("outside")

    monkeypatch.setenv("ARGUS_ALLOWED_STORAGE_ROOTS", str(allowed))

    with resolved_document(inside) as path:
        assert path.read_text() == "inside"

    with pytest.raises(DocumentResolutionError, match="escapes allowed storage roots"):
        with resolved_document(outside):
            pass

    # Traversal out of an allowed root is rejected after resolution, not before.
    traversal = allowed / ".." / "secret.txt"
    with pytest.raises(DocumentResolutionError, match="escapes allowed storage roots"):
        with resolved_document(traversal):
            pass
