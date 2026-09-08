"""Transforms extracted facts into deterministic risk-engine inputs."""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Optional

from ..contracts import ExtractedFactDraft
from .service import detect_risk

_IDENTIFIER_FIELDS = {"tax.gstin": "gstin", "identity.pan": "pan", "registration.udyam": "udyam", "labour.epfo_registration": "epfo", "labour.esic_registration": "esic"}

def detect_fact_risks(facts: Iterable[ExtractedFactDraft], *, prior_hashes: Optional[set[str]] = None) -> list:
    """Generate explainable risk signals from current-bid facts only.

    Verification comparisons and historical document text may be supplied separately
    by the authorized integration layer; this function does not call external systems.
    """
    facts = list(facts); identifiers: dict[str, set[str]] = {}; grouped: dict[str, list[tuple[str, Any]]] = {}
    expiry: Optional[date] = None
    for index, fact in enumerate(facts):
        evidence_id = "%s:%s" % (fact.document_id, index)
        grouped.setdefault(fact.field, []).append((evidence_id, fact.normalized_value if fact.normalized_value is not None else fact.value))
        identifier = _IDENTIFIER_FIELDS.get(fact.field)
        if identifier: identifiers.setdefault(identifier, set()).add(str(fact.normalized_value or fact.value))
        if fact.field == "document.expiry_date":
            try: expiry = date.fromisoformat(str(fact.normalized_value or fact.value))
            except ValueError: continue
    return detect_risk(prior_hashes=prior_hashes, identifiers=identifiers, expiry_date=expiry, facts_by_field=grouped, evidence_ids=["%s:%s" % (fact.document_id, index) for index, fact in enumerate(facts)])
