from __future__ import annotations
import re
import csv
from datetime import datetime
from pathlib import Path
from typing import Union
from ..contracts import (DocumentClassification, DocumentType, ExtractedFactDraft,
                         Operator, RequirementType, TenderRequirementDraft)
from ..parsing.service import parse_document
from ..model_gateway.gateway import ModelGateway
from ..contracts import TenderExtractionResponse, DocumentExtractionResponse

_CLASSIFIERS = ((r"gstin|goods and services tax", DocumentType.GST_CERT), (r"udyam", DocumentType.UDYAM_CERT), (r"permanent account number|\bpan\b", DocumentType.PAN_CERT), (r"turnover|financial statement", DocumentType.TURNOVER_CERT), (r"experience|work order", DocumentType.EXPERIENCE_CERT))
def classify_document(file_path: Union[str, Path]) -> DocumentClassification:
    text = "\n".join(page for _, page in parse_document(file_path)).lower()
    for pattern, kind in _CLASSIFIERS:
        if re.search(pattern, text): return DocumentClassification(document_type=kind, confidence=.8, rationale=f"matched document marker: {pattern}")
    return DocumentClassification(document_type=DocumentType.OTHER, confidence=.3, rationale="no supported document marker found")

def extract_document(file_path: Union[str, Path], *, document_id: str, bidder_id: str, gateway: ModelGateway = None) -> list[ExtractedFactDraft]:
    """Conservative deterministic baseline; model-backed extraction plugs in via ModelGateway."""
    pages = parse_document(file_path); text = "\n".join(t for _, t in pages)
    if gateway and gateway.provider:
        output = gateway.extract_structured("Extract factual claims only. Do not decide eligibility, compliance, or qualification. Preserve source page/text. Return facts matching the schema.", text, DocumentExtractionResponse)
        return [fact.model_copy(update={"document_id": document_id, "bidder_id": bidder_id}) for fact in output.facts]
    patterns = (("tax.gstin", r"\b\d{2}[A-Z]{5}\d{4}[A-Z]\d[Z]\d\b"), ("identity.pan", r"\b[A-Z]{5}\d{4}[A-Z]\b"))
    facts: list[ExtractedFactDraft] = []
    for field, pattern in patterns:
        match = re.search(pattern, text)
        if match:
            page = next((p for p, body in pages if match.group() in body), None)
            facts.append(ExtractedFactDraft(document_id=document_id, bidder_id=bidder_id, field=field, value=match.group(), normalized_value=match.group(), source_page=page, source_text=match.group(), confidence=.98, provider="deterministic", model="regex-v1"))
    registration_patterns = (
        ("registration.udyam", r"\bUDYAM-[A-Z]{2}-\d{2}-\d{6,8}\b"),
        ("labour.epfo_registration", r"\b(?:EPFO|PF)\s*(?:code|registration)?\s*[:#-]?\s*([A-Z]{2,5}[A-Z0-9/-]{4,})"),
        ("labour.esic_registration", r"\b(?:ESIC|ESI)\s*(?:code|registration)?\s*[:#-]?\s*(\d{10,17})"),
    )
    for field, pattern in registration_patterns:
        match = re.search(pattern, text, re.I)
        if match:
            value = match.group(1) if match.lastindex else match.group()
            page = next((p for p, body in pages if match.group() in body), None)
            facts.append(ExtractedFactDraft(document_id=document_id, bidder_id=bidder_id, field=field, value=value, normalized_value=value.upper(), source_page=page, source_text=match.group(), confidence=.90, provider="deterministic", model="regex-v1"))
    turnover = re.search(r"(?:annual\s+)?turnover[^\n.]{0,80}?(?:INR|Rs\.?|₹)\s*([\d,]+)", text, re.I)
    if turnover:
        page = next((p for p, body in pages if turnover.group() in body), None)
        facts.append(ExtractedFactDraft(document_id=document_id, bidder_id=bidder_id, field="financial.average_annual_turnover", value=int(turnover.group(1).replace(",", "")), normalized_value=int(turnover.group(1).replace(",", "")), source_page=page, source_text=_sentence(text, turnover.start(), turnover.end()), confidence=.78, provider="deterministic", model="regex-v1"))
    experience = re.search(r"(?:experience|completed work)[^.\n]{0,80}?(\d+)\s*(?:years?|yrs?)", text, re.I)
    if experience:
        page = next((p for p, body in pages if experience.group() in body), None)
        facts.append(ExtractedFactDraft(document_id=document_id, bidder_id=bidder_id, field="experience.years", value=int(experience.group(1)), normalized_value=int(experience.group(1)), source_page=page, source_text=_sentence(text, experience.start(), experience.end()), confidence=.75, provider="deterministic", model="regex-v1"))
    expiry = re.search(r"(?:valid\s+(?:up\s+)?to|expiry\s*date|expires?)\s*[:.-]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", text, re.I)
    if expiry:
        parsed = _parse_date(expiry.group(1))
        if parsed:
            page = next((p for p, body in pages if expiry.group() in body), None)
            facts.append(ExtractedFactDraft(document_id=document_id, bidder_id=bidder_id, field="document.expiry_date", value=parsed, normalized_value=parsed, source_page=page, source_text=expiry.group(), confidence=.84, provider="deterministic", model="regex-v1"))
    if Path(file_path).suffix.lower() == ".csv": facts.extend(_extract_csv_facts(Path(file_path), document_id, bidder_id))
    return _deduplicate_facts(facts)

def _extract_csv_facts(path: Path, document_id: str, bidder_id: str) -> list[ExtractedFactDraft]:
    """Table-aware baseline for CSV exports; spreadsheet/PDF tables need dedicated adapters."""
    aliases = {"gstin": "tax.gstin", "pan": "identity.pan", "udyam": "registration.udyam", "turnover": "financial.average_annual_turnover", "experience_years": "experience.years", "expiry_date": "document.expiry_date"}
    extracted: list[ExtractedFactDraft] = []
    with path.open(encoding="utf-8", errors="replace", newline="") as handle:
        for row_number, row in enumerate(csv.DictReader(handle), 2):
            for column, field in aliases.items():
                value = row.get(column)
                if not value or not value.strip(): continue
                normalized: Any = value.strip()
                if field in {"financial.average_annual_turnover", "experience.years"}:
                    try: normalized = int(re.sub(r"[^0-9]", "", value))
                    except ValueError: continue
                if field == "document.expiry_date":
                    normalized = _parse_date(value.strip())
                    if not normalized: continue
                extracted.append(ExtractedFactDraft(document_id=document_id, bidder_id=bidder_id, field=field, value=normalized, normalized_value=normalized, source_page=1, source_text="CSV row %d, column %s: %s" % (row_number, column, value), confidence=.90, provider="deterministic", model="csv-table-v1"))
    return extracted

def _deduplicate_facts(facts: list[ExtractedFactDraft]) -> list[ExtractedFactDraft]:
    seen, unique = set(), []
    for fact in facts:
        key = (fact.field, str(fact.normalized_value if fact.normalized_value is not None else fact.value))
        if key not in seen: seen.add(key); unique.append(fact)
    return unique

def _parse_date(value: str):
    for pattern in ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y"):
        try: return datetime.strptime(value, pattern).date().isoformat()
        except ValueError: pass
    return None

def extract_tender(file_path: Union[str, Path], gateway: ModelGateway = None) -> list[TenderRequirementDraft]:
    """Extract only unambiguous turnover thresholds; all other clauses remain for review/model extraction."""
    pages = parse_document(file_path)
    if gateway and gateway.provider:
        content = "\n\n".join("PAGE %s:\n%s" % page for page in pages)
        output = gateway.extract_structured("Extract machine-readable tender eligibility requirements only. Never decide bidder qualification or PASS/FAIL. If a clause is ambiguous, omit it rather than guessing.", content, TenderExtractionResponse)
        return output.requirements
    requirements: list[TenderRequirementDraft] = []
    for page, text in pages:
        clause_match = re.search(r"(?m)^\s*(\d+(?:\.\d+)*)\s*[).:-]", text)
        clause = clause_match.group(1) if clause_match else "UNNUMBERED"
        match = re.search(r"(?:minimum\s+)?(?:annual\s+)?turnover[^\n.]{0,100}?(?:INR|Rs\.?|₹)\s*([\d,]+)", text, re.I)
        if match:
            requirements.append(TenderRequirementDraft(clause=clause, requirement_type=RequirementType.TURNOVER, field="financial.average_annual_turnover", operator=Operator.GTE, expected_value=int(match.group(1).replace(",", "")), unit="INR", source_page=page, source_text=match.group(), confidence=.75, requires_verification=True))
        simple_requirements = (
            (r"\bgstin\b|\bgst registration\b", RequirementType.GST, "tax.gstin", Operator.EXISTS, True),
            (r"\budyam\b", RequirementType.UDYAM, "registration.udyam", Operator.EXISTS, True),
            (r"\bepfo\b", RequirementType.EPFO, "labour.epfo_registration", Operator.EXISTS, True),
            (r"\besic\b", RequirementType.ESIC, "labour.esic_registration", Operator.EXISTS, True),
            (r"\bmca\b|\bministry of corporate affairs\b", RequirementType.MCA, "corporate.mca_registration", Operator.EXISTS, True),
            (r"(?:not|no)\s+(?:be\s+)?blacklisted|non[ -]blacklisted", RequirementType.BLACK_LIST, "legal.blacklisted", Operator.EQ, False),
        )
        for pattern, kind, field, operator, expected in simple_requirements:
            marker = re.search(pattern, text, re.I)
            if marker:
                requirements.append(TenderRequirementDraft(clause=clause, requirement_type=kind, field=field, operator=operator, expected_value=expected, source_page=page, source_text=_sentence(text, marker.start(), marker.end()), confidence=.82, requires_verification=True))
        experience = re.search(r"(?:minimum\s+)?experience[^.\n]{0,100}?(\d+)\s*(?:years?|yrs?)", text, re.I)
        if experience:
            requirements.append(TenderRequirementDraft(clause=clause, requirement_type=RequirementType.EXPERIENCE, field="experience.years", operator=Operator.GTE, expected_value=int(experience.group(1)), unit="years", source_page=page, source_text=_sentence(text, experience.start(), experience.end()), confidence=.78, requires_verification=True))
    return requirements

def _sentence(text: str, start: int, end: int) -> str:
    """Small source excerpt for audit evidence; avoids returning a whole document page."""
    left = max(text.rfind(".", 0, start), text.rfind("\n", 0, start)) + 1
    right_options = [position for position in (text.find(".", end), text.find("\n", end)) if position != -1]
    right = min(right_options) if right_options else len(text)
    return text[left:right].strip()
