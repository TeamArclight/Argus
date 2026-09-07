import hashlib
from collections import Counter
from datetime import date
from difflib import SequenceMatcher
import re
from typing import Any, Optional, Set
from ..contracts import RiskSignal

# Canonical severity levels matching API's RiskSeverity enum.
_HIGH = "HIGH"
_MEDIUM = "MEDIUM"

def _normalise_name(value: str) -> str:
    return re.sub(r"\b(private|limited|ltd|llp|pvt)\b|[^a-z0-9]", "", value.lower())

def _token_similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, _normalise_name(left), _normalise_name(right)).ratio()

def _text_similarity(left: str, right: str) -> float:
    terms = lambda text: Counter(re.findall(r"[a-z0-9]+", text.lower()))
    a, b = terms(left), terms(right); dot = sum(a[key] * b[key] for key in a.keys() & b.keys())
    denom = (sum(v*v for v in a.values()) * sum(v*v for v in b.values())) ** .5
    return dot / denom if denom else 0.0

def detect_risk(*, document_bytes: Optional[bytes] = None, prior_hashes: Optional[Set[str]] = None,
                claim_value: object = None, verified_value: object = None,
                claimed_entity_name: Optional[str] = None, verified_entity_name: Optional[str] = None,
                identifiers: Optional[dict[str, set[str]]] = None, expiry_date: Optional[date] = None,
                document_text: Optional[str] = None, prior_document_texts: Optional[dict[str, str]] = None,
                oem_authorization_hash: Optional[str] = None, prior_oem_authorization_hashes: Optional[Set[str]] = None,
                facts_by_field: Optional[dict[str, list[tuple[str, Any]]]] = None,
                evidence_ids: Optional[list[str]] = None) -> list[RiskSignal]:
    """Deterministic signals only; callers route these to officer review, not compliance."""
    signals: list[RiskSignal] = []
    evidence_ids = evidence_ids or []
    if document_bytes:
        digest = hashlib.sha256(document_bytes).hexdigest()
        if digest in (prior_hashes or set()): signals.append(RiskSignal(signal_type="DUPLICATE_DOCUMENT", severity=_HIGH, title="Duplicate document", description="SHA-256 matches an earlier submitted document.", evidence_ids=evidence_ids))
    if claim_value is not None and verified_value is not None and claim_value != verified_value:
        signals.append(RiskSignal(signal_type="CLAIM_VERIFICATION_MISMATCH", severity=_HIGH, title="Claim conflicts with verification", description="The extracted claim differs from the authorized verification response.", evidence_ids=evidence_ids))
    if claimed_entity_name and verified_entity_name:
        score = _token_similarity(claimed_entity_name, verified_entity_name)
        if score < .82: signals.append(RiskSignal(signal_type="ENTITY_NAME_MISMATCH", severity=_MEDIUM, title="Entity-name mismatch", description="Claimed and verified entity names differ (similarity %.2f)." % score, evidence_ids=evidence_ids))
    for identifier, values in (identifiers or {}).items():
        normalised = {str(value).strip().upper() for value in values if str(value).strip()}
        if len(normalised) > 1: signals.append(RiskSignal(signal_type="IDENTIFIER_DISAGREEMENT", severity=_HIGH, title="Identifier disagreement", description="Multiple values were found for %s." % identifier, evidence_ids=evidence_ids))
    if expiry_date and expiry_date < date.today(): signals.append(RiskSignal(signal_type="EXPIRED_DOCUMENT", severity=_HIGH, title="Expired document", description="Document expiry date %s is in the past." % expiry_date.isoformat(), evidence_ids=evidence_ids))
    if oem_authorization_hash and oem_authorization_hash in (prior_oem_authorization_hashes or set()): signals.append(RiskSignal(signal_type="DUPLICATE_OEM_AUTHORIZATION", severity=_HIGH, title="Duplicate OEM authorization", description="OEM authorization hash matches a previous authorization.", evidence_ids=evidence_ids))
    for document_id, earlier in (prior_document_texts or {}).items():
        if document_text and _text_similarity(document_text, earlier) >= .97: signals.append(RiskSignal(signal_type="SUSPICIOUS_DOCUMENT_SIMILARITY", severity=_MEDIUM, title="Near-duplicate document", description="Document text is near-identical to document %s." % document_id, evidence_ids=[document_id, *evidence_ids])); break
    for field, observations in (facts_by_field or {}).items():
        distinct = {str(value).strip() for _, value in observations if value is not None}
        if len(distinct) > 1: signals.append(RiskSignal(signal_type="CONTRADICTORY_FACTS", severity=_HIGH, title="Contradictory extracted facts", description="Conflicting values were extracted for %s." % field, evidence_ids=[item[0] for item in observations]))
    return signals
