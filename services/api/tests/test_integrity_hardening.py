import pytest
from fastapi.testclient import TestClient
from app.core.config import Settings, settings
from app.db.session import DATABASE_URL, engine
from app.main import app
from app.schemas.canonical import VerificationMode, VerificationStatus
from app.verification.adapters import (
    GSTVerificationAdapter,
    EPFOVerificationAdapter,
)


def test_settings_loading_and_cors():
    cfg = Settings(CORS_ALLOWED_ORIGINS="http://localhost:3000, http://example.com")
    assert cfg.get_cors_origins() == ["http://localhost:3000", "http://example.com"]

    cfg_list = Settings(CORS_ALLOWED_ORIGINS=["http://test.com"])
    assert cfg_list.get_cors_origins() == ["http://test.com"]


def test_database_url_single_source():
    assert DATABASE_URL == settings.DATABASE_URL
    assert str(engine.url) == settings.DATABASE_URL


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
async def test_unsupported_mode_rejection():
    adapter = GSTVerificationAdapter()
    # GST does not support DOCUMENT mode
    res = await adapter.verify(
        {"id": "B1", "gstin": "27AAAAA0000A1Z5", "verification_mode": "DOCUMENT"},
        "general.gstin",
    )
    assert res.status == VerificationStatus.UNAVAILABLE
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
