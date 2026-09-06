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
