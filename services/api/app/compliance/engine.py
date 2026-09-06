from datetime import datetime, timezone
import uuid
from typing import Any
from app.schemas.canonical import (
    ComplianceStatus,
    FactRead,
    OperatorEnum,
    RuleEvaluationRead,
    TenderRequirementRead,
    VerificationResultRead,
    VerificationStatus,
)


class ComplianceEngine:
    """Pure deterministic compliance evaluation engine.
    
    Evaluates tender requirement rules against extracted facts and verification results.
    Prohibited from invoking LLMs, databases, network calls, or side effects.
    """

    @classmethod
    def evaluate(
        cls,
        rule: TenderRequirementRead,
        facts: list[FactRead],
        verification_results: list[VerificationResultRead],
        context: dict[str, Any] | None = None,
    ) -> RuleEvaluationRead:
        context = context or {}
        now = datetime.now(timezone.utc)
        eval_id = str(uuid.uuid4())

        # 1. Filter facts relevant to this requirement's field
        matching_facts = [f for f in facts if f.field == rule.field]
        evidence_ids = [f.id for f in matching_facts]

        # 2. Filter verification results for this requirement's field
        matching_verifications = [v for v in verification_results if v.field == rule.field]
        for v in matching_verifications:
            evidence_ids.append(v.id)

        # 3. Check for external verification failures/unavailability
        unhealthy_verifications = [
            v for v in matching_verifications
            if v.status in (VerificationStatus.SERVICE_ERROR, VerificationStatus.UNAVAILABLE, VerificationStatus.TIMEOUT)
        ]
        if unhealthy_verifications:
            first_err = unhealthy_verifications[0]
            return RuleEvaluationRead(
                id=eval_id,
                bidder_id=context.get("bidder_id", "UNKNOWN_BIDDER"),
                requirement_id=rule.id,
                status=ComplianceStatus.UNKNOWN,
                reason_code="VERIFICATION_UNAVAILABLE",
                observed_value=None,
                expected_value=rule.expected_value,
                evidence_ids=evidence_ids,
                rule_version="1.0",
                evaluated_at=now,
            )

        # 4. Check for verification mismatches
        mismatch_verifications = [
            v for v in matching_verifications if v.status == VerificationStatus.MISMATCH
        ]
        if mismatch_verifications:
            first_mismatch = mismatch_verifications[0]
            return RuleEvaluationRead(
                id=eval_id,
                bidder_id=context.get("bidder_id", "UNKNOWN_BIDDER"),
                requirement_id=rule.id,
                status=ComplianceStatus.REVIEW_REQUIRED,
                reason_code="VERIFICATION_MISMATCH",
                observed_value={
                    "claimed": first_mismatch.claimed_value,
                    "verified": first_mismatch.verified_value,
                },
                expected_value=rule.expected_value,
                evidence_ids=evidence_ids,
                rule_version="1.0",
                evaluated_at=now,
            )

        # 5. Handle EXISTS / NOT_EXISTS operators (don't strictly require value presence)
        if rule.operator == OperatorEnum.EXISTS:
            status = ComplianceStatus.PASS if matching_facts else (
                ComplianceStatus.FAIL if rule.mandatory else ComplianceStatus.NOT_APPLICABLE
            )
            reason = "EVIDENCE_EXISTS" if status == ComplianceStatus.PASS else "EVIDENCE_MISSING"
            return RuleEvaluationRead(
                id=eval_id,
                bidder_id=context.get("bidder_id", "UNKNOWN_BIDDER"),
                requirement_id=rule.id,
                status=status,
                reason_code=reason,
                observed_value=len(matching_facts) > 0,
                expected_value=True,
                evidence_ids=evidence_ids,
                rule_version="1.0",
                evaluated_at=now,
            )

        if rule.operator == OperatorEnum.NOT_EXISTS:
            status = ComplianceStatus.PASS if not matching_facts else ComplianceStatus.FAIL
            reason = "EVIDENCE_ABSENT" if status == ComplianceStatus.PASS else "EVIDENCE_PRESENT"
            return RuleEvaluationRead(
                id=eval_id,
                bidder_id=context.get("bidder_id", "UNKNOWN_BIDDER"),
                requirement_id=rule.id,
                status=status,
                reason_code=reason,
                observed_value=len(matching_facts) > 0,
                expected_value=False,
                evidence_ids=evidence_ids,
                rule_version="1.0",
                evaluated_at=now,
            )

        # 6. If no matching facts are present for non-existential operators
        if not matching_facts:
            # Check if verified value is present in matching_verifications
            verified_with_val = [v for v in matching_verifications if v.verified_value is not None]
            if not verified_with_val:
                status = ComplianceStatus.UNKNOWN if rule.mandatory else ComplianceStatus.NOT_APPLICABLE
                return RuleEvaluationRead(
                    id=eval_id,
                    bidder_id=context.get("bidder_id", "UNKNOWN_BIDDER"),
                    requirement_id=rule.id,
                    status=status,
                    reason_code="MISSING_EVIDENCE",
                    observed_value=None,
                    expected_value=rule.expected_value,
                    evidence_ids=evidence_ids,
                    rule_version="1.0",
                    evaluated_at=now,
                )

        # 7. Check for conflicting facts across documents
        unique_claimed_values = list({str(f.value) for f in matching_facts})
        if len(unique_claimed_values) > 1:
            return RuleEvaluationRead(
                id=eval_id,
                bidder_id=context.get("bidder_id", "UNKNOWN_BIDDER"),
                requirement_id=rule.id,
                status=ComplianceStatus.REVIEW_REQUIRED,
                reason_code="CONFLICTING_FACTS",
                observed_value=[f.value for f in matching_facts],
                expected_value=rule.expected_value,
                evidence_ids=evidence_ids,
                rule_version="1.0",
                evaluated_at=now,
            )

        # Determine effective value to evaluate (prefer verified value if present, else fact value)
        observed_val = None
        if matching_verifications and matching_verifications[0].verified_value is not None:
            observed_val = matching_verifications[0].verified_value
        elif matching_facts:
            observed_val = matching_facts[0].value

        # 8. Evaluate operator against observed value and expected value
        status, reason_code = cls._evaluate_operator(
            rule.operator, observed_val, rule.expected_value
        )

        return RuleEvaluationRead(
            id=eval_id,
            bidder_id=context.get("bidder_id", "UNKNOWN_BIDDER"),
            requirement_id=rule.id,
            status=status,
            reason_code=reason_code,
            observed_value=observed_val,
            expected_value=rule.expected_value,
            evidence_ids=evidence_ids,
            rule_version="1.0",
            evaluated_at=now,
        )

    @classmethod
    def _evaluate_operator(
        cls, operator: OperatorEnum, observed: Any, expected: Any
    ) -> tuple[ComplianceStatus, str]:
        if observed is None:
            return ComplianceStatus.UNKNOWN, "OBSERVED_VALUE_NULL"

        try:
            if operator == OperatorEnum.EQ:
                match = (observed == expected)
                return (ComplianceStatus.PASS, "EQUAL") if match else (ComplianceStatus.FAIL, "NOT_EQUAL")

            elif operator == OperatorEnum.NE:
                match = (observed != expected)
                return (ComplianceStatus.PASS, "NOT_EQUAL") if match else (ComplianceStatus.FAIL, "EQUAL")

            elif operator in (OperatorEnum.GT, OperatorEnum.GTE, OperatorEnum.LT, OperatorEnum.LTE):
                obs_num = float(observed)
                exp_num = float(expected)

                if operator == OperatorEnum.GT:
                    return (ComplianceStatus.PASS, "GREATER_THAN") if obs_num > exp_num else (ComplianceStatus.FAIL, "NOT_GREATER_THAN")
                elif operator == OperatorEnum.GTE:
                    return (ComplianceStatus.PASS, "GREATER_THAN_OR_EQUAL") if obs_num >= exp_num else (ComplianceStatus.FAIL, "LESS_THAN")
                elif operator == OperatorEnum.LT:
                    return (ComplianceStatus.PASS, "LESS_THAN") if obs_num < exp_num else (ComplianceStatus.FAIL, "NOT_LESS_THAN")
                elif operator == OperatorEnum.LTE:
                    return (ComplianceStatus.PASS, "LESS_THAN_OR_EQUAL") if obs_num <= exp_num else (ComplianceStatus.FAIL, "GREATER_THAN")

            elif operator == OperatorEnum.COUNT_GTE:
                obs_count = int(observed) if not isinstance(observed, list) else len(observed)
                exp_count = int(expected)
                return (ComplianceStatus.PASS, "COUNT_SUFFICIENT") if obs_count >= exp_count else (ComplianceStatus.FAIL, "COUNT_INSUFFICIENT")

            elif operator == OperatorEnum.DATE_BEFORE:
                obs_dt = cls._parse_date(observed)
                exp_dt = cls._parse_date(expected)
                if obs_dt and exp_dt:
                    return (ComplianceStatus.PASS, "BEFORE_DATE") if obs_dt < exp_dt else (ComplianceStatus.FAIL, "ON_OR_AFTER_DATE")
                return ComplianceStatus.REVIEW_REQUIRED, "MALFORMED_DATE"

            elif operator == OperatorEnum.DATE_AFTER:
                obs_dt = cls._parse_date(observed)
                exp_dt = cls._parse_date(expected)
                if obs_dt and exp_dt:
                    return (ComplianceStatus.PASS, "AFTER_DATE") if obs_dt > exp_dt else (ComplianceStatus.FAIL, "ON_OR_BEFORE_DATE")
                return ComplianceStatus.REVIEW_REQUIRED, "MALFORMED_DATE"

            elif operator == OperatorEnum.IN:
                exp_list = expected if isinstance(expected, list) else [expected]
                return (ComplianceStatus.PASS, "VALUE_IN_SET") if observed in exp_list else (ComplianceStatus.FAIL, "VALUE_NOT_IN_SET")

            elif operator == OperatorEnum.NOT_IN:
                exp_list = expected if isinstance(expected, list) else [expected]
                return (ComplianceStatus.PASS, "VALUE_NOT_IN_SET") if observed not in exp_list else (ComplianceStatus.FAIL, "VALUE_IN_SET")

        except (ValueError, TypeError) as exc:
            return ComplianceStatus.REVIEW_REQUIRED, f"TYPE_CONVERSION_ERROR: {exc}"

        return ComplianceStatus.UNKNOWN, "UNSUPPORTED_OPERATOR"

    @staticmethod
    def _parse_date(val: Any) -> datetime | None:
        if isinstance(val, datetime):
            return val
        if isinstance(val, str):
            for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%d/%m/%Y"):
                try:
                    return datetime.strptime(val, fmt).replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
        return None
