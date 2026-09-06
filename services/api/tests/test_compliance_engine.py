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


def make_requirement(
    operator: OperatorEnum,
    expected_value: any,
    field: str = "financial.average_annual_turnover",
    mandatory: bool = True,
) -> TenderRequirementRead:
    return TenderRequirementRead(
        id="REQ-001",
        tender_id="TENDER-001",
        clause="4.2",
        requirement_type=RequirementType.TURNOVER,
        field=field,
        operator=operator,
        expected_value=expected_value,
        unit="INR",
        mandatory=mandatory,
        confidence=1.0,
        requires_verification=True,
        created_at=datetime.now(timezone.utc),
    )


def make_fact(value: any, field: str = "financial.average_annual_turnover", fact_id: str = "FACT-001") -> FactRead:
    return FactRead(
        id=fact_id,
        document_id="DOC-001",
        bidder_id="BIDDER-001",
        field=field,
        value=value,
        confidence=1.0,
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
    req = make_requirement(OperatorEnum.GTE, 100000000, mandatory=False)
    eval_res = ComplianceEngine.evaluate(req, [], [])
    assert eval_res.status == ComplianceStatus.NOT_APPLICABLE
    assert eval_res.reason_code == "MISSING_EVIDENCE"


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
    facts = [make_fact(150000000)]  # Claimed 15 Cr
    ver = [make_verification(VerificationStatus.VERIFIED, verified_value=120000000)]  # Verified 12 Cr
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


def test_compliance_in_operator():
    req = make_requirement(OperatorEnum.IN, ["MAHARASHTRA", "DELHI"], field="general.state")
    facts = [make_fact("MAHARASHTRA", field="general.state")]
    eval_res = ComplianceEngine.evaluate(req, facts, [])
    assert eval_res.status == ComplianceStatus.PASS

    facts_not = [make_fact("KARNATAKA", field="general.state")]
    eval_res_fail = ComplianceEngine.evaluate(req, facts_not, [])
    assert eval_res_fail.status == ComplianceStatus.FAIL


def test_compliance_exists_operator():
    req = make_requirement(OperatorEnum.EXISTS, True, field="certificates.iso9001")
    facts = [make_fact(True, field="certificates.iso9001")]
    eval_res = ComplianceEngine.evaluate(req, facts, [])
    assert eval_res.status == ComplianceStatus.PASS

    eval_res_missing = ComplianceEngine.evaluate(req, [], [])
    assert eval_res_missing.status == ComplianceStatus.FAIL


def test_all_operators_coverage():
    # EQ
    req_eq = make_requirement(OperatorEnum.EQ, "ACTIVE", field="gst.status")
    assert ComplianceEngine.evaluate(req_eq, [make_fact("ACTIVE", field="gst.status")], []).status == ComplianceStatus.PASS
    assert ComplianceEngine.evaluate(req_eq, [make_fact("INACTIVE", field="gst.status")], []).status == ComplianceStatus.FAIL

    # NE
    req_ne = make_requirement(OperatorEnum.NE, "CANCELLED", field="gst.status")
    assert ComplianceEngine.evaluate(req_ne, [make_fact("ACTIVE", field="gst.status")], []).status == ComplianceStatus.PASS
    assert ComplianceEngine.evaluate(req_ne, [make_fact("CANCELLED", field="gst.status")], []).status == ComplianceStatus.FAIL

    # GT
    req_gt = make_requirement(OperatorEnum.GT, 100)
    assert ComplianceEngine.evaluate(req_gt, [make_fact(150)], []).status == ComplianceStatus.PASS
    assert ComplianceEngine.evaluate(req_gt, [make_fact(100)], []).status == ComplianceStatus.FAIL

    # GTE
    req_gte = make_requirement(OperatorEnum.GTE, 100)
    assert ComplianceEngine.evaluate(req_gte, [make_fact(100)], []).status == ComplianceStatus.PASS
    assert ComplianceEngine.evaluate(req_gte, [make_fact(99)], []).status == ComplianceStatus.FAIL

    # LT
    req_lt = make_requirement(OperatorEnum.LT, 100)
    assert ComplianceEngine.evaluate(req_lt, [make_fact(50)], []).status == ComplianceStatus.PASS
    assert ComplianceEngine.evaluate(req_lt, [make_fact(100)], []).status == ComplianceStatus.FAIL

    # LTE
    req_lte = make_requirement(OperatorEnum.LTE, 100)
    assert ComplianceEngine.evaluate(req_lte, [make_fact(100)], []).status == ComplianceStatus.PASS
    assert ComplianceEngine.evaluate(req_lte, [make_fact(101)], []).status == ComplianceStatus.FAIL

    # NOT_EXISTS
    req_nx = make_requirement(OperatorEnum.NOT_EXISTS, False, field="debarment.flag")
    assert ComplianceEngine.evaluate(req_nx, [], []).status == ComplianceStatus.PASS
    assert ComplianceEngine.evaluate(req_nx, [make_fact(True, field="debarment.flag")], []).status == ComplianceStatus.FAIL

    # COUNT_GTE
    req_count = make_requirement(OperatorEnum.COUNT_GTE, 3, field="experience.projects")
    assert ComplianceEngine.evaluate(req_count, [make_fact(["P1", "P2", "P3"], field="experience.projects")], []).status == ComplianceStatus.PASS
    assert ComplianceEngine.evaluate(req_count, [make_fact(["P1"], field="experience.projects")], []).status == ComplianceStatus.FAIL
    assert ComplianceEngine.evaluate(req_count, [make_fact("5", field="experience.projects")], []).status == ComplianceStatus.PASS

    # DATE_AFTER
    req_da = make_requirement(OperatorEnum.DATE_AFTER, "2020-01-01", field="cert.date")
    assert ComplianceEngine.evaluate(req_da, [make_fact("2021-06-15", field="cert.date")], []).status == ComplianceStatus.PASS
    assert ComplianceEngine.evaluate(req_da, [make_fact("2019-12-31", field="cert.date")], []).status == ComplianceStatus.FAIL

    # NOT_IN
    req_notin = make_requirement(OperatorEnum.NOT_IN, ["SUSPENDED", "CANCELLED"], field="status")
    assert ComplianceEngine.evaluate(req_notin, [make_fact("ACTIVE", field="status")], []).status == ComplianceStatus.PASS
    assert ComplianceEngine.evaluate(req_notin, [make_fact("CANCELLED", field="status")], []).status == ComplianceStatus.FAIL


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


def test_boolean_and_status_normalization():
    # True vs "true" vs "yes" vs 1
    req = make_requirement(OperatorEnum.EQ, True, field="cert.valid")
    assert ComplianceEngine.evaluate(req, [make_fact("true", field="cert.valid")], []).status == ComplianceStatus.PASS
    assert ComplianceEngine.evaluate(req, [make_fact("yes", field="cert.valid")], []).status == ComplianceStatus.PASS
    assert ComplianceEngine.evaluate(req, [make_fact(1, field="cert.valid")], []).status == ComplianceStatus.PASS

    # False vs "false" vs "no" vs 0
    req_f = make_requirement(OperatorEnum.EQ, False, field="cert.blacklisted")
    assert ComplianceEngine.evaluate(req_f, [make_fact("false", field="cert.blacklisted")], []).status == ComplianceStatus.PASS
    assert ComplianceEngine.evaluate(req_f, [make_fact("no", field="cert.blacklisted")], []).status == ComplianceStatus.PASS
    assert ComplianceEngine.evaluate(req_f, [make_fact(0, field="cert.blacklisted")], []).status == ComplianceStatus.PASS

    # Status case-insensitivity: "ACTIVE" vs "active" vs " Active "
    req_s = make_requirement(OperatorEnum.EQ, "ACTIVE", field="gst.status")
    ver = [make_verification(VerificationStatus.VERIFIED, verified_value=" active ", field="gst.status", ver_id="V1")]
    res = ComplianceEngine.evaluate(req_s, [make_fact("ACTIVE", field="gst.status")], ver)
    assert res.status == ComplianceStatus.PASS


def test_conflicting_verifications_and_facts_semantic():
    req = make_requirement(OperatorEnum.GTE, 100000000)

    ver_ok = [
        make_verification(VerificationStatus.VERIFIED, verified_value=100000000, ver_id="V1"),
        make_verification(VerificationStatus.VERIFIED, verified_value="₹10,00,00,000", ver_id="V2"),
    ]
    res_ok = ComplianceEngine.evaluate(req, [make_fact(100000000)], ver_ok)
    assert res_ok.status == ComplianceStatus.PASS

    ver_conflict = [
        make_verification(VerificationStatus.VERIFIED, verified_value=100000000, ver_id="V1"),
        make_verification(VerificationStatus.VERIFIED, verified_value=90000000, ver_id="V2"),
    ]
    res_conflict = ComplianceEngine.evaluate(req, [make_fact(100000000)], ver_conflict)
    assert res_conflict.status == ComplianceStatus.REVIEW_REQUIRED
    assert res_conflict.reason_code == "CONFLICTING_VERIFICATION_RESULTS"


def test_claim_vs_verification_and_structured_dict():
    req = make_requirement(OperatorEnum.GTE, 100000000)

    ver_struct = [make_verification(VerificationStatus.VERIFIED, verified_value={"verified_value": 100000000})]
    res_struct = ComplianceEngine.evaluate(req, [make_fact(100000000)], ver_struct)
    assert res_struct.status == ComplianceStatus.PASS

    ver_ambig = [make_verification(VerificationStatus.VERIFIED, verified_value={"field1": 100, "field2": 200})]
    res_ambig = ComplianceEngine.evaluate(req, [make_fact(100)], ver_ambig)
    assert res_ambig.status == ComplianceStatus.REVIEW_REQUIRED
    assert res_ambig.reason_code == "AMBIGUOUS_VERIFIED_VALUE"


def test_timezone_aware_date_comparison():
    req = make_requirement(OperatorEnum.DATE_BEFORE, "2026-01-15T12:00:00Z", field="cert.date")
    facts = [make_fact("2026-01-15T16:00:00+05:30", field="cert.date")]
    res = ComplianceEngine.evaluate(req, facts, [])
    assert res.status == ComplianceStatus.PASS


def test_malformed_inputs_type_conversion_and_malformed_date():
    req_num = make_requirement(OperatorEnum.GT, 100)
    res_num = ComplianceEngine.evaluate(req_num, [make_fact("not-a-number")], [])
    assert res_num.status == ComplianceStatus.REVIEW_REQUIRED
    assert res_num.reason_code == "TYPE_CONVERSION_ERROR"

    req_date = make_requirement(OperatorEnum.DATE_BEFORE, "2025-01-01", field="cert.date")
    res_date = ComplianceEngine.evaluate(req_date, [make_fact("invalid-date-format", field="cert.date")], [])
    assert res_date.status == ComplianceStatus.REVIEW_REQUIRED
    assert res_date.reason_code == "MALFORMED_DATE"

    req_cnt = make_requirement(OperatorEnum.COUNT_GTE, 5, field="projects")
    res_cnt = ComplianceEngine.evaluate(req_cnt, [make_fact("invalid-count", field="projects")], [])
    assert res_cnt.status == ComplianceStatus.REVIEW_REQUIRED
    assert res_cnt.reason_code == "TYPE_CONVERSION_ERROR"


def test_unsupported_operator_behavior():
    req = make_requirement(OperatorEnum.EQ, 100)
    req.operator = "INVALID_OPERATOR"
    res = ComplianceEngine.evaluate(req, [make_fact(100)], [])
    assert res.status == ComplianceStatus.UNKNOWN
    assert res.reason_code == "UNSUPPORTED_OPERATOR"


def test_compliance_status_rollup_hierarchy():
    def rollup(statuses):
        if not statuses:
            return ComplianceStatus.UNKNOWN
        if ComplianceStatus.FAIL in statuses:
            return ComplianceStatus.FAIL
        elif ComplianceStatus.REVIEW_REQUIRED in statuses:
            return ComplianceStatus.REVIEW_REQUIRED
        elif ComplianceStatus.UNKNOWN in statuses:
            return ComplianceStatus.UNKNOWN
        return ComplianceStatus.PASS

    assert rollup([]) == ComplianceStatus.UNKNOWN
    assert rollup([ComplianceStatus.PASS, ComplianceStatus.UNKNOWN]) == ComplianceStatus.UNKNOWN
    assert rollup([ComplianceStatus.PASS, ComplianceStatus.UNKNOWN, ComplianceStatus.REVIEW_REQUIRED]) == ComplianceStatus.REVIEW_REQUIRED
    assert rollup([ComplianceStatus.PASS, ComplianceStatus.UNKNOWN, ComplianceStatus.REVIEW_REQUIRED, ComplianceStatus.FAIL]) == ComplianceStatus.FAIL
