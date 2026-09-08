from argus_ai.contracts import ExtractedFactDraft
from argus_ai.rag.service import InMemoryRAG
from argus_ai.risk.service import detect_risk
from argus_ai.http_service import create_app
from fastapi.testclient import TestClient
from argus_ai.rag.ingestion import ingest_document
from datetime import date, datetime, timedelta, timezone
from argus_ai.extraction.service import extract_tender
from argus_ai import classify_document, extract_bidder_document, extract_tender as public_extract_tender

def test_fact_requires_valid_confidence():
    fact = ExtractedFactDraft(document_id="d", bidder_id="b", field="gstin", value="x", confidence=.9, provider="mock", model="mock")
    assert fact.model_dump()["confidence"] == .9

def test_rag_filters_expired_evidence():
    rag = InMemoryRAG(); rag.index("policy-1", "Policy", "GST registration is mandatory", tender_id="T1")
    assert rag.retrieve("GST mandatory", {"tender_id": "T1"})[0].entity_id == "policy-1"

def test_rag_filters_future_evidence_and_preserves_source_uri():
    rag = InMemoryRAG()
    future = datetime.now(timezone.utc) + timedelta(days=1)
    rag.index("future", "Policy", "GST registration is mandatory", source_uri="s3://policy/current.txt", effective_from=future)
    assert rag.retrieve("GST mandatory") == []
    current = rag.index("current", "Policy", "GST registration is mandatory", source_uri="s3://policy/current.txt")[0]
    assert current.source_uri == "s3://policy/current.txt"

def test_public_package_entry_points_delegate(tmp_path):
    document = tmp_path / "gst.txt"
    document.write_text("GSTIN 22AAAAA0000A1Z5")
    assert public_extract_tender(document)[0].requirement_type.value == "GST"
    assert classify_document(document).document_type.value == "GST_CERT"
    assert extract_bidder_document(str(document), document_id="D1", bidder_id="B1")[0].document_id == "D1"

def test_duplicate_and_claim_mismatch_are_risks():
    payload = b"certificate"; digest = __import__("hashlib").sha256(payload).hexdigest()
    assert len(detect_risk(document_bytes=payload, prior_hashes={digest}, claim_value="A", verified_value="B")) == 2

def test_http_extraction_and_rag_boundary(tmp_path):
    tender = tmp_path / "tender.txt"
    tender.write_text("Minimum annual turnover shall be INR 1,00,00,000.")
    client = TestClient(create_app())
    result = client.post("/extract-tender", json={"tender_id": "T1", "document_uri": str(tender)})
    assert result.status_code == 200
    requirement = result.json()["requirements"][0]
    assert set(requirement) == {"clause", "requirement_type", "field", "operator", "expected_value", "unit", "mandatory", "source_page", "source_text", "confidence", "requires_verification"}
    rag = client.post("/rag-query", json={"query": "GST"})
    assert rag.status_code == 200 and rag.json()["error_code"] == "INSUFFICIENT_EVIDENCE"
    assert client.get("/health").json()["checks"]["document_resolution"]["raw_s3_requires_backend_storage_adapter"] is True

def test_http_boundary_enforces_configured_api_key(monkeypatch):
    monkeypatch.setenv("ARGUS_INTELLIGENCE_API_KEY", "test-key")
    client = TestClient(create_app())
    assert client.get("/health").status_code == 401
    assert client.get("/health", headers={"Authorization": "Bearer test-key"}).status_code == 200

def test_http_rag_ingestion_indexes_and_retrieves_evidence(tmp_path):
    source = tmp_path / "policy.txt"
    source.write_text("GST registration is mandatory for every bidder.")
    client = TestClient(create_app())
    result = client.post("/rag-ingest", json={"document_id": "P1", "title": "GFR", "document_uri": str(source), "document_type": "POLICY", "source_uri": "file:///policy.txt"})
    assert result.status_code == 200
    assert result.json()["chunks_indexed"] == 1
    query = client.post("/rag-query", json={"query": "GST mandatory"})
    assert query.status_code == 200
    assert query.json()["results"][0]["source_uri"] == "file:///policy.txt"

def test_ingestion_keeps_page_and_policy_metadata(tmp_path):
    source = tmp_path / "policy.txt"; source.write_text("Current policy text")
    rag = InMemoryRAG()
    chunks = ingest_document(source, document_id="P1", title="GFR", document_type="POLICY", indexer=rag, version="2026", tender_id="T1")
    assert chunks[0].page_number == 1
    assert chunks[0].location_metadata["tender_id"] == "T1"
    assert chunks[0].version == "2026"

def test_explainable_extended_risk_signals():
    signals = detect_risk(claimed_entity_name="Argus Systems Pvt Ltd", verified_entity_name="Other Supplier", identifiers={"gstin": {"A", "B"}}, expiry_date=date.today() - timedelta(days=1), document_text="same certificate words", prior_document_texts={"D0": "same certificate words"}, oem_authorization_hash="x", prior_oem_authorization_hashes={"x"}, facts_by_field={"gstin": [("F1", "A"), ("F2", "B")]}, evidence_ids=["D1"])
    assert {signal.signal_type for signal in signals} == {"ENTITY_NAME_MISMATCH", "IDENTIFIER_DISAGREEMENT", "EXPIRED_DOCUMENT", "DUPLICATE_OEM_AUTHORIZATION", "SUSPICIOUS_DOCUMENT_SIMILARITY", "CONTRADICTORY_FACTS"}
    assert all(signal.evidence_ids for signal in signals)

def test_tender_extraction_emits_explicit_canonical_requirements(tmp_path):
    tender = tmp_path / "tender.txt"
    tender.write_text("4.2 GST registration and Udyam are mandatory. Bidder must not be blacklisted. Minimum experience of 3 years is required.")
    requirements = extract_tender(tender)
    assert {(item.requirement_type.value, item.field, item.operator.value) for item in requirements} == {("GST", "tax.gstin", "EXISTS"), ("UDYAM", "registration.udyam", "EXISTS"), ("BLACK_LIST", "legal.blacklisted", "EQ"), ("EXPERIENCE", "experience.years", "GTE")}
