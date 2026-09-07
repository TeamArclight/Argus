import pytest
from fastapi.testclient import TestClient
from app.core.config import Settings, settings
from app.db.session import DATABASE_URL, engine
from app.main import app
from app.schemas.canonical import VerificationMode, VerificationSource, VerificationStatus
from app.verification.adapters import (
    GSTVerificationAdapter,
    EPFOVerificationAdapter,
    ESICVerificationAdapter,
)


def test_settings_loading_and_cors():
    cfg = Settings(CORS_ALLOWED_ORIGINS="http://localhost:3000, http://example.com")
    assert cfg.get_cors_origins() == ["http://localhost:3000", "http://example.com"]

    cfg_list = Settings(CORS_ALLOWED_ORIGINS=["http://test.com"])
    assert cfg_list.get_cors_origins() == ["http://test.com"]


def test_database_url_single_source():
    assert DATABASE_URL == settings.DATABASE_URL
    assert engine.url.render_as_string(hide_password=False) == settings.DATABASE_URL


@pytest.mark.asyncio
async def test_no_simulated_mode_behavior():
    adapter = GSTVerificationAdapter()
    # Passing legacy "simulated_mode" should be ignored and mode should resolve to default (LIVE)
    resolved_mode = adapter.resolve_mode({"simulated_mode": "demo"})
    assert resolved_mode == VerificationMode.LIVE


@pytest.mark.asyncio
async def test_explicit_demo_behavior():
    adapter = GSTVerificationAdapter()
    resolved_mode = adapter.resolve_mode({"verification_mode": "demo"})
    assert resolved_mode == VerificationMode.DEMO

    res = await adapter.verify(
        {"id": "B1", "bidder_name": "ALPHA INFOTECH PVT LTD", "gstin": "27AAAAA0000A1Z5", "verification_mode": "demo"},
        "general.gstin",
    )
    assert res.status == VerificationStatus.VERIFIED
    assert res.mode == VerificationMode.DEMO


@pytest.mark.asyncio
async def test_unsupported_gst_document_result_and_provenance():
    adapter = GSTVerificationAdapter()
    # GST does not support DOCUMENT mode
    res = await adapter.verify(
        {"id": "B1", "gstin": "27AAAAA0000A1Z5", "verification_mode": "DOCUMENT"},
        "general.gstin",
    )
    assert res.status == VerificationStatus.UNAVAILABLE
    assert res.source == VerificationSource.SYSTEM_CONFIGURATION_ERROR
    assert res.source != VerificationSource.GST_DEMO_DATA
    assert "Unsupported verification mode" in res.error_message


def test_cors_middleware_headers():
    with TestClient(app) as client:
        # Test OPTIONS preflight request
        response = client.options(
            "/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"
        assert response.headers.get("access-control-allow-credentials") == "true"


def test_health_integrations_uses_actual_api_url_settings(monkeypatch):
    monkeypatch.setattr(settings, "GST_VERIFICATION_MODE", VerificationMode.LIVE)
    monkeypatch.setattr(settings, "GST_API_URL", "https://api.gst.gov.in/v1")
    monkeypatch.setattr(settings, "GST_API_KEY", "test-key")

    with TestClient(app) as client:
        resp = client.get("/health/integrations")
        assert resp.status_code == 200
        data = resp.json()
        assert data["gst"]["mode"] == "LIVE"
        assert data["gst"]["configured"] is True
        assert "GST API gateway configured" in data["gst"]["details"]


def test_health_domain_mode_support(monkeypatch):
    # Test 1: GST + DOCUMENT (Unsupported) -> configured=False
    monkeypatch.setattr(settings, "GST_VERIFICATION_MODE", VerificationMode.DOCUMENT)
    # Test 2: EPFO + DOCUMENT (Supported) -> configured=True
    monkeypatch.setattr(settings, "EPFO_VERIFICATION_MODE", VerificationMode.DOCUMENT)
    # Test 3: ESIC + DOCUMENT (Supported) -> configured=True
    monkeypatch.setattr(settings, "ESIC_VERIFICATION_MODE", VerificationMode.DOCUMENT)
    # Test 4: UDYAM + DEMO (Supported) -> configured=True
    monkeypatch.setattr(settings, "UDYAM_VERIFICATION_MODE", VerificationMode.DEMO)

    with TestClient(app) as client:
        resp = client.get("/health/integrations")
        assert resp.status_code == 200
        data = resp.json()

        # GST + DOCUMENT -> configured=false
        assert data["gst"]["mode"] == "DOCUMENT"
        assert data["gst"]["configured"] is False
        assert "DOCUMENT mode is unsupported for GST" in data["gst"]["details"]

        # EPFO + DOCUMENT -> configured=true
        assert data["epfo"]["mode"] == "DOCUMENT"
        assert data["epfo"]["configured"] is True

        # ESIC + DOCUMENT -> configured=true
        assert data["esic"]["mode"] == "DOCUMENT"
        assert data["esic"]["configured"] is True

        # UDYAM + DEMO -> configured=true
        assert data["udyam"]["mode"] == "DEMO"
        assert data["udyam"]["configured"] is True


def test_epfo_esic_provenance_by_mode():
    epfo_adapter = EPFOVerificationAdapter()
    esic_adapter = ESICVerificationAdapter()

    # EPFO PORTAL_CACHED source == EPFO_PORTAL_VERIFIED_CACHE
    epfo_portal_provider = epfo_adapter.get_provider(VerificationMode.PORTAL_CACHED)
    assert epfo_portal_provider.source == VerificationSource.EPFO_PORTAL_VERIFIED_CACHE

    # EPFO DOCUMENT source == EPFO_DOCUMENT_VERIFICATION
    epfo_doc_provider = epfo_adapter.get_provider(VerificationMode.DOCUMENT)
    assert epfo_doc_provider.source == VerificationSource.EPFO_DOCUMENT_VERIFICATION

    # ESIC PORTAL_CACHED source == ESIC_PORTAL_VERIFIED_CACHE
    esic_portal_provider = esic_adapter.get_provider(VerificationMode.PORTAL_CACHED)
    assert esic_portal_provider.source == VerificationSource.ESIC_PORTAL_VERIFIED_CACHE

    # ESIC DOCUMENT source == ESIC_DOCUMENT_VERIFICATION
    esic_doc_provider = esic_adapter.get_provider(VerificationMode.DOCUMENT)
    assert esic_doc_provider.source == VerificationSource.ESIC_DOCUMENT_VERIFICATION
