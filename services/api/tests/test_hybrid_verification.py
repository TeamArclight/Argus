import httpx
import pytest
from fastapi.testclient import TestClient
from app.core.config import settings
from app.db.session import Base, engine
from app.main import app
from app.schemas.canonical import (
    VerificationMode,
    VerificationSource,
    VerificationStatus,
)
from app.verification.adapters import (
    EPFOVerificationAdapter,
    ESICVerificationAdapter,
    GSTVerificationAdapter,
    MCAVerificationAdapter,
)
from app.verification.providers.document_providers import DocumentVerificationProvider
from app.verification.providers.live_providers import GenericLiveProvider
from app.verification.providers.portal_cached_providers import PortalCachedProvider


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.mark.asyncio
async def test_live_provider_unconfigured_returns_unavailable():
    provider = GenericLiveProvider("gst", VerificationSource.GST_AUTHORIZED_API, api_base_url=None, api_key=None)
    res = await provider.verify({"id": "B1", "gstin": "27AAAAA0000A1Z5"}, "general.gstin")
    assert res.status == VerificationStatus.UNAVAILABLE
    assert res.mode == VerificationMode.LIVE
    assert res.source == VerificationSource.GST_AUTHORIZED_API
    assert "Live GST provider is not configured" in res.error_message


@pytest.mark.asyncio
async def test_live_provider_http_200_success(monkeypatch):
    # Mock httpx.AsyncClient.get for live API call
    async def mock_get(self, url, headers=None, params=None):
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            json={
                "match": True,
                "verified_value": {"gstin": "27AAAAA0000A1Z5", "legal_name": "Acme Corp"},
                "reference": "LIVE-GST-REF-10023",
            },
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    provider = GenericLiveProvider(
        "gst",
        VerificationSource.GST_AUTHORIZED_API,
        api_base_url="https://api.gst.gov.in",
        api_key="secret-live-key",
    )
    res = await provider.verify({"id": "B1", "gstin": "27AAAAA0000A1Z5"}, "general.gstin")

    assert res.status == VerificationStatus.VERIFIED
    assert res.mode == VerificationMode.LIVE
    assert res.source == VerificationSource.GST_AUTHORIZED_API
    assert res.verification_reference == "LIVE-GST-REF-10023"


@pytest.mark.asyncio
async def test_live_provider_unauthorized_401(monkeypatch):
    async def mock_get_401(self, url, headers=None, params=None):
        request = httpx.Request("GET", url)
        return httpx.Response(401, json={"error": "Invalid API key"}, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get_401)

    provider = GenericLiveProvider(
        "gst",
        VerificationSource.GST_AUTHORIZED_API,
        api_base_url="https://api.gst.gov.in",
        api_key="bad-key",
    )
    res = await provider.verify({"id": "B1", "gstin": "27AAAAA0000A1Z5"}, "general.gstin")

    assert res.status == VerificationStatus.SERVICE_ERROR
    assert res.mode == VerificationMode.LIVE
    assert "Unauthorized" in res.error_message


@pytest.mark.asyncio
async def test_live_provider_timeout(monkeypatch):
    async def mock_get_timeout(self, url, headers=None, params=None):
        raise httpx.TimeoutException("Timed out connecting to remote host")

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get_timeout)

    provider = GenericLiveProvider(
        "gst",
        VerificationSource.GST_AUTHORIZED_API,
        api_base_url="https://api.gst.gov.in",
        api_key="valid-key",
    )
    res = await provider.verify({"id": "B1", "gstin": "27AAAAA0000A1Z5"}, "general.gstin")

    assert res.status == VerificationStatus.TIMEOUT
    assert res.mode == VerificationMode.LIVE
    assert "timed out" in res.error_message


@pytest.mark.asyncio
async def test_portal_cached_provider_found_record():
    provider = PortalCachedProvider("gst", VerificationSource.GST_PORTAL_VERIFIED_CACHE)
    res = await provider.verify(
        {"id": "B1", "bidder_name": "Bharat Cybernetics Pvt Ltd", "gstin": "27AAAAA0000A1Z5"},
        "general.gstin",
    )

    assert res.status == VerificationStatus.VERIFIED
    assert res.mode == VerificationMode.PORTAL_CACHED
    assert res.source == VerificationSource.GST_PORTAL_VERIFIED_CACHE
    assert res.verification_reference == "GST-CACHE-27AAAAA0000A1Z5"


@pytest.mark.asyncio
async def test_portal_cached_provider_missing_record():
    provider = PortalCachedProvider("gst", VerificationSource.GST_PORTAL_VERIFIED_CACHE)
    res = await provider.verify(
        {"id": "B1", "bidder_name": "Unknown Entity", "gstin": "99NONEXISTENT000"},
        "general.gstin",
    )

    assert res.status == VerificationStatus.UNVERIFIED
    assert res.mode == VerificationMode.PORTAL_CACHED
    assert "No portal cache record found" in res.error_message


@pytest.mark.asyncio
async def test_demo_provider_flagship_alpha():
    adapter = GSTVerificationAdapter()
    res = await adapter.verify(
        {"id": "B1", "bidder_name": "ALPHA INFOTECH PVT LTD", "gstin": "27AAAAA0000A1Z5", "simulated_mode": "demo"},
        "general.gstin",
    )

    assert res.status == VerificationStatus.VERIFIED
    assert res.mode == VerificationMode.DEMO
    assert res.source == VerificationSource.GST_DEMO_DATA
    assert res.source != VerificationSource.GST_AUTHORIZED_API


@pytest.mark.asyncio
async def test_demo_provider_flagship_bharat_turnover_mismatch():
    adapter = GSTVerificationAdapter()
    res = await adapter.verify(
        {"id": "B1", "bidder_name": "BHARAT CYBERNETICS PVT LTD", "simulated_mode": "demo"},
        "financial.average_annual_turnover",
    )

    assert res.status == VerificationStatus.MISMATCH
    assert res.mode == VerificationMode.DEMO
    assert res.source == VerificationSource.GST_DEMO_DATA
    assert res.verified_value == 85000000


@pytest.mark.asyncio
async def test_demo_provider_flagship_crest_debarred():
    adapter = GSTVerificationAdapter()
    res = await adapter.verify(
        {"id": "B1", "bidder_name": "CREST LOGISTICS PVT LTD", "simulated_mode": "demo"},
        "debarment.status",
    )

    assert res.status == VerificationStatus.MISMATCH
    assert res.mode == VerificationMode.DEMO
    assert res.error_message is not None


@pytest.mark.asyncio
async def test_document_epfo_provider():
    provider = DocumentVerificationProvider("epfo", VerificationSource.EPFO_DOCUMENT_VERIFICATION)
    bidder_data = {
        "id": "B1",
        "bidder_name": "Bharat Cybernetics Pvt Ltd",
        "pan": "ABCDE1234F",
        "document_facts": {
            "establishment_code": "MH/BAN/0012345/000",
            "payment_status": "PAID",
            "active_subscribers": 142,
            "source_doc_id": "DOC-EPFO-ECR-2026",
            "page_number": 2,
        },
    }
    res = await provider.verify(bidder_data, "general.epfo")

    assert res.status == VerificationStatus.VERIFIED
    assert res.mode == VerificationMode.DOCUMENT
    assert res.source == VerificationSource.EPFO_DOCUMENT_VERIFICATION
    assert res.verified_value["document_verified"] is True
    assert res.verified_value["source_doc_id"] == "DOC-EPFO-ECR-2026"


def test_health_integrations_endpoint():
    with TestClient(app) as client:
        resp = client.get("/health/integrations")
        assert resp.status_code == 200
        data = resp.json()

        assert "gst" in data
        assert "udyam" in data
        assert "mca" in data
        assert "epfo" in data
        assert "esic" in data
        assert "blacklist" in data
        assert "intelligence" in data

        # Default mode is LIVE
        assert data["gst"]["mode"] == "LIVE"
        assert data["epfo"]["mode"] == "DOCUMENT"
        assert data["esic"]["mode"] == "DOCUMENT"
