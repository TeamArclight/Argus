from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from argus_ai.contracts import DocumentExtractionResponse, ExtractedFactDraft
from argus_ai.extraction.service import _parse_money_amount, extract_document
from argus_ai.http_service import create_app
from argus_ai.model_gateway.gateway import (
    ModelGateway,
    ModelProviderUnavailableError,
    TransientProviderError,
    is_transient_provider_error,
)


SURYATECH_FIXTURE_TEXT = """ARGUS - SYNTHETIC BIDDER SUBMISSION
SIH 2026 DEMO
Synthetic bidder dossier. Not issued by GSTN, MCA, Udyam, GeM, or any government authority.
Page 1
SYNTHETIC DEMO DATA - NON-AUTHORITATIVE - NOT A GOVERNMENT RECORD
SuryaTech Energy Solutions Pvt Ltd
Bidder Profile and Compliance Dossier | Intended Tender: GEM/2026/B/4521091 - Solar Grid Monitoring and Control System - Phase IV
1. Bidder Identification and Qualification Evidence
Field
Synthetic Bidder Evidence
Company Name
SuryaTech Energy Solutions Pvt Ltd
Bidder ID / Reference
SURYA-SOLAR-2026
GSTIN
29ABCDE5678K1Z1
PAN
ABCDE5678K
CIN
U40106KA2025PTC678901
UDYAM
UDYAM-KA-29-0067890
Average Annual Turnover
Rs. 11.6 crore (3-year average)
Relevant Experience
4 comparable solar/SCADA/grid-monitoring projects completed
OEM / Provider Authorization
Valid OEM authorization submitted
Technical Compliance
Secure telemetry, event logging and remote health monitoring supported
2. Bidder Declaration
The bidder submits the above synthetic statutory identifiers and qualification evidence for ARGUS demonstration processing.
"""


def _create_temp_doc(content: str, suffix: str = ".txt") -> Path:
    with tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False, encoding="utf-8") as f:
        f.write(content)
        return Path(f.name)


# ---------------------------------------------------------------------------
# 1. Money Normalization Tests
# ---------------------------------------------------------------------------

def test_money_normalization_canonical_inr():
    assert _parse_money_amount("Rs. 11.6 crore") == 116000000
    assert _parse_money_amount("INR 11.6 Crore") == 116000000
    assert _parse_money_amount("₹11.6 crore") == 116000000
    assert _parse_money_amount("116000000") == 116000000
    assert _parse_money_amount("Rs. 50,00,000") == 5000000
    assert _parse_money_amount("5 crore INR") == 50000000
    assert _parse_money_amount("invalid money text") is None


# ---------------------------------------------------------------------------
# 2. Model Success Test
# ---------------------------------------------------------------------------

def test_model_success_returns_model_facts():
    doc_path = _create_temp_doc(SURYATECH_FIXTURE_TEXT)
    try:
        mock_provider = MagicMock()
        mock_provider.structured.return_value = {
            "facts": [
                {
                    "document_id": "temp_doc",
                    "bidder_id": "temp_bidder",
                    "field": "tax.gstin",
                    "value": "29ABCDE5678K1Z1",
                    "source_page": 1,
                    "confidence": 0.99,
                    "provider": "gemini",
                    "model": "gemini-3.6-flash",
                }
            ]
        }
        gw = ModelGateway(mock_provider)

        facts = extract_document(doc_path, document_id="doc_1", bidder_id="bid_1", gateway=gw)
        assert len(facts) == 1
        assert facts[0].field == "tax.gstin"
        assert facts[0].value == "29ABCDE5678K1Z1"
        assert facts[0].provider == "gemini"
        assert facts[0].model == "gemini-3.6-flash"
        assert mock_provider.structured.call_count == 1
    finally:
        if doc_path.exists():
            doc_path.unlink()


# ---------------------------------------------------------------------------
# 3. Transient Provider Failures (503, 429, Timeout) -> Fallback
# ---------------------------------------------------------------------------

def test_transient_503_retries_and_falls_back():
    doc_path = _create_temp_doc(SURYATECH_FIXTURE_TEXT)
    try:
        mock_provider = MagicMock()
        transient_err = ConnectionError("503 Server Unavailable")
        mock_provider.structured.side_effect = transient_err

        gw = ModelGateway(mock_provider)
        facts = extract_document(doc_path, document_id="doc_1", bidder_id="bid_1", gateway=gw)

        assert mock_provider.structured.call_count == 1
        assert len(facts) > 0
        assert all(f.provider == "DETERMINISTIC_FALLBACK" for f in facts)
        assert all(f.model == "deterministic-rules-v1" for f in facts)

        fact_fields = {f.field: f.value for f in facts}
        assert fact_fields.get("tax.gstin") == "29ABCDE5678K1Z1"
        assert fact_fields.get("identity.pan") == "ABCDE5678K"
        assert fact_fields.get("corporate.cin") == "U40106KA2025PTC678901"
        assert fact_fields.get("registration.udyam") == "UDYAM-KA-29-0067890"
        assert fact_fields.get("financial.average_annual_turnover") == 116000000
    finally:
        if doc_path.exists():
            doc_path.unlink()


def test_transient_429_retries_and_falls_back():
    doc_path = _create_temp_doc(SURYATECH_FIXTURE_TEXT)
    try:
        mock_provider = MagicMock()
        mock_provider.structured.side_effect = ConnectionError("429 Resource Exhausted")

        gw = ModelGateway(mock_provider)
        facts = extract_document(doc_path, document_id="doc_1", bidder_id="bid_1", gateway=gw)

        assert len(facts) > 0
        assert any(f.field == "tax.gstin" for f in facts)
        assert all(f.provider == "DETERMINISTIC_FALLBACK" for f in facts)
    finally:
        if doc_path.exists():
            doc_path.unlink()


def test_timeout_retries_and_falls_back():
    doc_path = _create_temp_doc(SURYATECH_FIXTURE_TEXT)
    try:
        mock_provider = MagicMock()
        mock_provider.structured.side_effect = TimeoutError("Request timed out")

        gw = ModelGateway(mock_provider)
        facts = extract_document(doc_path, document_id="doc_1", bidder_id="bid_1", gateway=gw)

        assert len(facts) > 0
        assert all(f.provider == "DETERMINISTIC_FALLBACK" for f in facts)
    finally:
        if doc_path.exists():
            doc_path.unlink()


# ---------------------------------------------------------------------------
# 4. Non-Transient Programming Errors Must NOT Be Silently Swallowed
# ---------------------------------------------------------------------------

def test_non_transient_error_is_raised():
    doc_path = _create_temp_doc(SURYATECH_FIXTURE_TEXT)
    try:
        mock_provider = MagicMock()
        mock_provider.structured.side_effect = TypeError("Programming error inside provider")

        gw = ModelGateway(mock_provider)
        with pytest.raises(TypeError, match="Programming error inside provider"):
            extract_document(doc_path, document_id="doc_1", bidder_id="bid_1", gateway=gw)

        assert mock_provider.structured.call_count == 1
    finally:
        if doc_path.exists():
            doc_path.unlink()


# ---------------------------------------------------------------------------
# 5. Experience Semantics: Project Counts Are NOT Experience Years
# ---------------------------------------------------------------------------

def test_project_count_not_misused_as_experience_years():
    text_with_projects = """
    Bidder Qualification Evidence
    Relevant Experience: 4 comparable solar/SCADA/grid-monitoring projects completed.
    """
    doc_path = _create_temp_doc(text_with_projects)
    try:
        facts = extract_document(doc_path, document_id="doc_1", bidder_id="bid_1", gateway=None)
        exp_facts = [f for f in facts if f.field == "experience.years"]
        assert len(exp_facts) == 0, "Project count must NOT be converted into experience.years"
    finally:
        if doc_path.exists():
            doc_path.unlink()

    text_with_explicit_years = """
    Bidder Qualification Evidence
    The contractor has completed 5 years of relevant experience in solar power projects.
    """
    doc_path_years = _create_temp_doc(text_with_explicit_years)
    try:
        facts = extract_document(doc_path_years, document_id="doc_1", bidder_id="bid_1", gateway=None)
        exp_facts = [f for f in facts if f.field == "experience.years"]
        assert len(exp_facts) == 1
        assert exp_facts[0].value == 5
    finally:
        if doc_path_years.exists():
            doc_path_years.unlink()


# ---------------------------------------------------------------------------
# 6. Fallback Zero Facts -> ModelProviderUnavailableError / 503
# ---------------------------------------------------------------------------

def test_fallback_zero_facts_raises_provider_unavailable():
    unusable_text = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Nulla facilisi."
    doc_path = _create_temp_doc(unusable_text)
    try:
        mock_provider = MagicMock()
        mock_provider.structured.side_effect = TimeoutError("Timed out")
        gw = ModelGateway(mock_provider)

        with pytest.raises(ModelProviderUnavailableError):
            extract_document(doc_path, document_id="doc_1", bidder_id="bid_1", gateway=gw)
    finally:
        if doc_path.exists():
            doc_path.unlink()


# ---------------------------------------------------------------------------
# 7. HTTP Endpoint Integration Tests
# ---------------------------------------------------------------------------

def test_http_endpoint_fallback_marks_review_required(monkeypatch):
    monkeypatch.setenv("ARGUS_ALLOW_ANONYMOUS_INTELLIGENCE", "true")
    monkeypatch.setenv("APP_ENV", "local")

    mock_provider = MagicMock()
    mock_provider.structured.side_effect = ConnectionError("503 Service Unavailable")
    mock_gw = ModelGateway(mock_provider)

    from argus_ai import http_service
    monkeypatch.setattr(http_service, "configured_gateway", lambda: mock_gw)

    client = TestClient(create_app())
    doc_path = _create_temp_doc(SURYATECH_FIXTURE_TEXT)
    try:
        import base64, hashlib
        content_bytes = doc_path.read_bytes()
        sha256 = hashlib.sha256(content_bytes).hexdigest()
        b64 = base64.b64encode(content_bytes).decode("utf-8")

        resp = client.post(
            "/extract-document",
            json={
                "contract_version": "1.0",
                "request_id": "test-req-001",
                "document_id": "doc_123",
                "bidder_id": "bidder_456",
                "filename": "suryatech.txt",
                "document_sha256": sha256,
                "file_bytes_base64": b64,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "COMPLETED"
        assert data["review_required"] is True, "Fallback extraction must flag review_required"
        assert data["fallback_used"] is True
        assert data["provider_model"] == "DETERMINISTIC_FALLBACK"

        facts = data["facts"]
        assert len(facts) > 0
        for f in facts:
            assert f["metadata_json"]["provider"] == "DETERMINISTIC_FALLBACK"
            assert f["metadata_json"]["fallback_used"] is True
    finally:
        if doc_path.exists():
            doc_path.unlink()


def test_http_endpoint_zero_facts_returns_503(monkeypatch):
    monkeypatch.setenv("ARGUS_ALLOW_ANONYMOUS_INTELLIGENCE", "true")
    monkeypatch.setenv("APP_ENV", "local")

    mock_provider = MagicMock()
    mock_provider.structured.side_effect = ConnectionError("503 Service Unavailable")
    mock_gw = ModelGateway(mock_provider)

    from argus_ai import http_service
    monkeypatch.setattr(http_service, "configured_gateway", lambda: mock_gw)

    client = TestClient(create_app())
    doc_path = _create_temp_doc("No statutory facts or identifiers whatsoever.")
    try:
        import base64, hashlib
        content_bytes = doc_path.read_bytes()
        sha256 = hashlib.sha256(content_bytes).hexdigest()
        b64 = base64.b64encode(content_bytes).decode("utf-8")

        resp = client.post(
            "/extract-document",
            json={
                "contract_version": "1.0",
                "request_id": "test-req-002",
                "document_id": "doc_empty",
                "bidder_id": "bidder_empty",
                "filename": "empty.txt",
                "document_sha256": sha256,
                "file_bytes_base64": b64,
            },
        )
        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert detail.get("error_code") == "MODEL_PROVIDER_UNAVAILABLE"
    finally:
        if doc_path.exists():
            doc_path.unlink()


def test_extract_tender_model_provider_unavailable_returns_503(monkeypatch):
    monkeypatch.setenv("ARGUS_ALLOW_ANONYMOUS_INTELLIGENCE", "true")
    monkeypatch.setenv("APP_ENV", "local")

    mock_provider = MagicMock()
    mock_provider.structured.side_effect = ConnectionError("503 Service Unavailable")
    mock_gw = ModelGateway(mock_provider)

    from argus_ai import http_service
    monkeypatch.setattr(http_service, "configured_gateway", lambda: mock_gw)

    client = TestClient(create_app())
    doc_path = _create_temp_doc("RFP Tender Document without deterministic patterns.")
    try:
        import base64, hashlib
        content_bytes = doc_path.read_bytes()
        sha256 = hashlib.sha256(content_bytes).hexdigest()
        b64 = base64.b64encode(content_bytes).decode("utf-8")

        resp = client.post(
            "/extract-tender",
            json={
                "contract_version": "1.0",
                "request_id": "test-req-tender-001",
                "tender_id": "tender_123",
                "document_id": "doc_tender_123",
                "filename": "tender.txt",
                "document_sha256": sha256,
                "file_bytes_base64": b64,
            },
        )
        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert detail.get("error_code") == "MODEL_PROVIDER_UNAVAILABLE"
    finally:
        if doc_path.exists():
            doc_path.unlink()


def test_extract_tender_success_returns_completed(monkeypatch):
    monkeypatch.setenv("ARGUS_ALLOW_ANONYMOUS_INTELLIGENCE", "true")
    monkeypatch.setenv("APP_ENV", "local")

    from argus_ai.contracts import TenderRequirementDraft, RequirementType, Operator
    mock_provider = MagicMock()
    mock_provider.structured.return_value = {
        "requirements": [
            {
                "clause": "2.1",
                "requirement_type": "GST",
                "field": "tax.gstin",
                "operator": "EXISTS",
                "expected_value": True,
                "confidence": 0.95,
                "mandatory": True,
            }
        ]
    }
    mock_gw = ModelGateway(mock_provider)

    from argus_ai import http_service
    monkeypatch.setattr(http_service, "configured_gateway", lambda: mock_gw)

    client = TestClient(create_app())
    doc_path = _create_temp_doc("Clause 2.1 GSTIN is mandatory.")
    try:
        import base64, hashlib
        content_bytes = doc_path.read_bytes()
        sha256 = hashlib.sha256(content_bytes).hexdigest()
        b64 = base64.b64encode(content_bytes).decode("utf-8")

        resp = client.post(
            "/extract-tender",
            json={
                "contract_version": "1.0",
                "request_id": "test-req-tender-002",
                "tender_id": "tender_123",
                "document_id": "doc_tender_123",
                "filename": "tender.txt",
                "document_sha256": sha256,
                "file_bytes_base64": b64,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "COMPLETED"
        assert len(data["requirements"]) == 1
        assert data["requirements"][0]["clause"] == "2.1"
        assert data["requirements"][0]["field"] == "tax.gstin"
    finally:
        if doc_path.exists():
            doc_path.unlink()


def test_extract_tender_fallback_model_provenance_reported(monkeypatch):
    monkeypatch.setenv("ARGUS_ALLOW_ANONYMOUS_INTELLIGENCE", "true")
    monkeypatch.setenv("APP_ENV", "local")

    mock_provider = MagicMock()
    mock_provider.last_model_used = "gemini-3.1-flash-lite"
    mock_provider.structured.return_value = {
        "requirements": [
            {
                "clause": "3.1",
                "requirement_type": "TURNOVER",
                "field": "financial.average_annual_turnover",
                "operator": "GTE",
                "expected_value": 50000000,
                "confidence": 0.90,
                "mandatory": True,
            }
        ]
    }
    mock_gw = ModelGateway(mock_provider)
    mock_gw.model_name = "gemini-3.6-flash"

    from argus_ai import http_service
    monkeypatch.setattr(http_service, "configured_gateway", lambda: mock_gw)

    client = TestClient(create_app())
    doc_path = _create_temp_doc("Clause 3.1 Minimum turnover 50000000.")
    try:
        import base64, hashlib
        content_bytes = doc_path.read_bytes()
        sha256 = hashlib.sha256(content_bytes).hexdigest()
        b64 = base64.b64encode(content_bytes).decode("utf-8")

        resp = client.post(
            "/extract-tender",
            json={
                "contract_version": "1.0",
                "request_id": "test-req-tender-fallback-001",
                "tender_id": "tender_123",
                "document_id": "doc_tender_123",
                "filename": "tender.txt",
                "document_sha256": sha256,
                "file_bytes_base64": b64,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "COMPLETED"
        assert data["provider_model"] == "gemini-3.1-flash-lite"
        assert data["requirements"][0]["metadata_json"]["provider_model"] == "gemini-3.1-flash-lite"
    finally:
        if doc_path.exists():
            doc_path.unlink()


def test_gemini_provider_fast_429_fallback_and_provenance(monkeypatch):
    monkeypatch.setenv("ARGUS_GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("ARGUS_MODEL_NAME", "gemini-3.6-flash")

    from argus_ai.model_gateway.gateway import GeminiProvider

    mock_client = MagicMock()
    call_models = []

    def mock_generate_content(model, contents, config):
        call_models.append(model)
        if model == "gemini-3.6-flash":
            err = Exception("429 RESOURCE_EXHAUSTED")
            setattr(err, "code", 429)
            raise err
        resp = MagicMock()
        resp.text = '{"answer": "fallback success", "cited_evidence_ids": []}'
        return resp

    mock_client.models.generate_content.side_effect = mock_generate_content

    provider = GeminiProvider.__new__(GeminiProvider)
    provider._client = mock_client
    provider._model = "gemini-3.6-flash"
    provider.last_model_used = "gemini-3.6-flash"

    result = provider.structured("test prompt", {"type": "object"})
    assert result == {"answer": "fallback success", "cited_evidence_ids": []}
    assert provider.last_model_used == "gemini-3.1-flash-lite"
    assert call_models.count("gemini-3.6-flash") == 1
    assert call_models == ["gemini-3.6-flash", "gemini-3.1-flash-lite"]


def test_stream_ended_pdf_parsing_recovery(tmp_path):
    stub_file = tmp_path / "test_stub.pdf"
    stub_file.write_bytes(b"%PDF-1.4 dummy test content for deletion verification")

    from argus_ai.parsing.service import parse_document
    pages = parse_document(stub_file)
    assert len(pages) == 1
    assert "dummy test content for deletion verification" in pages[0][1]
