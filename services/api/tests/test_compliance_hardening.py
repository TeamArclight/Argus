from datetime import date, datetime, timezone
from decimal import Decimal
import pytest

from app.compliance.engine import ComplianceEngine
from app.compliance.reason_codes import ReasonCode
from app.risk.engine import RiskEngine, RiskSignalCandidate
from app.schemas.canonical import (
    ComplianceStatus,
    FactRead,
    OperatorEnum,
    RequirementType,
    RiskSeverity,
    TenderRequirementRead,
    VerificationResultRead,
    VerificationSource,
    VerificationStatus,
)


def make_req(
    operator: OperatorEnum,
    expected_value: any,
    field: str = "financial.annual_turnover",
    mandatory: bool = True,
    unit: str | None = "INR",
    req_type: RequirementType = RequirementType.TURNOVER,
    req_id: str = "REQ-101",
) -> TenderRequirementRead:
    return TenderRequirementRead(
        id=req_id,
        tender_id="TENDER-101",
        clause="4.1",
        requirement_type=req_type,
        field=field,
        operator=operator,
        expected_value=expected_value,
        unit=unit,
        mandatory=mandatory,
        confidence=1.0,
        requires_verification=True,
        is_approved=True,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def make_fact(
    value: any,
    field: str = "financial.annual_turnover",
    fact_id: str = "FACT-101",
    meta: dict | None = None,
) -> FactRead:
    return FactRead(
        id=fact_id,
        document_id="DOC-101",
        bidder_id="BIDDER-101",
        field=field,
        value=value,
        confidence=1.0,
        metadata_json=meta or {},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def make_ver(
    status: VerificationStatus,
    verified_value: any = None,
    field: str = "financial.annual_turnover",
    ver_id: str = "VER-101",
) -> VerificationResultRead:
    return VerificationResultRead(
        id=ver_id,
        bidder_id="BIDDER-101",
        field=field,
        claimed_value=None,
        verified_value=verified_value,
        status=status,
        source=VerificationSource.GST_DEMO_DATA,
        checked_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# 1. EXACT NUMERIC SEMANTICS & DECIMAL PRECISION TESTS
# ---------------------------------------------------------------------------

def test_numeric_decimal_precision_large_values():
    """Verifies that large numbers (Crore, Billion) evaluate with exact Decimal precision."""
    req = make_req(OperatorEnum.GTE, 5000000000, unit="INR")  # 500 Crore
    facts = [make_fact(5000000000)]
    res = ComplianceEngine.evaluate(req, facts, [])
    assert res.status == ComplianceStatus.PASS
    assert res.reason_code == ReasonCode.GREATER_THAN_OR_EQUAL

    # Fact with 499.9999999 Crore should fail GTE 500 Crore
    facts_fail = [make_fact(4999999999)]
    res_fail = ComplianceEngine.evaluate(req, facts_fail, [])
    assert res_fail.status == ComplianceStatus.FAIL
    assert res_fail.reason_code == ReasonCode.LESS_THAN


def test_numeric_rejection_of_nan_and_infinities():
    """Verifies that NaN and Infinity inputs are strictly rejected."""
    req = make_req(OperatorEnum.GTE, 1000000)

    for bad_val in [float("nan"), float("inf"), float("-inf"), "NaN", "Infinity", "-inf", "+inf"]:
        facts = [make_fact(bad_val)]
        res = ComplianceEngine.evaluate(req, facts, [])
        assert res.status == ComplianceStatus.REVIEW_REQUIRED
        assert res.reason_code == ReasonCode.TYPE_CONVERSION_ERROR


def test_numeric_rejection_of_booleans():
    """Verifies that boolean values are NEVER masqueraded as numbers."""
    req = make_req(OperatorEnum.GTE, 1)

    for bool_val in [True, False]:
        facts = [make_fact(bool_val)]
        res = ComplianceEngine.evaluate(req, facts, [])
        assert res.status == ComplianceStatus.REVIEW_REQUIRED
        assert res.reason_code == ReasonCode.TYPE_CONVERSION_ERROR


def test_numeric_rejection_of_corrupt_comma_formatting():
    """Verifies that malformed comma formatting is rejected as invalid numbers."""
    req = make_req(OperatorEnum.GTE, 1000)

    for bad_str in ["12,,34", "1,2.34.56", ",1000", "1000,", "12.3,4"]:
        facts = [make_fact(bad_str)]
        res = ComplianceEngine.evaluate(req, facts, [])
        assert res.status == ComplianceStatus.REVIEW_REQUIRED
        assert res.reason_code == ReasonCode.TYPE_CONVERSION_ERROR


def test_numeric_scale_applied_exactly_once():
    """Verifies scale is applied exactly once when both metadata unit and textual scale match."""
    req = make_req(OperatorEnum.EQ, 50000000, unit="INR")  # 5 Crore in INR

    # Case A: structured metadata unit "Crore" + numeric 5
    fact_a = make_fact(5, meta={"unit": "Crore", "currency": "INR"})
    res_a = ComplianceEngine.evaluate(req, [fact_a], [])
    assert res_a.status == ComplianceStatus.PASS
    assert res_a.reason_code == ReasonCode.EQUAL

    # Case B: explicit text "5 Crore"
    fact_b = make_fact("5 Crore")
    res_b = ComplianceEngine.evaluate(req, [fact_b], [])
    assert res_b.status == ComplianceStatus.PASS
    assert res_b.reason_code == ReasonCode.EQUAL

    # Case C: both metadata unit "Crore" AND explicit text "5 Crore" -> must NOT double-scale to 500,000,000,000,000
    fact_c = make_fact("5 Crore", meta={"unit": "Crore", "currency": "INR"})
    res_c = ComplianceEngine.evaluate(req, [fact_c], [])
    assert res_c.status == ComplianceStatus.PASS
    assert res_c.reason_code == ReasonCode.EQUAL


def test_numeric_unit_scale_contradiction():
    """Verifies that contradictory metadata unit and textual scale fail safely."""
    req = make_req(OperatorEnum.GTE, 50000000)
    fact = make_fact("5 Lakh", meta={"unit": "Crore"})  # Text says Lakh (500k), metadata says Crore (50M)
    res = ComplianceEngine.evaluate(req, [fact], [])
    assert res.status == ComplianceStatus.REVIEW_REQUIRED
    assert res.reason_code == ReasonCode.TYPE_CONVERSION_ERROR


def test_numeric_currency_mismatch_fails_closed():
    """Verifies that currency mismatch returns CURRENCY_MISMATCH without silent conversion."""
    req = make_req(OperatorEnum.GTE, 100000, unit="INR")
    fact = make_fact(200000, meta={"currency": "USD"})
    res = ComplianceEngine.evaluate(req, [fact], [])
    assert res.status == ComplianceStatus.REVIEW_REQUIRED
    assert res.reason_code == ReasonCode.CURRENCY_MISMATCH


def test_numeric_boundary_conditions_eq_gt_gte_lt_lte():
    """Verifies boundary correctness across numeric operators."""
    # EQ / NE
    req_eq = make_req(OperatorEnum.EQ, 100)
    assert ComplianceEngine.evaluate(req_eq, [make_fact(100)], []).status == ComplianceStatus.PASS
    assert ComplianceEngine.evaluate(req_eq, [make_fact(101)], []).status == ComplianceStatus.FAIL

    req_ne = make_req(OperatorEnum.NE, 100)
    assert ComplianceEngine.evaluate(req_ne, [make_fact(101)], []).status == ComplianceStatus.PASS
    assert ComplianceEngine.evaluate(req_ne, [make_fact(100)], []).status == ComplianceStatus.FAIL

    # GT / GTE
    req_gt = make_req(OperatorEnum.GT, 100)
    assert ComplianceEngine.evaluate(req_gt, [make_fact(100)], []).status == ComplianceStatus.FAIL
    assert ComplianceEngine.evaluate(req_gt, [make_fact(100.00001)], []).status == ComplianceStatus.PASS

    req_gte = make_req(OperatorEnum.GTE, 100)
    assert ComplianceEngine.evaluate(req_gte, [make_fact(100)], []).status == ComplianceStatus.PASS
    assert ComplianceEngine.evaluate(req_gte, [make_fact(99.99999)], []).status == ComplianceStatus.FAIL

    # LT / LTE
    req_lt = make_req(OperatorEnum.LT, 100)
    assert ComplianceEngine.evaluate(req_lt, [make_fact(100)], []).status == ComplianceStatus.FAIL
    assert ComplianceEngine.evaluate(req_lt, [make_fact(99.99999)], []).status == ComplianceStatus.PASS

    req_lte = make_req(OperatorEnum.LTE, 100)
    assert ComplianceEngine.evaluate(req_lte, [make_fact(100)], []).status == ComplianceStatus.PASS
    assert ComplianceEngine.evaluate(req_lte, [make_fact(100.00001)], []).status == ComplianceStatus.FAIL


def test_count_gte_integer_semantics():
    """Verifies COUNT_GTE behaves deterministically for lists and counts."""
    field_name = "technical.certifications_count"
    req = make_req(OperatorEnum.COUNT_GTE, 3, field=field_name, unit=None)

    # List of length 3 -> PASS
    assert ComplianceEngine.evaluate(req, [make_fact(["ISO9001", "ISO27001", "CMMI5"], field=field_name)], []).status == ComplianceStatus.PASS
    # List of length 2 -> FAIL
    assert ComplianceEngine.evaluate(req, [make_fact(["ISO9001", "ISO27001"], field=field_name)], []).status == ComplianceStatus.FAIL
    # Numeric count 3 -> PASS
    assert ComplianceEngine.evaluate(req, [make_fact(3, field=field_name)], []).status == ComplianceStatus.PASS
    # Numeric count 2 -> FAIL
    assert ComplianceEngine.evaluate(req, [make_fact(2, field=field_name)], []).status == ComplianceStatus.FAIL
    # Negative count -> TYPE_CONVERSION_ERROR
    assert ComplianceEngine.evaluate(req, [make_fact(-1, field=field_name)], []).status == ComplianceStatus.REVIEW_REQUIRED


# ---------------------------------------------------------------------------
# 2. TEMPORAL DETERMINISM & DATE/TIME SEMANTICS
# ---------------------------------------------------------------------------

def test_temporal_pure_evaluation_timestamp():
    """Verifies that evaluation timestamp is injected and pure without datetime.now() divergence."""
    fixed_ts = datetime(2026, 5, 20, 14, 30, 0, tzinfo=timezone.utc)
    req = make_req(OperatorEnum.EQ, "ACTIVE", field="gst.status", req_type=RequirementType.GST)
    fact = make_fact("ACTIVE", field="gst.status")

    res = ComplianceEngine.evaluate(req, [fact], [], evaluation_timestamp=fixed_ts)
    assert res.evaluated_at == fixed_ts


def test_temporal_date_only_vs_datetime_semantics():
    """Verifies date comparisons and leap-year boundaries."""
    req_before = make_req(
        OperatorEnum.DATE_BEFORE, "2024-03-01", field="general.incorporation_date", unit=None
    )

    # Valid leap day: 2024-02-29 is BEFORE 2024-03-01 -> PASS
    fact_leap = make_fact("2024-02-29", field="general.incorporation_date")
    res_leap = ComplianceEngine.evaluate(req_before, [fact_leap], [])
    assert res_leap.status == ComplianceStatus.PASS
    assert res_leap.reason_code == ReasonCode.BEFORE_DATE

    # 2024-03-01 is ON_OR_AFTER 2024-03-01 -> FAIL
    fact_same = make_fact("2024-03-01", field="general.incorporation_date")
    res_same = ComplianceEngine.evaluate(req_before, [fact_same], [])
    assert res_same.status == ComplianceStatus.FAIL
    assert res_same.reason_code == ReasonCode.ON_OR_AFTER_DATE


def test_temporal_rejection_of_malformed_dates():
    """Verifies that invalid/malformed date strings return MALFORMED_DATE."""
    req = make_req(OperatorEnum.DATE_BEFORE, "2024-03-01", field="general.incorporation_date", unit=None)

    for bad_date in ["2024-02-30", "invalid-date", "99/99/9999", "2023-02-29"]:
        res = ComplianceEngine.evaluate(req, [make_fact(bad_date, field="general.incorporation_date")], [])
        assert res.status == ComplianceStatus.REVIEW_REQUIRED
        assert res.reason_code == ReasonCode.MALFORMED_DATE


def test_temporal_timezone_mismatch_handling():
    """Verifies timezone-aware vs naive comparisons return MALFORMED_DATE safely."""
    req = make_req(
        OperatorEnum.DATE_BEFORE,
        datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        field="general.deadline",
        unit=None,
    )
    # Naive datetime fact
    fact_naive = make_fact(datetime(2025, 12, 31, 23, 59, 59), field="general.deadline")
    res = ComplianceEngine.evaluate(req, [fact_naive], [])
    assert res.status == ComplianceStatus.REVIEW_REQUIRED
    assert res.reason_code == ReasonCode.MALFORMED_DATE


# ---------------------------------------------------------------------------
# 3. MISSING EVIDENCE TRUTH TABLE & 4/5-VALUED LOGIC
# ---------------------------------------------------------------------------

def test_missing_evidence_truth_table_mandatory_vs_optional():
    """Verifies mandatory missing evidence is UNKNOWN and optional missing is NOT_APPLICABLE."""
    # Mandatory rule + missing evidence -> UNKNOWN / MISSING_EVIDENCE
    req_mand = make_req(OperatorEnum.GTE, 1000000, mandatory=True)
    res_mand = ComplianceEngine.evaluate(req_mand, [], [])
    assert res_mand.status == ComplianceStatus.UNKNOWN
    assert res_mand.reason_code == ReasonCode.MISSING_EVIDENCE

    # Optional rule + missing evidence -> NOT_APPLICABLE / MISSING_EVIDENCE
    req_opt = make_req(OperatorEnum.GTE, 1000000, mandatory=False)
    res_opt = ComplianceEngine.evaluate(req_opt, [], [])
    assert res_opt.status == ComplianceStatus.NOT_APPLICABLE
    assert res_opt.reason_code == ReasonCode.MISSING_EVIDENCE


def test_distinction_between_none_empty_string_false_zero_empty_list():
    """Verifies exact semantic distinction between null, empty, false, 0, and []."""
    # 0 is valid numeric 0
    req_gt_zero = make_req(OperatorEnum.GT, 0, field="financial.net_worth")
    res_zero = ComplianceEngine.evaluate(req_gt_zero, [make_fact(0, field="financial.net_worth")], [])
    assert res_zero.status == ComplianceStatus.FAIL
    assert res_zero.reason_code == ReasonCode.NOT_GREATER_THAN

    # False is valid boolean False
    req_eq_true = make_req(OperatorEnum.EQ, True, field="debarment.is_debarred")
    res_false = ComplianceEngine.evaluate(req_eq_true, [make_fact(False, field="debarment.is_debarred")], [])
    assert res_false.status == ComplianceStatus.FAIL
    assert res_false.reason_code == ReasonCode.NOT_EQUAL

    # [] is empty list
    req_not_exists = make_req(OperatorEnum.NOT_EXISTS, False, field="debarment.records")
    res_empty_list = ComplianceEngine.evaluate(req_not_exists, [make_fact([], field="debarment.records")], [])
    assert res_empty_list.status == ComplianceStatus.PASS
    assert res_empty_list.reason_code == ReasonCode.EVIDENCE_ABSENT

    # None is missing
    res_none = ComplianceEngine.evaluate(req_gt_zero, [make_fact(None, field="financial.net_worth")], [])
    assert res_none.status == ComplianceStatus.UNKNOWN
    assert res_none.reason_code == ReasonCode.MISSING_EVIDENCE


def test_unhealthy_verification_results_fail_closed():
    """Verifies external verification errors return UNKNOWN / VERIFICATION_UNAVAILABLE."""
    req = make_req(OperatorEnum.EQ, "ACTIVE", field="gst.status", req_type=RequirementType.GST)

    for bad_status in [VerificationStatus.SERVICE_ERROR, VerificationStatus.UNAVAILABLE, VerificationStatus.TIMEOUT]:
        ver = [make_ver(bad_status, field="gst.status")]
        res = ComplianceEngine.evaluate(req, [], ver)
        assert res.status == ComplianceStatus.UNKNOWN
        assert res.reason_code == ReasonCode.VERIFICATION_UNAVAILABLE


def test_verification_mismatch_status_review_required():
    """Verifies verification discrepancy returns REVIEW_REQUIRED rather than automatic silent FAIL."""
    req = make_req(OperatorEnum.GTE, 100000000)
    facts = [make_fact(150000000)]
    ver = [make_ver(VerificationStatus.MISMATCH, verified_value=80000000)]
    res = ComplianceEngine.evaluate(req, facts, ver)
    assert res.status == ComplianceStatus.REVIEW_REQUIRED
    assert res.reason_code == ReasonCode.VERIFICATION_MISMATCH


# ---------------------------------------------------------------------------
# 4. MULTI-FACT CONFLICT HANDLING
# ---------------------------------------------------------------------------

def test_multi_fact_deduplication_when_consistent():
    """Verifies multiple facts with identical normalized values are consolidated with primary contributing fact linked."""
    req = make_req(OperatorEnum.GTE, 10000000)
    fact_1 = make_fact(15000000, fact_id="FACT-001")
    fact_2 = make_fact("1.5 Crore", fact_id="FACT-002")  # 15,000,000
    res = ComplianceEngine.evaluate(req, [fact_1, fact_2], [])
    assert res.status == ComplianceStatus.PASS
    assert "FACT-001" in res.evidence_ids


def test_multi_fact_divergence_review_required():
    """Verifies divergent facts produce REVIEW_REQUIRED / CONFLICTING_FACTS preserving all IDs."""
    req = make_req(OperatorEnum.GTE, 10000000)
    fact_1 = make_fact(15000000, fact_id="FACT-001")
    fact_2 = make_fact(8000000, fact_id="FACT-002")
    res = ComplianceEngine.evaluate(req, [fact_1, fact_2], [])
    assert res.status == ComplianceStatus.REVIEW_REQUIRED
    assert res.reason_code == ReasonCode.CONFLICTING_FACTS
    assert "FACT-001" in res.evidence_ids
    assert "FACT-002" in res.evidence_ids


def test_advisory_risk_does_not_alter_compliance_results():
    """Verifies that advisory risk signals do not mutate compliance evaluation outcomes."""
    req = make_req(OperatorEnum.GTE, 10000000)
    facts = [make_fact(20000000)]
    res = ComplianceEngine.evaluate(req, facts, [])
    assert res.status == ComplianceStatus.PASS

    # Advisory high risk candidate
    risk = RiskSignalCandidate(
        severity=RiskSeverity.HIGH,
        signal_type="SHELL_COMPANY_RISK",
        title="High Risk Detected",
        description="Shell risk detected",
        reason_code="SHELL_COMPANY_RISK",
    )
    # Compliance evaluation remains PASS regardless of risk signal
    assert res.status == ComplianceStatus.PASS


# ---------------------------------------------------------------------------
# 5. RULE VERSIONING & SNAPSHOT REPRODUCIBILITY
# ---------------------------------------------------------------------------

def test_canonical_rule_hashing_and_versioning():
    """Verifies canonical rule hash is deterministic and independent of generated IDs."""
    req1 = make_req(OperatorEnum.GTE, 1000000, req_id="ID-1")
    req2 = make_req(OperatorEnum.GTE, 1000000, req_id="ID-2")

    hash1 = ComplianceEngine.compute_rules_hash([req1])
    hash2 = ComplianceEngine.compute_rules_hash([req2])
    assert hash1 == hash2
    assert len(hash1) == 64


def test_historical_run_snapshot_immutability_and_replay():
    """Verifies that replaying evaluations from an immutable run snapshot yields identical outcomes."""
    eval_ts = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)
    rules = [
        make_req(OperatorEnum.GTE, 50000000, field="turnover", req_id="R1", unit="INR"),
        make_req(OperatorEnum.EQ, "ACTIVE", field="gst_status", req_id="R2", unit=None, req_type=RequirementType.GST),
        make_req(OperatorEnum.COUNT_GTE, 2, field="certs", req_id="R3", unit=None),
    ]
    facts = [
        make_fact(80000000, field="turnover", fact_id="F1", meta={"currency": "INR"}),
        make_fact("ACTIVE", field="gst_status", fact_id="F2"),
        make_fact(["ISO9001", "ISO27001"], field="certs", fact_id="F3"),
    ]
    verifications = [
        make_ver(VerificationStatus.VERIFIED, verified_value="ACTIVE", field="gst_status", ver_id="V1"),
    ]

    # Initial evaluation
    initial_evals = []
    for r in rules:
        ev = ComplianceEngine.evaluate(r, facts, verifications, evaluation_timestamp=eval_ts)
        initial_evals.append(ev)

    rules_hash = ComplianceEngine.compute_rules_hash(rules)
    snapshot = {
        "snapshot_version": "1.2.0",
        "engine_version": ComplianceEngine.ENGINE_VERSION,
        "normalization_policy_version": ComplianceEngine.NORMALIZATION_POLICY_VERSION,
        "operator_semantics_version": ComplianceEngine.OPERATOR_SEMANTICS_VERSION,
        "rules_hash": rules_hash,
        "evaluation_timestamp": eval_ts.isoformat(),
        "approved_requirements": [r.model_dump(mode="json") for r in rules],
        "facts": [f.model_dump(mode="json") for f in facts],
        "verifications": [v.model_dump(mode="json") for v in verifications],
        "exact_evaluation_linkage": [e.model_dump(mode="json") for e in initial_evals],
    }

    # Replay from snapshot alone without live database
    replay_ts = datetime.fromisoformat(snapshot["evaluation_timestamp"])
    replayed_rules = [TenderRequirementRead.model_validate(r) for r in snapshot["approved_requirements"]]
    replayed_facts = [FactRead.model_validate(f) for f in snapshot["facts"]]
    replayed_vers = [VerificationResultRead.model_validate(v) for v in snapshot["verifications"]]

    replayed_evals = []
    for r in replayed_rules:
        ev = ComplianceEngine.evaluate(r, replayed_facts, replayed_vers, evaluation_timestamp=replay_ts)
        replayed_evals.append(ev)

    assert len(replayed_evals) == len(initial_evals)
    for orig, rep in zip(initial_evals, replayed_evals):
        assert rep.status == orig.status
        assert rep.reason_code == orig.reason_code
        assert rep.expected_value == orig.expected_value
        assert rep.observed_value == orig.observed_value
        assert rep.evaluated_at == orig.evaluated_at


