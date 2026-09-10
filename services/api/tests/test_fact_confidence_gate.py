"""Guards that low-confidence AI-extracted facts cannot produce a definitive PASS.

Audit finding C-6. The intelligence service reports a per-fact extraction
confidence; before this gate the deterministic engine ignored it, so a
hallucinated value could satisfy a mandatory rule outright.

The gate is deliberately narrow: it fires only when an outcome would rest
*solely* on an extracted fact with no corroborating registry verification for
that field, and never when the fact is human-attested.
"""
from datetime import datetime, timezone

import pytest

from app.compliance.engine import ComplianceEngine
from app.compliance.reason_codes import ReasonCode
from app.schemas.canonical import (
    ComplianceStatus,
    FactRead,
    OperatorEnum,
    RequirementType,
    TenderRequirementRead,
    VerificationResultRead,
    VerificationSource,
    VerificationStatus,
)

EVAL_TS = datetime(2026, 6, 1, tzinfo=timezone.utc)
CONTEXT = {"bidder_id": "BIDDER-901", "tender_id": "TENDER-901", "evaluation_timestamp": EVAL_TS}


def make_requirement(
    operator: OperatorEnum = OperatorEnum.GTE,
    expected_value=20000000,
    field: str = "financial.average_annual_turnover",
    mandatory: bool = True,
    req_type: RequirementType = RequirementType.TURNOVER,
) -> TenderRequirementRead:
    return TenderRequirementRead(
        id="REQ-901",
        tender_id="TENDER-901",
        clause="4.2",
        requirement_type=req_type,
        field=field,
        operator=operator,
        expected_value=expected_value,
        unit="INR" if "financial" in field else None,
        mandatory=mandatory,
        confidence=1.0,
        requires_verification=True,
        is_approved=True,
        metadata_json={"currency": "INR"} if "financial" in field else {},
        created_at=EVAL_TS,
    )


def make_fact(
    value,
    confidence: float = 1.0,
    field: str = "financial.average_annual_turnover",
    fact_id: str = "FACT-901",
    meta: dict | None = None,
) -> FactRead:
    metadata = {"currency": "INR"} if "financial" in field else {}
    if meta:
        metadata.update(meta)
    return FactRead(
        id=fact_id,
        document_id="DOC-901",
        bidder_id="BIDDER-901",
        field=field,
        value=value,
        confidence=confidence,
        metadata_json=metadata,
        created_at=EVAL_TS,
    )


def make_verification(
    verified_value,
    field: str = "financial.average_annual_turnover",
    ver_id: str = "VER-901",
) -> VerificationResultRead:
    return VerificationResultRead(
        id=ver_id,
        bidder_id="BIDDER-901",
        field=field,
        claimed_value=verified_value,
        verified_value=verified_value,
        status=VerificationStatus.VERIFIED,
        source=VerificationSource.GST_DEMO_DATA,
        checked_at=EVAL_TS,
    )


def evaluate(rule, facts, verifications=None, context=None):
    return ComplianceEngine.evaluate(
        rule=rule,
        facts=facts,
        verification_results=verifications or [],
        context={**CONTEXT, **(context or {})},
        evaluation_timestamp=EVAL_TS,
    )


# ---------------------------------------------------------------------------
# 1. High-confidence facts keep the existing deterministic behaviour
# ---------------------------------------------------------------------------

def test_high_confidence_fact_still_passes_deterministically():
    result = evaluate(make_requirement(), [make_fact(50000000, confidence=0.95)])
    assert result.status == ComplianceStatus.PASS
    assert result.reason_code == ReasonCode.GREATER_THAN_OR_EQUAL
    assert result.observed_value == 50000000


def test_high_confidence_fact_still_fails_deterministically():
    result = evaluate(make_requirement(), [make_fact(5000000, confidence=0.95)])
    assert result.status == ComplianceStatus.FAIL
    assert result.reason_code == ReasonCode.LESS_THAN


def test_fact_without_explicit_confidence_is_treated_as_confident():
    """FactRead.confidence defaults to 1.0, so pre-existing behaviour is unchanged."""
    fact = make_fact(50000000)
    assert fact.confidence == 1.0
    assert evaluate(make_requirement(), [fact]).status == ComplianceStatus.PASS


def test_confidence_exactly_at_threshold_is_accepted():
    result = evaluate(
        make_requirement(), [make_fact(50000000, confidence=ComplianceEngine.MIN_FACT_CONFIDENCE)]
    )
    assert result.status == ComplianceStatus.PASS


# ---------------------------------------------------------------------------
# 2 & 3. Low-confidence facts cannot yield PASS
# ---------------------------------------------------------------------------

def test_low_confidence_fact_on_mandatory_rule_requires_review():
    result = evaluate(make_requirement(mandatory=True), [make_fact(50000000, confidence=0.21)])
    assert result.status == ComplianceStatus.REVIEW_REQUIRED
    assert result.reason_code == ReasonCode.LOW_CONFIDENCE_EVIDENCE
    assert result.evidence_ids == ["FACT-901"]


def test_low_confidence_fact_never_yields_pass_for_any_operator():
    """A low-confidence fact must not produce PASS through any comparison path."""
    cases = [
        (OperatorEnum.GTE, 20000000, 50000000),
        (OperatorEnum.GT, 20000000, 50000000),
        (OperatorEnum.LTE, 90000000, 50000000),
        (OperatorEnum.EQ, 50000000, 50000000),
        (OperatorEnum.EXISTS, True, 50000000),
    ]
    for operator, expected, observed in cases:
        rule = make_requirement(operator=operator, expected_value=expected)
        result = evaluate(rule, [make_fact(observed, confidence=0.3)])
        assert result.status != ComplianceStatus.PASS, f"{operator} leaked a PASS"
        assert result.status == ComplianceStatus.REVIEW_REQUIRED
        assert result.reason_code == ReasonCode.LOW_CONFIDENCE_EVIDENCE


def test_low_confidence_fact_does_not_produce_definitive_fail_either():
    """The gate withholds determination in both directions rather than failing a bidder."""
    result = evaluate(make_requirement(), [make_fact(1000, confidence=0.3)])
    assert result.status == ComplianceStatus.REVIEW_REQUIRED
    assert result.reason_code == ReasonCode.LOW_CONFIDENCE_EVIDENCE


def test_unparseable_confidence_is_treated_as_untrusted():
    """A malformed confidence must fail closed, not default to fully confident."""

    class _Malformed:
        id = "FACT-BAD"
        confidence = "not-a-number"
        metadata_json: dict = {}

    assert ComplianceEngine._fact_confidence(_Malformed()) == 0.0
    # An absent confidence attribute keeps the permissive default for non-AI callers.
    assert ComplianceEngine._fact_confidence(object()) == 1.0


# ---------------------------------------------------------------------------
# 4. Multiple facts where one critical input is low confidence
# ---------------------------------------------------------------------------

def test_one_low_confidence_fact_among_several_blocks_definitive_outcome():
    """Agreeing values do not launder an untrusted extraction into a PASS."""
    facts = [
        make_fact(50000000, confidence=0.97, fact_id="FACT-HI"),
        make_fact(50000000, confidence=0.19, fact_id="FACT-LO"),
    ]
    result = evaluate(make_requirement(), facts)
    assert result.status == ComplianceStatus.REVIEW_REQUIRED
    assert result.reason_code == ReasonCode.LOW_CONFIDENCE_EVIDENCE
    assert result.evidence_ids == ["FACT-LO"]


def test_conflicting_facts_precedence_still_wins_over_confidence_gate():
    """The gate is inserted after the existing conflict checks and must not displace them."""
    facts = [
        make_fact(50000000, confidence=0.97, fact_id="FACT-HI"),
        make_fact(9000000, confidence=0.19, fact_id="FACT-LO"),
    ]
    result = evaluate(make_requirement(), facts)
    assert result.status == ComplianceStatus.REVIEW_REQUIRED
    assert result.reason_code == ReasonCode.CONFLICTING_FACTS


def test_all_high_confidence_facts_evaluate_normally():
    facts = [
        make_fact(50000000, confidence=0.97, fact_id="FACT-A"),
        make_fact(50000000, confidence=0.88, fact_id="FACT-B"),
    ]
    assert evaluate(make_requirement(), facts).status == ComplianceStatus.PASS


# ---------------------------------------------------------------------------
# 5. Trusted / corroborated evidence still evaluates deterministically
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "meta",
    [
        {"human_verified": True},
        {"officer_verified": True},
        {"manually_approved": True},
        {"verification_source": "MANUAL_OFFICER_ENTRY"},
        {"fact_source": "human_attested"},
    ],
)
def test_human_attested_fact_bypasses_confidence_gate(meta):
    result = evaluate(make_requirement(), [make_fact(50000000, confidence=0.05, meta=meta)])
    assert result.status == ComplianceStatus.PASS
    assert result.reason_code == ReasonCode.GREATER_THAN_OR_EQUAL


def test_registry_corroborated_low_confidence_fact_is_not_gated():
    """The gate fires only when the outcome rests SOLELY on the extracted fact."""
    result = evaluate(
        make_requirement(),
        [make_fact(50000000, confidence=0.15)],
        [make_verification(50000000)],
    )
    assert result.status == ComplianceStatus.PASS
    assert result.reason_code == ReasonCode.GREATER_THAN_OR_EQUAL


def test_claim_verification_mismatch_still_takes_precedence():
    result = evaluate(
        make_requirement(),
        [make_fact(50000000, confidence=0.15)],
        [make_verification(9000000)],
    )
    assert result.status == ComplianceStatus.REVIEW_REQUIRED
    assert result.reason_code == ReasonCode.CLAIM_VERIFICATION_MISMATCH


# ---------------------------------------------------------------------------
# Threshold configuration
# ---------------------------------------------------------------------------

def test_threshold_is_configurable_per_evaluation():
    fact = make_fact(50000000, confidence=0.7)
    assert evaluate(make_requirement(), [fact]).status == ComplianceStatus.PASS
    strict = evaluate(make_requirement(), [fact], context={"min_fact_confidence": 0.9})
    assert strict.status == ComplianceStatus.REVIEW_REQUIRED
    assert strict.reason_code == ReasonCode.LOW_CONFIDENCE_EVIDENCE


def test_invalid_threshold_falls_back_to_engine_default():
    for bad in ["not-a-number", -1.0, 5.0, None]:
        assert (
            ComplianceEngine._resolve_min_fact_confidence({"min_fact_confidence": bad})
            == ComplianceEngine.MIN_FACT_CONFIDENCE
        )


def test_missing_evidence_path_is_unaffected_by_the_gate():
    """No facts at all is still MISSING_EVIDENCE, not a confidence problem."""
    result = evaluate(make_requirement(operator=OperatorEnum.EXISTS, expected_value=True), [])
    assert result.status == ComplianceStatus.UNKNOWN
    assert result.reason_code == ReasonCode.MISSING_EVIDENCE
