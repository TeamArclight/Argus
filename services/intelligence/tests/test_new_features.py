import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock
from pydantic import BaseModel, ConfigDict
from argus_ai.agents.workflow import build_argus_workflow, memory_checkpointer, build_workflow
from argus_ai.evaluation.runner import evaluate, EvaluationReport
from argus_ai.evaluation.metrics import structured_response_validity
from argus_ai.extraction.service import extract_document
from argus_ai.risk.service import detect_risk

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "gold_cases.json").read_text())


class SimpleSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    age: int


def test_workflow_with_real_services(tmp_path):
    """Invoke the workflow with real extraction on demo-like data."""
    tender_file = tmp_path / "tender.txt"
    tender_file.write_text("The bidder must have a valid GSTIN. Minimum turnover of INR 1,00,00,000.")
    bidder_file = tmp_path / "bidder.txt"
    bidder_file.write_text("GSTIN: 27ABCDE1234F1Z5. Annual turnover: INR 2,00,00,000.")

    # Without compliance backend, default returns REVIEW_REQUIRED → triggers interrupt.
    workflow_review = build_argus_workflow()
    result = workflow_review.invoke({
        "tender": {"document_uri": str(tender_file)},
        "document": {"document_uri": str(bidder_file), "document_id": "D1", "bidder_id": "B1"},
        "rag_query": {"query": "turnover requirement"},
    })
    assert len(result["requirements"]) > 0
    assert len(result["facts"]) > 0
    # Human interrupt fires correctly — no report yet (human must act first).
    assert result["compliance_evaluation"]["status"] == "REVIEW_REQUIRED"

    # With PASS compliance, workflow runs to report without interrupt.
    workflow_pass = build_argus_workflow(
        compliance_tool=lambda state: {"status": "PASS", "source": "test"},
    )
    result_pass = workflow_pass.invoke({
        "tender": {"document_uri": str(tender_file)},
        "document": {"document_uri": str(bidder_file), "document_id": "D1", "bidder_id": "B1"},
        "rag_query": {"query": "turnover requirement"},
    })
    assert "report" in result_pass
    assert result_pass["report"]["compliance_status"] == "PASS"


def test_workflow_checkpointing():
    """Verify workflow compiles and runs with a memory checkpointer."""
    checkpointer = memory_checkpointer()
    graph = build_workflow(
        extract_tender=lambda _: [],
        extract_document=lambda _: [],
        retrieve=lambda _: [],
        detect_risk=lambda _: [],
        compliance_tool=lambda _: {},
        report_tool=lambda _: {},
        checkpointer=checkpointer,
    )
    res = graph.invoke({"tender": {}}, config={"configurable": {"thread_id": "1"}})
    assert res is not None


def test_structured_response_validity():
    valid_resp = {"name": "Alice", "age": 30}
    invalid_resp = {"name": "Bob", "age": "thirty"}
    extra_field_resp = {"name": "Charlie", "age": 40, "extra": "yes"}

    responses = [valid_resp, invalid_resp, extra_field_resp, valid_resp]
    score = structured_response_validity(responses, SimpleSchema)
    assert score == 0.5


def test_evaluation_report_includes_recall_at_10():
    report = evaluate(
        expected_fields={"f1"},
        extracted_fields={"f1"},
        expected_evidence={"e1", "e2"},
        ranked_evidence=["e1", "e3", "e4", "e5", "e6", "e7", "e8", "e9", "e10", "e2"],
        cited_evidence=["e1"],
        expected_risks={"r1"},
        detected_risks={"r1"},
        answer_count=1,
        unsupported_answers=0,
        structured_responses=[{"name": "test", "age": 10}],
        schema_type=SimpleSchema,
    )
    assert report.retrieval_recall_at_10 == 1.0
    assert report.retrieval_recall_at_5 == 0.5
    assert report.structured_validity == 1.0


def test_gold_missing_requirement(tmp_path):
    """Verify extraction finds PAN but not GSTIN from a PAN-only document."""
    case = next(c for c in FIXTURES if c["id"] == "missing_requirement")
    path = tmp_path / "pan_only.txt"
    path.write_text(case["document_text"])
    facts = extract_document(path, document_id="D5", bidder_id="B1")
    extracted_fields = {f.field for f in facts}
    for expected in case["expected_fields"]:
        assert expected in extracted_fields, f"{expected} should be extracted"
    for missing in case.get("missing_fields", []):
        assert missing not in extracted_fields, f"{missing} should NOT be extracted"


def test_gold_true_identity_mismatch():
    """Verify ENTITY_NAME_MISMATCH fires for genuinely different names."""
    case = next(c for c in FIXTURES if c["id"] == "true_identity_mismatch")
    signals = detect_risk(
        claimed_entity_name=case["claimed_entity_name"],
        verified_entity_name=case["verified_entity_name"],
    )
    signal_types = {s.signal_type for s in signals}
    for expected in case["expected_signals"]:
        assert expected in signal_types, f"{expected} should fire"


def test_http_classify_and_risk_endpoints(tmp_path):
    """Verify the new /classify-document and /detect-risk HTTP endpoints work."""
    from fastapi.testclient import TestClient
    from argus_ai.http_service import create_app

    app = create_app()
    client = TestClient(app)

    gst_cert = tmp_path / "gst.txt"
    gst_cert.write_text("GSTIN: 07AABCA1234H1Z9\nPAN: AABCA1234H")

    # Test classify-document
    resp = client.post("/classify-document", json={"document_uri": str(gst_cert)})
    assert resp.status_code == 200
    data = resp.json()
    assert data["document_type"] == "GST_CERT"

    # Test detect-risk with expiry
    resp = client.post("/detect-risk", json={
        "expiry_date": "2020-01-01",
        "evidence_ids": ["E1"],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["signal_count"] >= 1
    assert data["has_high_severity"] is True
    assert any(s["signal_type"] == "EXPIRED_DOCUMENT" for s in data["signals"])

    # Test detect-risk with entity name mismatch
    resp = client.post("/detect-risk", json={
        "claimed_entity_name": "Alpha Tech Pvt Ltd",
        "verified_entity_name": "Zeta Solutions Limited",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert any(s["signal_type"] == "ENTITY_NAME_MISMATCH" for s in data["signals"])


def test_http_extract_document_low_confidence_flagging(tmp_path):
    """Verify /extract-document response includes review_required and low_confidence_fields."""
    from fastapi.testclient import TestClient
    from argus_ai.http_service import create_app

    app = create_app()
    client = TestClient(app)

    doc = tmp_path / "doc.txt"
    doc.write_text("GSTIN: 07AABCA1234H1Z9")

    resp = client.post("/extract-document", json={
        "document_id": "D1",
        "document_uri": str(doc),
        "bidder_id": "B1",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "review_required" in data
    assert "low_confidence_fields" in data
    assert isinstance(data["low_confidence_fields"], list)


def test_http_evaluate_bid_endpoint(tmp_path):
    """Verify POST /evaluate-bid runs the full LangGraph pipeline via HTTP."""
    from fastapi.testclient import TestClient
    from argus_ai.http_service import create_app

    app = create_app()
    client = TestClient(app)

    tender = tmp_path / "tender.txt"
    tender.write_text("The bidder must have a valid GSTIN. Minimum turnover of INR 1,00,00,000.")
    bidder = tmp_path / "bidder.txt"
    bidder.write_text("GSTIN: 27ABCDE1234F1Z5. Annual turnover: INR 2,00,00,000.")

    resp = client.post("/evaluate-bid", json={
        "tender_document_uri": str(tender),
        "bidder_document_uri": str(bidder),
        "document_id": "D1",
        "bidder_id": "B1",
        "rag_query": "turnover GST",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["requirements"]) > 0
    assert len(data["facts"]) > 0
    assert data["compliance_evaluation"]["status"] == "REVIEW_REQUIRED"
    assert data["interrupted"] is True
