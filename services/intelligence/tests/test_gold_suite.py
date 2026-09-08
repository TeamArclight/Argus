import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from argus_ai.evaluation.runner import evaluate
from argus_ai.extraction.service import extract_document
from argus_ai.rag.service import InMemoryRAG
from argus_ai.risk.service import detect_risk
from argus_ai.risk.context import detect_fact_risks

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "gold_cases.json").read_text())

def test_gold_clean_bidder_extraction(tmp_path):
    case = next(item for item in FIXTURES if item["id"] == "clean_bidder")
    path = tmp_path / "gst.txt"; path.write_text(case["document_text"])
    assert {fact.field for fact in extract_document(path, document_id="D1", bidder_id="B1")} == set(case["expected_fields"])

def test_gold_obsolete_policy_is_never_retrieved():
    rag = InMemoryRAG()
    rag.index("current-policy", "Current", "turnover exemption applies", effective_to=datetime.now(timezone.utc) + timedelta(days=1))
    rag.index("obsolete-policy", "Old", "turnover exemption applies", effective_to=datetime.now(timezone.utc) - timedelta(days=1))
    result = rag.retrieve("turnover exemption")
    assert [item.entity_id for item in result] == ["current-policy"]

def test_gold_prompt_injection_is_document_content_not_instruction(tmp_path):
    case = next(item for item in FIXTURES if item["id"] == "prompt_injection_document")
    path = tmp_path / "injection.txt"; path.write_text(case["document_text"])
    assert extract_document(path, document_id="D2", bidder_id="B1") == []

def test_evaluation_report_metrics():
    report = evaluate(expected_fields={"gstin"}, extracted_fields={"gstin"}, expected_evidence={"E1"}, ranked_evidence=["E1"], cited_evidence=["E1"], expected_risks={"CLAIM_VERIFICATION_MISMATCH"}, detected_risks={"CLAIM_VERIFICATION_MISMATCH"}, answer_count=2, unsupported_answers=0)
    assert report.extraction_recall == report.retrieval_recall_at_5 == report.citation_precision == 1

def test_document_extraction_turnover_expiry_and_registration(tmp_path):
    path = tmp_path / "certificate.txt"
    path.write_text("UDYAM-MH-12-1234567. Annual turnover: INR 1,20,00,000. Valid up to: 31/12/2027.")
    facts = extract_document(path, document_id="D3", bidder_id="B1")
    by_field = {fact.field: fact.value for fact in facts}
    assert by_field["registration.udyam"] == "UDYAM-MH-12-1234567"
    assert by_field["financial.average_annual_turnover"] == 12000000
    assert by_field["document.expiry_date"] == "2027-12-31"

def test_csv_extraction_and_fact_risk_context(tmp_path):
    path = tmp_path / "facts.csv"; path.write_text("gstin,expiry_date\n27ABCDE1234F1Z5,01/01/2020\n29ABCDE1234F1Z5,01/01/2020\n")
    facts = extract_document(path, document_id="D4", bidder_id="B1")
    types = {signal.signal_type for signal in detect_fact_risks(facts)}
    assert "IDENTIFIER_DISAGREEMENT" in types and "EXPIRED_DOCUMENT" in types
