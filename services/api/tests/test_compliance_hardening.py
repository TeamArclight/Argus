from datetime import date, datetime, timezone
from decimal import Decimal
import pytest

from app.compliance.engine import ComplianceEngine, FinancialContext
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
    meta: dict | None = None,
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
        metadata_json=meta or {},
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
# 1. FINANCIAL CONTEXT & SCALE PARSER REGRESSIONS
# ---------------------------------------------------------------------------

def test_financial_context_scale_equivalences():
    """5 Crore INR == 50,000,000 INR."""
    req = make_req(OperatorEnum.EQ, "50000000 INR", unit="INR")
    facts = [make_fact("5 Crore INR")]
    res = ComplianceEngine.evaluate(req, facts, [])
    assert res.status == ComplianceStatus.PASS
    assert res.reason_code == ReasonCode.EQUAL


def test_financial_context_currency_mismatch():
    """5 Crore INR != 5 Crore USD (fails with CURRENCY_MISMATCH)."""
    req = make_req(OperatorEnum.EQ, "5 Crore INR", unit="INR")
    facts = [make_fact("5 Crore USD")]
    res = ComplianceEngine.evaluate(req, facts, [])
    assert res.status == ComplianceStatus.REVIEW_REQUIRED
    assert res.reason_code == ReasonCode.CURRENCY_MISMATCH


def test_financial_base_unit_not_multiplied_again():
    """Base-unit 50,000,000 INR with is_base_unit=True must not be scaled again."""
    req = make_req(OperatorEnum.EQ, 50000000, unit="INR")
    facts = [make_fact(50000000, meta={"is_base_unit": True, "currency": "INR"})]
    res = ComplianceEngine.evaluate(req, facts, [])
    assert res.status == ComplianceStatus.PASS
    assert res.reason_code == ReasonCode.EQUAL


def test_financial_conflicting_text_and_metadata_scales():
    """Contradictory scale in metadata vs text returns UNIT_MISMATCH."""
    req = make_req(OperatorEnum.GTE, 10000000, unit="INR")
    facts = [make_fact("5 Crore INR", meta={"unit": "Lakh", "currency": "INR"})]
    res = ComplianceEngine.evaluate(req, facts, [])
    assert res.status == ComplianceStatus.REVIEW_REQUIRED
    assert res.reason_code == ReasonCode.UNIT_MISMATCH


def test_financial_missing_required_currency():
    """Financial rule with explicit currency comparing against number missing currency returns MISSING_FINANCIAL_CONTEXT."""
    req = make_req(OperatorEnum.GTE, "50000000 INR", unit="INR", meta={"currency": "INR"})
    facts = [make_fact("50000000")]  # No currency in text or metadata
    res = ComplianceEngine.evaluate(req, facts, [])
    assert res.status == ComplianceStatus.REVIEW_REQUIRED
    assert res.reason_code == ReasonCode.MISSING_FINANCIAL_CONTEXT


def test_financial_year_mismatch_and_missing():
    """Mismatched FY returns FINANCIAL_CONTEXT_MISMATCH; missing FY returns FINANCIAL_CONTEXT_MISMATCH."""
    req_fy = make_req(OperatorEnum.GTE, 10000000, meta={"fy": "2023-2024", "currency": "INR"})

    # Mismatched FY
    facts_mismatch = [make_fact(20000000, meta={"fy": "2022-2023", "currency": "INR"})]
    res_mismatch = ComplianceEngine.evaluate(req_fy, facts_mismatch, [])
    assert res_mismatch.status == ComplianceStatus.REVIEW_REQUIRED
    assert res_mismatch.reason_code == ReasonCode.FINANCIAL_CONTEXT_MISMATCH

    # Missing FY
    facts_missing = [make_fact(20000000, meta={"currency": "INR"})]
    res_missing = ComplianceEngine.evaluate(req_fy, facts_missing, [])
    assert res_missing.status == ComplianceStatus.REVIEW_REQUIRED
    assert res_missing.reason_code == ReasonCode.FINANCIAL_CONTEXT_MISMATCH


def test_financial_averaging_period_and_metric_mismatch():
    """Mismatched averaging period or financial metric returns FINANCIAL_CONTEXT_MISMATCH."""
    req = make_req(
        OperatorEnum.GTE,
        10000000,
        meta={"averaging_period": "3_years", "metric": "annual_turnover", "currency": "INR"},
    )

    # Different period
    facts_period = [make_fact(20000000, meta={"averaging_period": "1_year", "metric": "annual_turnover", "currency": "INR"})]
    res_period = ComplianceEngine.evaluate(req, facts_period, [])
    assert res_period.status == ComplianceStatus.REVIEW_REQUIRED
    assert res_period.reason_code == ReasonCode.FINANCIAL_CONTEXT_MISMATCH

    # Different metric
    facts_metric = [make_fact(20000000, meta={"averaging_period": "3_years", "metric": "net_worth", "currency": "INR"})]
    res_metric = ComplianceEngine.evaluate(req, facts_metric, [])
    assert res_metric.status == ComplianceStatus.REVIEW_REQUIRED
    assert res_metric.reason_code == ReasonCode.FINANCIAL_CONTEXT_MISMATCH


def test_input_scale_independence_rule_unit_not_applied_to_input():
    """Rule unit 'Crore' must not be applied to input '50000000 INR'."""
    req = make_req(OperatorEnum.GTE, 5, unit="Crore", meta={"currency": "INR"})
    # Observed fact has 50000000 INR (base units)
    facts = [make_fact("50000000 INR")]
    res = ComplianceEngine.evaluate(req, facts, [])
    assert res.status == ComplianceStatus.PASS
    assert res.reason_code == ReasonCode.GREATER_THAN_OR_EQUAL


# ---------------------------------------------------------------------------
# 2. EXACT NUMERIC & COUNT BEHAVIOR
# ---------------------------------------------------------------------------

def test_count_gte_exact_integer_behavior():
    """COUNT_GTE rejects floats, negative numbers, and booleans."""
    req = make_req(OperatorEnum.COUNT_GTE, 3, field="general.certifications_count", unit=None)

    # Valid integers
    res_pass = ComplianceEngine.evaluate(req, [make_fact(3, field="general.certifications_count")], [])
    assert res_pass.status == ComplianceStatus.PASS
    assert res_pass.reason_code == ReasonCode.COUNT_SUFFICIENT

    res_fail = ComplianceEngine.evaluate(req, [make_fact(2, field="general.certifications_count")], [])
    assert res_fail.status == ComplianceStatus.FAIL
    assert res_fail.reason_code == ReasonCode.COUNT_INSUFFICIENT

    # Floats rejected for count
    res_float = ComplianceEngine.evaluate(req, [make_fact(3.5, field="general.certifications_count")], [])
    assert res_float.status == ComplianceStatus.REVIEW_REQUIRED
    assert res_float.reason_code == ReasonCode.TYPE_CONVERSION_ERROR

    # Boolean rejected
    res_bool = ComplianceEngine.evaluate(req, [make_fact(True, field="general.certifications_count")], [])
    assert res_bool.status == ComplianceStatus.REVIEW_REQUIRED
    assert res_bool.reason_code == ReasonCode.TYPE_CONVERSION_ERROR


def test_numeric_bounds_and_malformed_rejection():
    """Exceedingly long strings, NaN, Infinity, and invalid commas are rejected."""
    req = make_req(OperatorEnum.GT, 100, unit=None)

    # NaN / Inf
    assert ComplianceEngine.evaluate(req, [make_fact("NaN", field=req.field)], []).status == ComplianceStatus.REVIEW_REQUIRED
    assert ComplianceEngine.evaluate(req, [make_fact("Infinity", field=req.field)], []).status == ComplianceStatus.REVIEW_REQUIRED

    # Input length bound > 100 chars
    long_str = "1" * 150
    assert ComplianceEngine.evaluate(req, [make_fact(long_str, field=req.field)], []).status == ComplianceStatus.REVIEW_REQUIRED

    # Invalid comma placement
    assert ComplianceEngine.evaluate(req, [make_fact("1,2345,678", field=req.field)], []).status == ComplianceStatus.REVIEW_REQUIRED
    assert ComplianceEngine.evaluate(req, [make_fact("123,45.67,8", field=req.field)], []).status == ComplianceStatus.REVIEW_REQUIRED


# ---------------------------------------------------------------------------
# 3. TEMPORAL SEMANTICS & POLICIES
# ---------------------------------------------------------------------------

def test_date_only_leap_year_comparisons():
    """Date-only comparisons handle leap years strictly on calendar days."""
    req_leap = make_req(OperatorEnum.DATE_BEFORE, "2024-02-29", field="general.incorporation_date", unit=None)

    # 2024-02-28 is BEFORE 2024-02-29
    res1 = ComplianceEngine.evaluate(req_leap, [make_fact("2024-02-28", field="general.incorporation_date")], [])
    assert res1.status == ComplianceStatus.PASS
    assert res1.reason_code == ReasonCode.BEFORE_DATE

    # 2024-02-29 is NOT BEFORE 2024-02-29
    res2 = ComplianceEngine.evaluate(req_leap, [make_fact("2024-02-29", field="general.incorporation_date")], [])
    assert res2.status == ComplianceStatus.FAIL
    assert res2.reason_code == ReasonCode.ON_OR_AFTER_DATE


def test_datetime_timezone_aware_normalization():
    """Aware datetimes across timezones are compared via UTC conversion."""
    req_dt = make_req(OperatorEnum.DATE_AFTER, "2024-05-01T04:30:00Z", field="audit.timestamp", unit=None)

    # 2024-05-01T10:00:00+05:30 is 04:30:00 UTC (not after)
    res_eq = ComplianceEngine.evaluate(req_dt, [make_fact("2024-05-01T10:00:00+05:30", field="audit.timestamp")], [])
    assert res_eq.status == ComplianceStatus.FAIL
    assert res_eq.reason_code == ReasonCode.ON_OR_BEFORE_DATE

    # 2024-05-01T10:01:00+05:30 is 04:31:00 UTC (after)
    res_after = ComplianceEngine.evaluate(req_dt, [make_fact("2024-05-01T10:01:00+05:30", field="audit.timestamp")], [])
    assert res_after.status == ComplianceStatus.PASS
    assert res_after.reason_code == ReasonCode.AFTER_DATE


def test_datetime_naive_vs_aware_ambiguity():
    """Comparing aware datetime with naive datetime returns AMBIGUOUS_TIMEZONE without guessing."""
    req_aware = make_req(OperatorEnum.DATE_BEFORE, "2024-05-01T10:00:00Z", field="audit.timestamp", unit=None)
    facts_naive = [make_fact("2024-05-01 10:00:00", field="audit.timestamp")]  # Naive datetime
    res = ComplianceEngine.evaluate(req_aware, facts_naive, [])
    assert res.status == ComplianceStatus.REVIEW_REQUIRED
    assert res.reason_code == ReasonCode.AMBIGUOUS_TIMEZONE


def test_mixed_date_only_and_datetime_mismatch():
    """Comparing date-only (YYYY-MM-DD) with datetime (ISO with time) returns TEMPORAL_CONTEXT_MISMATCH."""
    req_date = make_req(OperatorEnum.DATE_BEFORE, "2024-05-01", field="general.incorporation_date", unit=None)
    facts_dt = [make_fact("2024-05-01T14:30:00Z", field="general.incorporation_date")]
    res = ComplianceEngine.evaluate(req_date, facts_dt, [])
    assert res.status == ComplianceStatus.REVIEW_REQUIRED
    assert res.reason_code == ReasonCode.TEMPORAL_CONTEXT_MISMATCH


# ---------------------------------------------------------------------------
# 4. OPTIONALITY & APPLICABILITY TRUTH TABLE & STRICT STRING PARSING
# ---------------------------------------------------------------------------

def test_applicability_truth_table():
    """Tests all combinations of mandatory/optional and applicability policy."""
    # 1. Mandatory + Applicable + Missing -> UNKNOWN (MISSING_EVIDENCE)
    req_mand_app = make_req(OperatorEnum.EXISTS, True, mandatory=True, meta={"applicable": True})
    assert ComplianceEngine.evaluate(req_mand_app, [], []).status == ComplianceStatus.UNKNOWN
    assert ComplianceEngine.evaluate(req_mand_app, [], []).reason_code == ReasonCode.MISSING_EVIDENCE

    # 2. Mandatory + Inapplicable -> NOT_APPLICABLE (NOT_APPLICABLE_EXPLICIT)
    req_mand_inapp = make_req(OperatorEnum.EXISTS, True, mandatory=True, meta={"applicable": False})
    assert ComplianceEngine.evaluate(req_mand_inapp, [], []).status == ComplianceStatus.NOT_APPLICABLE
    assert ComplianceEngine.evaluate(req_mand_inapp, [], []).reason_code == ReasonCode.NOT_APPLICABLE_EXPLICIT

    # 3. Mandatory + Unknown applicability + Missing -> UNKNOWN (MISSING_EVIDENCE)
    req_mand_unk = make_req(OperatorEnum.EXISTS, True, mandatory=True, meta={})
    assert ComplianceEngine.evaluate(req_mand_unk, [], []).status == ComplianceStatus.UNKNOWN
    assert ComplianceEngine.evaluate(req_mand_unk, [], []).reason_code == ReasonCode.MISSING_EVIDENCE

    # 4. Optional + Inapplicable -> NOT_APPLICABLE (NOT_APPLICABLE_EXPLICIT)
    req_opt_inapp = make_req(OperatorEnum.EXISTS, True, mandatory=False, meta={"applicable": False})
    assert ComplianceEngine.evaluate(req_opt_inapp, [], []).status == ComplianceStatus.NOT_APPLICABLE
    assert ComplianceEngine.evaluate(req_opt_inapp, [], []).reason_code == ReasonCode.NOT_APPLICABLE_EXPLICIT

    # 5. Optional + Missing Policy Exemption -> NOT_APPLICABLE (NOT_APPLICABLE_OPTIONAL)
    req_opt_exempt = make_req(OperatorEnum.EXISTS, True, mandatory=False, meta={"optional_missing_policy": "NOT_APPLICABLE"})
    assert ComplianceEngine.evaluate(req_opt_exempt, [], []).status == ComplianceStatus.NOT_APPLICABLE
    assert ComplianceEngine.evaluate(req_opt_exempt, [], []).reason_code == ReasonCode.NOT_APPLICABLE_OPTIONAL

    # 6. Optional + Applicable without exemption + Missing -> UNKNOWN (MISSING_EVIDENCE)
    req_opt_app = make_req(OperatorEnum.EXISTS, True, mandatory=False, meta={"applicable": True})
    assert ComplianceEngine.evaluate(req_opt_app, [], []).status == ComplianceStatus.UNKNOWN
    assert ComplianceEngine.evaluate(req_opt_app, [], []).reason_code == ReasonCode.MISSING_EVIDENCE


def test_applicability_string_parsing_and_invalids():
    """Strictly parses boolean strings and rejects invalid applicability strings."""
    # is_applicable="false"
    req_false_str = make_req(OperatorEnum.EXISTS, True, mandatory=True, meta={"is_applicable": "false"})
    res_false = ComplianceEngine.evaluate(req_false_str, [], [])
    assert res_false.status == ComplianceStatus.NOT_APPLICABLE
    assert res_false.reason_code == ReasonCode.NOT_APPLICABLE_EXPLICIT

    # is_applicable="true"
    req_true_str = make_req(OperatorEnum.EXISTS, True, mandatory=True, meta={"is_applicable": "true"})
    res_true = ComplianceEngine.evaluate(req_true_str, [], [])
    assert res_true.status == ComplianceStatus.UNKNOWN  # Missing evidence on applicable rule
    assert res_true.reason_code == ReasonCode.MISSING_EVIDENCE

    # is_applicable="invalid"
    req_invalid_str = make_req(OperatorEnum.EXISTS, True, mandatory=True, meta={"is_applicable": "maybe_applicable"})
    res_inv = ComplianceEngine.evaluate(req_invalid_str, [], [])
    assert res_inv.status == ComplianceStatus.UNKNOWN
    assert res_inv.reason_code == ReasonCode.INVALID_APPLICABILITY_POLICY


def test_approved_and_unapproved_exemptions():
    """Approved exemptions return NOT_APPLICABLE_EXEMPTION, unapproved returns INVALID_APPLICABILITY_POLICY, unverified context returns UNVERIFIED_EXEMPTION_ELIGIBILITY."""
    # Exemption policy on rule with verified context
    req_app_ex = make_req(OperatorEnum.EXISTS, True, meta={"exemption": "APPROVED"})
    res_ex_approved = ComplianceEngine.evaluate(req_app_ex, [], [], context={"exemption_approved": True})
    assert res_ex_approved.status == ComplianceStatus.NOT_APPLICABLE
    assert res_ex_approved.reason_code == ReasonCode.NOT_APPLICABLE_EXEMPTION

    # Exemption policy on rule without verified context
    res_ex_unverified = ComplianceEngine.evaluate(req_app_ex, [], [], context={})
    assert res_ex_unverified.status == ComplianceStatus.UNKNOWN
    assert res_ex_unverified.reason_code == ReasonCode.UNVERIFIED_EXEMPTION_ELIGIBILITY

    # Unrecognized / invalid exemption policy string on rule
    req_unapp_ex = make_req(OperatorEnum.EXISTS, True, meta={"exemption": "random_self_claimed_exemption"})
    res_unapp = ComplianceEngine.evaluate(req_unapp_ex, [], [])
    assert res_unapp.status == ComplianceStatus.UNKNOWN
    assert res_unapp.reason_code == ReasonCode.INVALID_APPLICABILITY_POLICY


def test_mandatory_missing_not_overridden_by_optional_policy():
    """Mandatory requirement missing evidence must NOT be overridden by optional_missing_policy."""
    req_mand = make_req(
        OperatorEnum.EXISTS,
        True,
        mandatory=True,
        meta={"optional_missing_policy": "NOT_APPLICABLE"},
    )
    res = ComplianceEngine.evaluate(req_mand, [], [])
    assert res.status == ComplianceStatus.UNKNOWN
    assert res.reason_code == ReasonCode.MISSING_EVIDENCE


def test_categorical_bidder_applicability_and_missing_category():
    """Rule targeting specific bidder categories; missing bidder category is NOT treated as mismatch."""
    req_msme = make_req(
        OperatorEnum.EXISTS,
        True,
        mandatory=True,
        meta={"applicable_bidder_types": ["MSME", "STARTUP"]},
    )

    # Missing bidder category -> not treated as mismatch -> UNKNOWN (MISSING_EVIDENCE)
    res_missing_cat = ComplianceEngine.evaluate(req_msme, [], [], context={})
    assert res_missing_cat.status == ComplianceStatus.UNKNOWN
    assert res_missing_cat.reason_code == ReasonCode.MISSING_EVIDENCE

    # MSME bidder -> applicable, missing evidence -> UNKNOWN
    res_msme = ComplianceEngine.evaluate(req_msme, [], [], context={"bidder_category": "MSME"})
    assert res_msme.status == ComplianceStatus.UNKNOWN
    assert res_msme.reason_code == ReasonCode.MISSING_EVIDENCE

    # LARGE_ENTERPRISE bidder -> not applicable -> NOT_APPLICABLE
    res_large = ComplianceEngine.evaluate(req_msme, [], [], context={"bidder_category": "LARGE_ENTERPRISE"})
    assert res_large.status == ComplianceStatus.NOT_APPLICABLE
    assert res_large.reason_code == ReasonCode.NOT_APPLICABLE_EXPLICIT


# ---------------------------------------------------------------------------
# 5. EXPLICIT EVALUATION CLOCK & CONFLICT RESOLUTION
# ---------------------------------------------------------------------------

def test_missing_evaluation_clock_on_time_dependent_rule_no_1970():
    """Time-dependent rule missing evaluation clock returns MISSING_EVALUATION_CLOCK with evaluated_at=None."""
    req_time = make_req(
        OperatorEnum.DATE_BEFORE,
        "now",
        field="general.registration_expiry",
        meta={"is_time_dependent": True},
    )
    res = ComplianceEngine.evaluate(req_time, [make_fact("2025-01-01")], [], context={})
    assert res.status == ComplianceStatus.UNKNOWN
    assert res.reason_code == ReasonCode.MISSING_EVALUATION_CLOCK
    assert res.evaluated_at is None


def test_multi_fact_conflicts_linking_all_evidence():
    """Conflicting facts across financial years or values return CONFLICTING_FACTS linking all fact IDs."""
    req = make_req(OperatorEnum.GTE, 10000000, meta={"currency": "INR"})
    f1 = make_fact(20000000, fact_id="FACT-1", meta={"currency": "INR", "fy": "2022-2023"})
    f2 = make_fact(30000000, fact_id="FACT-2", meta={"currency": "INR", "fy": "2023-2024"})

    res = ComplianceEngine.evaluate(req, [f1, f2], [])
    assert res.status == ComplianceStatus.REVIEW_REQUIRED
    assert res.reason_code == ReasonCode.CONFLICTING_FACTS
    assert set(res.evidence_ids) == {"FACT-1", "FACT-2"}


# ---------------------------------------------------------------------------
# 6. CANONICAL RULE HASH & HISTORICAL REPLAY BOUNDS
# ---------------------------------------------------------------------------

def test_canonical_rule_hash_includes_policy_excludes_ids():
    """Rule hashing includes semantic policy metadata and excludes generated IDs and timestamps."""
    r1 = make_req(OperatorEnum.GTE, 5000000, req_id="REQ-AAA", meta={"currency": "INR", "fy": "2023-2024"})
    r2 = make_req(OperatorEnum.GTE, 5000000, req_id="REQ-BBB", meta={"currency": "INR", "fy": "2023-2024"})

    # Different generated IDs must produce the EXACT same rules_hash
    hash1 = ComplianceEngine.compute_rules_hash([r1])
    hash2 = ComplianceEngine.compute_rules_hash([r2])
    assert hash1 == hash2

    # Different policy metadata must produce DIFFERENT rules_hash
    r3 = make_req(OperatorEnum.GTE, 5000000, req_id="REQ-AAA", meta={"currency": "USD", "fy": "2023-2024"})
    hash3 = ComplianceEngine.compute_rules_hash([r3])
    assert hash1 != hash3


def test_historical_replay_bounds_unsupported_version():
    """Replaying historical snapshot with unsupported engine version returns HISTORICAL_VERSION_UNSUPPORTED."""
    snapshot = {
        "engine_version": "0.9.0",  # Unsupported legacy version
        "bidder_id": "BIDDER-101",
        "rule_evaluations": [],
    }
    res = ComplianceEngine.replay_evaluation(snapshot, "REQ-101")
    assert res.status == ComplianceStatus.REVIEW_REQUIRED
    assert res.reason_code == ReasonCode.HISTORICAL_VERSION_UNSUPPORTED


def test_historical_replay_missing_rule_evaluation():
    """Replaying historical snapshot when rule evaluation is absent returns HISTORICAL_EVALUATION_NOT_FOUND."""
    snapshot = {
        "engine_version": "2.0.0",
        "bidder_id": "BIDDER-101",
        "started_at": "2026-01-15T10:00:00+00:00",
        "rule_evaluations": [],
    }
    res = ComplianceEngine.replay_evaluation(snapshot, "REQ-101")
    assert res.status == ComplianceStatus.UNKNOWN
    assert res.reason_code == ReasonCode.HISTORICAL_EVALUATION_NOT_FOUND
    assert res.evaluated_at == datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)


def test_invalid_context_cannot_fallback_to_raw_numeric_equality():
    """Different currency or missing required financial metadata must not fall back to raw number equality."""
    # 50,000,000 INR vs 50,000,000 USD (same raw number, different currency)
    req_curr = make_req(OperatorEnum.EQ, "50000000 INR", unit="INR", meta={"currency": "INR"})
    facts_curr = [make_fact("50000000 USD", meta={"currency": "USD"})]
    res_curr = ComplianceEngine.evaluate(req_curr, facts_curr, [])
    assert res_curr.status == ComplianceStatus.REVIEW_REQUIRED
    assert res_curr.reason_code == ReasonCode.CURRENCY_MISMATCH

    # Same raw number, but fact is missing required currency
    facts_no_curr = [make_fact(50000000)]
    res_no_curr = ComplianceEngine.evaluate(req_curr, facts_no_curr, [])
    assert res_no_curr.status == ComplianceStatus.REVIEW_REQUIRED
    assert res_no_curr.reason_code == ReasonCode.MISSING_FINANCIAL_CONTEXT


def test_non_financial_numeric_unaffected():
    """Non-financial numeric and count fields compare cleanly without requiring financial metadata."""
    req_exp = make_req(
        OperatorEnum.GTE,
        5,
        field="general.experience_years",
        unit="years",
        req_type=RequirementType.EXPERIENCE,
        meta={},
    )
    facts_exp = [make_fact(7, field="general.experience_years")]
    res_exp = ComplianceEngine.evaluate(req_exp, facts_exp, [])
    assert res_exp.status == ComplianceStatus.PASS
    assert res_exp.reason_code == ReasonCode.GREATER_THAN_OR_EQUAL


# ---------------------------------------------------------------------------
# 7. REPRESENTATION FLAGS & EVIDENCE-BACKED EXEMPTIONS
# ---------------------------------------------------------------------------

def test_representation_flags_strict_parsing():
    """Strict boolean representation flags: 'false' is not base unit, 'true' is base unit, malformed is rejected."""
    # is_base_unit='false' with unit='Crore' -> multiplier 10,000,000 applies
    ctx_scaled = ComplianceEngine._parse_financial_context(5, {"currency": "INR", "unit": "Crore", "is_base_unit": "false"})
    assert ctx_scaled.is_valid is True
    assert ctx_scaled.base_decimal_value == Decimal("50000000")

    # is_base_unit='true' with unit='Crore' -> already normalized base unit, multiplier not applied again
    ctx_base = ComplianceEngine._parse_financial_context(50000000, {"currency": "INR", "unit": "Crore", "is_base_unit": "true"})
    assert ctx_base.is_valid is True
    assert ctx_base.base_decimal_value == Decimal("50000000")

    # is_base_unit='maybe' -> malformed boolean representation flag
    ctx_malformed = ComplianceEngine._parse_financial_context(5, {"currency": "INR", "unit": "Crore", "is_base_unit": "maybe"})
    assert ctx_malformed.is_valid is False
    assert ctx_malformed.error_reason == ReasonCode.MALFORMED_NUMBER


def test_unsupported_unit_scale_rejected():
    """Unsupported unit scale in metadata is rejected with UNIT_MISMATCH."""
    ctx_unsupported = ComplianceEngine._parse_financial_context(5, {"currency": "INR", "unit": "lightyears"})
    assert ctx_unsupported.is_valid is False
    assert ctx_unsupported.error_reason == ReasonCode.UNIT_MISMATCH


def test_evidence_backed_exemption_policy():
    """Bare exemption string in rule without verified bidder context returns UNVERIFIED_EXEMPTION_ELIGIBILITY."""
    req_ex = make_req(
        OperatorEnum.GTE,
        5000000,
        meta={"exemption": "MSME"},
    )
    # Unverified context -> UNVERIFIED_EXEMPTION_ELIGIBILITY
    res_unverified = ComplianceEngine.evaluate(req_ex, [], [], context={})
    assert res_unverified.status == ComplianceStatus.UNKNOWN
    assert res_unverified.reason_code == ReasonCode.UNVERIFIED_EXEMPTION_ELIGIBILITY

    # Verified exemption -> NOT_APPLICABLE_EXEMPTION
    res_verified = ComplianceEngine.evaluate(req_ex, [], [], context={"exemption_approved": True})
    assert res_verified.status == ComplianceStatus.NOT_APPLICABLE
    assert res_verified.reason_code == ReasonCode.NOT_APPLICABLE_EXEMPTION


