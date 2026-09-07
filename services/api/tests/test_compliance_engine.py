from datetime import datetime, timezone
import pytest
from app.compliance.engine import ComplianceEngine
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
from app.services.bid_verification_service import BidVerificationService


def make_requirement(
    operator: OperatorEnum,
    expected_value: any,
    field: str = "financial.average_annual_turnover",
    mandatory: bool = True,
    req_type: RequirementType | None = None,
    meta: dict | None = None,
) -> TenderRequirementRead:
    if req_type is None:
        req_type = RequirementType.TURNOVER if "financial" in field else RequirementType.CUSTOM
    return TenderRequirementRead(
        id="REQ-001",
        tender_id="TENDER-001",
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
        metadata_json=meta or ({"currency": "INR"} if "financial" in field else {}),
        created_at=datetime.now(timezone.utc),
    )


def make_fact(value: any, field: str = "financial.average_annual_turnover", fact_id: str = "FACT-001", meta: dict | None = None) -> FactRead:
    metadata = {"currency": "INR"} if "financial" in field else {}
    if meta:
        metadata.update(meta)
    return FactRead(
        id=fact_id,
        document_id="DOC-001",
        bidder_id="BIDDER-001",
        field=field,
        value=value,
        confidence=1.0,
        metadata_json=metadata,
        created_at=datetime.now(timezone.utc),
    )


def make_verification(
    status: VerificationStatus,
    verified_value: any = None,
    field: str = "financial.average_annual_turnover",
    ver_id: str = "VER-001",
) -> VerificationResultRead:
    return VerificationResultRead(
        id=ver_id,
        bidder_id="BIDDER-001",
        field=field,
        claimed_value=150000000,
        verified_value=verified_value,
        status=status,
        source=VerificationSource.GST_DEMO_DATA,
        checked_at=datetime.now(timezone.utc),
    )


def test_compliance_gte_pass():
    req = make_requirement(OperatorEnum.GTE, 100000000)
    facts = [make_fact(150000000)]
    eval_res = ComplianceEngine.evaluate(req, facts, [])
    assert eval_res.status == ComplianceStatus.PASS
    assert eval_res.reason_code == "GREATER_THAN_OR_EQUAL"


def test_compliance_gte_fail():
    req = make_requirement(OperatorEnum.GTE, 100000000)
    facts = [make_fact(80000000)]
    eval_res = ComplianceEngine.evaluate(req, facts, [])
    assert eval_res.status == ComplianceStatus.FAIL
    assert eval_res.reason_code == "LESS_THAN"


def test_compliance_missing_facts_mandatory_unknown():
    req = make_requirement(OperatorEnum.GTE, 100000000, mandatory=True)
    eval_res = ComplianceEngine.evaluate(req, [], [])
    assert eval_res.status == ComplianceStatus.UNKNOWN
    assert eval_res.reason_code == "MISSING_EVIDENCE"


def test_compliance_missing_facts_optional_not_applicable():
    req = make_requirement(OperatorEnum.GTE, 100000000, mandatory=False, meta={"optional_missing_policy": "NOT_APPLICABLE"})
    eval_res = ComplianceEngine.evaluate(req, [], [])
    assert eval_res.status == ComplianceStatus.NOT_APPLICABLE
    assert eval_res.reason_code == "NOT_APPLICABLE_OPTIONAL"


def test_compliance_verification_service_error_unknown():
    req = make_requirement(OperatorEnum.GTE, 100000000)
    facts = [make_fact(150000000)]
    ver = [make_verification(VerificationStatus.SERVICE_ERROR)]
    eval_res = ComplianceEngine.evaluate(req, facts, ver)
    assert eval_res.status == ComplianceStatus.UNKNOWN
    assert eval_res.reason_code == "VERIFICATION_UNAVAILABLE"


def test_compliance_verification_timeout_unknown():
    req = make_requirement(OperatorEnum.GTE, 100000000)
    facts = [make_fact(150000000)]
    ver = [make_verification(VerificationStatus.TIMEOUT)]
    eval_res = ComplianceEngine.evaluate(req, facts, ver)
    assert eval_res.status == ComplianceStatus.UNKNOWN
    assert eval_res.reason_code == "VERIFICATION_UNAVAILABLE"


def test_compliance_verification_mismatch_review_required():
    req = make_requirement(OperatorEnum.GTE, 100000000)
    facts = [make_fact(150000000)]
    ver = [make_verification(VerificationStatus.MISMATCH, verified_value=80000000)]
    eval_res = ComplianceEngine.evaluate(req, facts, ver)
    assert eval_res.status == ComplianceStatus.REVIEW_REQUIRED
    assert eval_res.reason_code == "VERIFICATION_MISMATCH"


def test_compliance_conflicting_facts_review_required():
    req = make_requirement(OperatorEnum.GTE, 100000000)
    facts = [
        make_fact(150000000, fact_id="FACT-001"),
        make_fact(80000000, fact_id="FACT-002"),
    ]
    eval_res = ComplianceEngine.evaluate(req, facts, [])
    assert eval_res.status == ComplianceStatus.REVIEW_REQUIRED
    assert eval_res.reason_code == "CONFLICTING_FACTS"


def test_compliance_conflicting_verified_values_review_required():
    req = make_requirement(OperatorEnum.GTE, 100000000)
    facts = [make_fact(150000000)]
    ver = [
        make_verification(VerificationStatus.VERIFIED, verified_value=150000000, ver_id="V1"),
        make_verification(VerificationStatus.VERIFIED, verified_value=80000000, ver_id="V2"),
    ]
    eval_res = ComplianceEngine.evaluate(req, facts, ver)
    assert eval_res.status == ComplianceStatus.REVIEW_REQUIRED
    assert eval_res.reason_code == "CONFLICTING_VERIFICATION_RESULTS"


def test_compliance_claim_vs_verification_mismatch():
    req = make_requirement(OperatorEnum.GTE, 100000000)
    facts = [make_fact(150000000)]
    ver = [make_verification(VerificationStatus.VERIFIED, verified_value=120000000)]
    eval_res = ComplianceEngine.evaluate(req, facts, ver)
    assert eval_res.status == ComplianceStatus.REVIEW_REQUIRED
    assert eval_res.reason_code == "CLAIM_VERIFICATION_MISMATCH"


def test_compliance_date_before():
    req = make_requirement(OperatorEnum.DATE_BEFORE, "2025-01-01", field="general.incorporation_date")
    facts = [make_fact("2024-05-15", field="general.incorporation_date")]
    eval_res = ComplianceEngine.evaluate(req, facts, [])
    assert eval_res.status == ComplianceStatus.PASS

    facts_after = [make_fact("2025-06-01", field="general.incorporation_date")]
    eval_res_fail = ComplianceEngine.evaluate(req, facts_after, [])
    assert eval_res_fail.status == ComplianceStatus.FAIL


def test_compliance_exists_operator():
    req = make_requirement(OperatorEnum.EXISTS, True, field="certificates.iso9001", mandatory=True)
    facts = [make_fact(True, field="certificates.iso9001")]
    eval_res = ComplianceEngine.evaluate(req, facts, [])
    assert eval_res.status == ComplianceStatus.PASS
    assert eval_res.reason_code == "EVIDENCE_EXISTS"

    eval_res_missing = ComplianceEngine.evaluate(req, [], [])
    assert eval_res_missing.status == ComplianceStatus.UNKNOWN
    assert eval_res_missing.reason_code == "MISSING_EVIDENCE"


def test_evidence_absence_safety_exists_and_not_exists():
    # 1. EXISTS mandatory + no facts/verifications => UNKNOWN / MISSING_EVIDENCE
    req_exists_mand = make_requirement(OperatorEnum.EXISTS, True, field="cert.iso", mandatory=True)
    res1 = ComplianceEngine.evaluate(req_exists_mand, [], [])
    assert res1.status == ComplianceStatus.UNKNOWN
    assert res1.reason_code == "MISSING_EVIDENCE"

    # 2. EXISTS optional + no evidence => NOT_APPLICABLE / NOT_APPLICABLE_OPTIONAL
    req_exists_opt = make_requirement(OperatorEnum.EXISTS, True, field="cert.iso", mandatory=False, meta={"optional_missing_policy": "NOT_APPLICABLE"})
    res2 = ComplianceEngine.evaluate(req_exists_opt, [], [])
    assert res2.status == ComplianceStatus.NOT_APPLICABLE
    assert res2.reason_code == "NOT_APPLICABLE_OPTIONAL"

    # 3. EXISTS + explicit usable value => PASS / EVIDENCE_EXISTS
    res3 = ComplianceEngine.evaluate(req_exists_mand, [make_fact(True, field="cert.iso")], [])
    assert res3.status == ComplianceStatus.PASS
    assert res3.reason_code == "EVIDENCE_EXISTS"

    # 4. NOT_EXISTS mandatory + no evidence => UNKNOWN / MISSING_EVIDENCE
    req_ne_mand = make_requirement(OperatorEnum.NOT_EXISTS, False, field="debarment.status", mandatory=True)
    res4 = ComplianceEngine.evaluate(req_ne_mand, [], [])
    assert res4.status == ComplianceStatus.UNKNOWN
    assert res4.reason_code == "MISSING_EVIDENCE"

    # 5. NOT_EXISTS optional + no evidence => NOT_APPLICABLE / NOT_APPLICABLE_OPTIONAL
    req_ne_opt = make_requirement(OperatorEnum.NOT_EXISTS, False, field="debarment.status", mandatory=False, meta={"optional_missing_policy": "NOT_APPLICABLE"})
    res5 = ComplianceEngine.evaluate(req_ne_opt, [], [])
    assert res5.status == ComplianceStatus.NOT_APPLICABLE
    assert res5.reason_code == "NOT_APPLICABLE_OPTIONAL"

    # 6. NOT_EXISTS + explicit False => PASS / EVIDENCE_ABSENT
    res6 = ComplianceEngine.evaluate(req_ne_mand, [make_fact(False, field="debarment.status")], [])
    assert res6.status == ComplianceStatus.PASS
    assert res6.reason_code == "EVIDENCE_ABSENT"

    # 7. NOT_EXISTS + explicit True => FAIL / EVIDENCE_PRESENT
    res7 = ComplianceEngine.evaluate(req_ne_mand, [make_fact(True, field="debarment.status")], [])
    assert res7.status == ComplianceStatus.FAIL
    assert res7.reason_code == "EVIDENCE_PRESENT"

    # 8. NOT_EXISTS + verification unavailable => UNKNOWN / VERIFICATION_UNAVAILABLE
    ver_unavail = [make_verification(VerificationStatus.UNAVAILABLE, verified_value=None, field="debarment.status", ver_id="V1")]
    res8 = ComplianceEngine.evaluate(req_ne_mand, [], ver_unavail)
    assert res8.status == ComplianceStatus.UNKNOWN
    assert res8.reason_code == "VERIFICATION_UNAVAILABLE"

    # 9. NOT_EXISTS + malformed/ambiguous value => REVIEW_REQUIRED
    ver_ambig = [make_verification(VerificationStatus.VERIFIED, verified_value={"v1": 1, "v2": 2}, field="debarment.status", ver_id="V2")]
    res9a = ComplianceEngine.evaluate(req_ne_mand, [], ver_ambig)
    assert res9a.status == ComplianceStatus.REVIEW_REQUIRED
    assert res9a.reason_code == "AMBIGUOUS_VERIFIED_VALUE"

    res9b = ComplianceEngine.evaluate(req_ne_mand, [make_fact("arbitrary_string", field="debarment.status")], [])
    assert res9b.status == ComplianceStatus.REVIEW_REQUIRED
    assert res9b.reason_code == "TYPE_CONVERSION_ERROR"


def test_gst_requirement_vendor_name_case_sensitivity():
    # GST requirement with field "vendor.name": "Acme" vs "ACME" -> NOT automatically equal
    req = make_requirement(OperatorEnum.EQ, "ACME", field="vendor.name", req_type=RequirementType.GST)
    facts = [make_fact("Acme", field="vendor.name")]
    res = ComplianceEngine.evaluate(req, facts, [])
    assert res.status == ComplianceStatus.FAIL
    assert res.reason_code == "NOT_EQUAL"


def test_gst_status_case_insensitivity():
    # gst.status: "active" vs "ACTIVE" -> equal
    req = make_requirement(OperatorEnum.EQ, "ACTIVE", field="gst.status", req_type=RequirementType.GST)
    facts = [make_fact("active", field="gst.status")]
    res = ComplianceEngine.evaluate(req, facts, [])
    assert res.status == ComplianceStatus.PASS
    assert res.reason_code == "EQUAL"


def test_date_eq_valid_equivalent_and_malformed():
    # Valid equivalent representations (date-only) -> PASS
    req_eq = make_requirement(OperatorEnum.EQ, "2026-01-15", field="general.incorporation_date")
    facts_valid = [make_fact("2026-01-15", field="general.incorporation_date")]
    res_valid = ComplianceEngine.evaluate(req_eq, facts_valid, [])
    assert res_valid.status == ComplianceStatus.PASS

    # Malformed observed for EQ -> REVIEW_REQUIRED / MALFORMED_DATE
    facts_malformed = [make_fact("invalid-date", field="general.incorporation_date")]
    res_malformed_eq = ComplianceEngine.evaluate(req_eq, facts_malformed, [])
    assert res_malformed_eq.status == ComplianceStatus.REVIEW_REQUIRED
    assert res_malformed_eq.reason_code == "MALFORMED_DATE"

    # Malformed observed for NE -> REVIEW_REQUIRED / MALFORMED_DATE
    req_ne = make_requirement(OperatorEnum.NE, "2026-01-15", field="general.incorporation_date")
    res_malformed_ne = ComplianceEngine.evaluate(req_ne, facts_malformed, [])
    assert res_malformed_ne.status == ComplianceStatus.REVIEW_REQUIRED
    assert res_malformed_ne.reason_code == "MALFORMED_DATE"


def test_in_and_not_in_field_semantics():
    # IN on status field remains case-insensitive
    req_in_status = make_requirement(OperatorEnum.IN, ["ACTIVE", "SUSPENDED"], field="gst.status", req_type=RequirementType.GST)
    facts_status = [make_fact("active", field="gst.status")]
    res_in_s = ComplianceEngine.evaluate(req_in_status, facts_status, [])
    assert res_in_s.status == ComplianceStatus.PASS

    # IN on generic text field preserves case
    req_in_text = make_requirement(OperatorEnum.IN, ["ACME"], field="vendor.name", req_type=RequirementType.GST)
    facts_text = [make_fact("Acme", field="vendor.name")]
    res_in_t = ComplianceEngine.evaluate(req_in_text, facts_text, [])
    assert res_in_t.status == ComplianceStatus.FAIL

    # NOT_IN on generic text field preserves case
    req_notin_text = make_requirement(OperatorEnum.NOT_IN, ["ACME"], field="vendor.name", req_type=RequirementType.GST)
    res_notin_t = ComplianceEngine.evaluate(req_notin_text, facts_text, [])
    assert res_notin_t.status == ComplianceStatus.PASS


def test_type_aware_eq_and_ne_type_conversion_errors():
    # EQ expected bool + malformed string -> REVIEW_REQUIRED, TYPE_CONVERSION_ERROR
    req_eq_bool = make_requirement(OperatorEnum.EQ, False, field="cert.valid")
    res1 = ComplianceEngine.evaluate(req_eq_bool, [make_fact("maybe", field="cert.valid")], [])
    assert res1.status == ComplianceStatus.REVIEW_REQUIRED
    assert res1.reason_code == "TYPE_CONVERSION_ERROR"

    # NE expected bool + malformed string -> REVIEW_REQUIRED, TYPE_CONVERSION_ERROR
    req_ne_bool = make_requirement(OperatorEnum.NE, False, field="cert.valid")
    res2 = ComplianceEngine.evaluate(req_ne_bool, [make_fact("maybe", field="cert.valid")], [])
    assert res2.status == ComplianceStatus.REVIEW_REQUIRED
    assert res2.reason_code == "TYPE_CONVERSION_ERROR"

    # EQ expected numeric + malformed string -> REVIEW_REQUIRED, MALFORMED_NUMBER / TYPE_CONVERSION_ERROR
    req_eq_num = make_requirement(OperatorEnum.EQ, 100)
    res3 = ComplianceEngine.evaluate(req_eq_num, [make_fact("not-a-number")], [])
    assert res3.status == ComplianceStatus.REVIEW_REQUIRED
    assert res3.reason_code in ("TYPE_CONVERSION_ERROR", "MALFORMED_NUMBER")


def test_strict_structured_value_ambiguity():
    req = make_requirement(OperatorEnum.GTE, 100000000)

    ver_ambig = [make_verification(VerificationStatus.VERIFIED, verified_value={"value": 100000000, "verified_value": 90000000})]
    res_ambig = ComplianceEngine.evaluate(req, [make_fact(100000000)], ver_ambig)
    assert res_ambig.status == ComplianceStatus.REVIEW_REQUIRED
    assert res_ambig.reason_code == "AMBIGUOUS_VERIFIED_VALUE"

    ver_safe = [make_verification(VerificationStatus.VERIFIED, verified_value={"verified_value": 150000000, "unit": "INR"})]
    res_safe = ComplianceEngine.evaluate(req, [make_fact(150000000)], ver_safe)
    assert res_safe.status == ComplianceStatus.PASS
    assert res_safe.reason_code == "GREATER_THAN_OR_EQUAL"


def test_count_gte_integer_safety():
    req = make_requirement(OperatorEnum.COUNT_GTE, 3, field="projects")
    res_frac_obs = ComplianceEngine.evaluate(req, [make_fact(2.5, field="projects")], [])
    assert res_frac_obs.status == ComplianceStatus.REVIEW_REQUIRED
    assert res_frac_obs.reason_code == "TYPE_CONVERSION_ERROR"

    res_neg_obs = ComplianceEngine.evaluate(req, [make_fact(-1, field="projects")], [])
    assert res_neg_obs.status == ComplianceStatus.REVIEW_REQUIRED
    assert res_neg_obs.reason_code == "TYPE_CONVERSION_ERROR"

    req_neg_exp = make_requirement(OperatorEnum.COUNT_GTE, -3, field="projects")
    res_neg_exp = ComplianceEngine.evaluate(req_neg_exp, [make_fact(3, field="projects")], [])
    assert res_neg_exp.status == ComplianceStatus.REVIEW_REQUIRED
    assert res_neg_exp.reason_code == "TYPE_CONVERSION_ERROR"

    req_frac_exp = make_requirement(OperatorEnum.COUNT_GTE, 1.7, field="projects")
    res_frac_exp = ComplianceEngine.evaluate(req_frac_exp, [make_fact(3, field="projects")], [])
    assert res_frac_exp.status == ComplianceStatus.REVIEW_REQUIRED
    assert res_frac_exp.reason_code == "TYPE_CONVERSION_ERROR"


def test_actual_bid_verification_service_rollup_precedence():
    class MockEval:
        def __init__(self, status):
            self.status = status

    assert BidVerificationService.compute_overall_status([]) == ComplianceStatus.UNKNOWN

    assert BidVerificationService.compute_overall_status([MockEval(ComplianceStatus.PASS), MockEval(ComplianceStatus.UNKNOWN)]) == ComplianceStatus.UNKNOWN

    assert BidVerificationService.compute_overall_status([
        MockEval(ComplianceStatus.PASS),
        MockEval(ComplianceStatus.UNKNOWN),
        MockEval(ComplianceStatus.REVIEW_REQUIRED)
    ]) == ComplianceStatus.REVIEW_REQUIRED

    assert BidVerificationService.compute_overall_status([
        MockEval(ComplianceStatus.PASS),
        MockEval(ComplianceStatus.UNKNOWN),
        MockEval(ComplianceStatus.REVIEW_REQUIRED),
        MockEval(ComplianceStatus.FAIL)
    ]) == ComplianceStatus.FAIL


def test_numeric_normalization_and_indian_currency():
    req = make_requirement(OperatorEnum.EQ, 100000000)

    facts = [
        make_fact("₹10,00,00,000", fact_id="F1"),
        make_fact("10,00,00,000", fact_id="F2"),
        make_fact(100000000, fact_id="F3"),
    ]
    res = ComplianceEngine.evaluate(req, facts, [])
    assert res.status == ComplianceStatus.PASS
    assert res.reason_code == "EQUAL"


def test_timezone_aware_date_comparison():
    req = make_requirement(OperatorEnum.DATE_BEFORE, "2026-01-15T12:00:00Z", field="general.incorporation_date")
    facts = [make_fact("2026-01-15T16:00:00+05:30", field="general.incorporation_date")]
    res = ComplianceEngine.evaluate(req, facts, [])
    assert res.status == ComplianceStatus.PASS


def test_unsupported_operator_behavior():
    req = make_requirement(OperatorEnum.EQ, 100)
    req.operator = "INVALID_OPERATOR"
    res = ComplianceEngine.evaluate(req, [make_fact(100)], [])
    assert res.status == ComplianceStatus.UNKNOWN
    assert res.reason_code == "UNSUPPORTED_OPERATOR"
