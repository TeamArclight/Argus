from datetime import datetime, timezone
import uuid
from typing import Any

from app.compliance.reason_codes import ReasonCode
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

    @staticmethod
    def _normalize_number(val: Any) -> float | int | None:
        """Normalizes numeric representations including currency strings and Indian grouping."""
        if val is None or isinstance(val, bool):
            return None
        if isinstance(val, (int, float)):
            return val
        if isinstance(val, str):
            s = val.strip()
            if not s:
                return None
            # Strip currency symbols and letters e.g. ₹, Rs., INR
            s = s.replace("₹", "").replace("INR", "").replace("inr", "").replace("Rs.", "").replace("rs.", "").strip()
            s = s.replace(",", "")
            try:
                num = float(s)
                if num.is_integer():
                    return int(num)
                return num
            except ValueError:
                return None
        return None

    @staticmethod
    def _normalize_bool(val: Any) -> bool | None:
        """Normalizes boolean values from bools, numbers, and case-insensitive strings."""
        if val is None:
            return None
        if isinstance(val, bool):
            return val
        if isinstance(val, (int, float)):
            if val == 1:
                return True
            if val == 0:
                return False
            return None
        if isinstance(val, str):
            s = val.strip().lower()
            if s in ("true", "yes", "1"):
                return True
            if s in ("false", "no", "0"):
                return False
            return None
        return None

    @staticmethod
    def _normalize_string(val: Any) -> str | None:
        """Trims whitespace from string representation."""
        if val is None:
            return None
        if isinstance(val, str):
            return val.strip()
        return str(val).strip()

    @staticmethod
    def _normalize_date(val: Any) -> datetime | None:
        """Parses timezone-aware datetimes from datetime objects or ISO/standard date strings."""
        if val is None:
            return None
        if isinstance(val, datetime):
            if val.tzinfo is None:
                return val.replace(tzinfo=timezone.utc)
            return val
        if isinstance(val, str):
            s = val.strip()
            if not s:
                return None
            try:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                pass
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
                try:
                    dt = datetime.strptime(s, fmt)
                    return dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
        return None

    @classmethod
    def _values_equivalent(cls, a: Any, b: Any) -> bool:
        """Determines semantic equality across numeric, boolean, date, and status string values."""
        if a is None or b is None:
            return a is b
        if a == b:
            return True

        # 1. Numeric comparison
        num_a = cls._normalize_number(a)
        num_b = cls._normalize_number(b)
        if num_a is not None and num_b is not None:
            return num_a == num_b

        # 2. Boolean comparison
        bool_a = cls._normalize_bool(a)
        bool_b = cls._normalize_bool(b)
        if bool_a is not None and bool_b is not None:
            return bool_a == bool_b

        # 3. Date comparison
        dt_a = cls._normalize_date(a)
        dt_b = cls._normalize_date(b)
        if dt_a is not None and dt_b is not None:
            return dt_a == dt_b

        # 4. Case-insensitive string / status comparison
        str_a = cls._normalize_string(a)
        str_b = cls._normalize_string(b)
        if str_a is not None and str_b is not None:
            return str_a.lower() == str_b.lower()

        return False

    @staticmethod
    def _resolve_scalar(val: Any) -> tuple[Any, bool]:
        """Resolves scalar value from structured dicts deterministically."""
        if isinstance(val, dict):
            if len(val) == 1:
                return list(val.values())[0], True
            for candidate in ("value", "verified_value", "claimed_value"):
                if candidate in val:
                    return val[candidate], True
            return val, False
        return val, True

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

        matching_facts = [f for f in facts if f.field == rule.field]
        matching_verifications = [v for v in verification_results if v.field == rule.field]

        evidence_ids = [f.id for f in matching_facts] + [v.id for v in matching_verifications]

        # PRECEDENCE 1: External verification service error / unavailable / timeout
        unhealthy_verifications = [
            v
            for v in matching_verifications
            if v.status in (VerificationStatus.SERVICE_ERROR, VerificationStatus.UNAVAILABLE, VerificationStatus.TIMEOUT)
        ]
        if unhealthy_verifications:
            return RuleEvaluationRead(
                id=eval_id,
                bidder_id=context.get("bidder_id", "UNKNOWN_BIDDER"),
                requirement_id=rule.id,
                status=ComplianceStatus.UNKNOWN,
                reason_code=ReasonCode.VERIFICATION_UNAVAILABLE,
                observed_value=None,
                expected_value=rule.expected_value,
                evidence_ids=evidence_ids,
                rule_version="1.0",
                evaluated_at=now,
            )

        usable_verifications = [
            v
            for v in matching_verifications
            if v.status not in (VerificationStatus.SERVICE_ERROR, VerificationStatus.UNAVAILABLE, VerificationStatus.TIMEOUT)
        ]
        verified_with_val = [v for v in usable_verifications if v.verified_value is not None]

        # PRECEDENCE 2: Conflicting verification values across verification results
        has_ver_conflict = False
        if len(verified_with_val) > 1:
            first_v = verified_with_val[0].verified_value
            for v in verified_with_val[1:]:
                if not cls._values_equivalent(first_v, v.verified_value):
                    has_ver_conflict = True
                    break

        if has_ver_conflict:
            return RuleEvaluationRead(
                id=eval_id,
                bidder_id=context.get("bidder_id", "UNKNOWN_BIDDER"),
                requirement_id=rule.id,
                status=ComplianceStatus.REVIEW_REQUIRED,
                reason_code=ReasonCode.CONFLICTING_VERIFICATION_RESULTS,
                observed_value=[v.verified_value for v in verified_with_val],
                expected_value=rule.expected_value,
                evidence_ids=evidence_ids,
                rule_version="1.0",
                evaluated_at=now,
            )

        # PRECEDENCE 3: Explicit verification status MISMATCH
        mismatch_verifications = [v for v in usable_verifications if v.status == VerificationStatus.MISMATCH]
        if mismatch_verifications:
            first_mismatch = mismatch_verifications[0]
            return RuleEvaluationRead(
                id=eval_id,
                bidder_id=context.get("bidder_id", "UNKNOWN_BIDDER"),
                requirement_id=rule.id,
                status=ComplianceStatus.REVIEW_REQUIRED,
                reason_code=ReasonCode.VERIFICATION_MISMATCH,
                observed_value={
                    "claimed": first_mismatch.claimed_value,
                    "verified": first_mismatch.verified_value,
                },
                expected_value=rule.expected_value,
                evidence_ids=evidence_ids,
                rule_version="1.0",
                evaluated_at=now,
            )

        # PRECEDENCE 4: Conflicting facts across documents
        facts_with_val = [f for f in matching_facts if f.value is not None]
        has_fact_conflict = False
        if len(facts_with_val) > 1:
            first_f = facts_with_val[0].value
            for f in facts_with_val[1:]:
                if not cls._values_equivalent(first_f, f.value):
                    has_fact_conflict = True
                    break

        if has_fact_conflict:
            return RuleEvaluationRead(
                id=eval_id,
                bidder_id=context.get("bidder_id", "UNKNOWN_BIDDER"),
                requirement_id=rule.id,
                status=ComplianceStatus.REVIEW_REQUIRED,
                reason_code=ReasonCode.CONFLICTING_FACTS,
                observed_value=[f.value for f in facts_with_val],
                expected_value=rule.expected_value,
                evidence_ids=evidence_ids,
                rule_version="1.0",
                evaluated_at=now,
            )

        # PRECEDENCE 5: Claim vs verified mismatch (when both exist)
        if facts_with_val and verified_with_val:
            claimed_val = facts_with_val[0].value
            raw_verified_val = verified_with_val[0].verified_value

            resolved_verified_val, is_unambiguous = cls._resolve_scalar(raw_verified_val)
            if not is_unambiguous:
                return RuleEvaluationRead(
                    id=eval_id,
                    bidder_id=context.get("bidder_id", "UNKNOWN_BIDDER"),
                    requirement_id=rule.id,
                    status=ComplianceStatus.REVIEW_REQUIRED,
                    reason_code=ReasonCode.AMBIGUOUS_VERIFIED_VALUE,
                    observed_value={"claimed": claimed_val, "verified": raw_verified_val},
                    expected_value=rule.expected_value,
                    evidence_ids=evidence_ids,
                    rule_version="1.0",
                    evaluated_at=now,
                )

            if not cls._values_equivalent(claimed_val, resolved_verified_val):
                return RuleEvaluationRead(
                    id=eval_id,
                    bidder_id=context.get("bidder_id", "UNKNOWN_BIDDER"),
                    requirement_id=rule.id,
                    status=ComplianceStatus.REVIEW_REQUIRED,
                    reason_code=ReasonCode.CLAIM_VERIFICATION_MISMATCH,
                    observed_value={"claimed": claimed_val, "verified": resolved_verified_val},
                    expected_value=rule.expected_value,
                    evidence_ids=evidence_ids,
                    rule_version="1.0",
                    evaluated_at=now,
                )

        # PRECEDENCE 6: EXISTS / NOT_EXISTS operators
        has_usable_evidence = bool(facts_with_val or verified_with_val)

        if rule.operator == OperatorEnum.EXISTS:
            status = (
                ComplianceStatus.PASS
                if has_usable_evidence
                else (ComplianceStatus.FAIL if rule.mandatory else ComplianceStatus.NOT_APPLICABLE)
            )
            reason = ReasonCode.EVIDENCE_EXISTS if status == ComplianceStatus.PASS else ReasonCode.EVIDENCE_MISSING
            return RuleEvaluationRead(
                id=eval_id,
                bidder_id=context.get("bidder_id", "UNKNOWN_BIDDER"),
                requirement_id=rule.id,
                status=status,
                reason_code=reason,
                observed_value=has_usable_evidence,
                expected_value=True,
                evidence_ids=evidence_ids,
                rule_version="1.0",
                evaluated_at=now,
            )

        if rule.operator == OperatorEnum.NOT_EXISTS:
            status = ComplianceStatus.PASS if not has_usable_evidence else ComplianceStatus.FAIL
            reason = ReasonCode.EVIDENCE_ABSENT if status == ComplianceStatus.PASS else ReasonCode.EVIDENCE_PRESENT
            return RuleEvaluationRead(
                id=eval_id,
                bidder_id=context.get("bidder_id", "UNKNOWN_BIDDER"),
                requirement_id=rule.id,
                status=status,
                reason_code=reason,
                observed_value=has_usable_evidence,
                expected_value=False,
                evidence_ids=evidence_ids,
                rule_version="1.0",
                evaluated_at=now,
            )

        # PRECEDENCE 7: Missing evidence for standard operators
        if not has_usable_evidence:
            status = ComplianceStatus.UNKNOWN if rule.mandatory else ComplianceStatus.NOT_APPLICABLE
            return RuleEvaluationRead(
                id=eval_id,
                bidder_id=context.get("bidder_id", "UNKNOWN_BIDDER"),
                requirement_id=rule.id,
                status=status,
                reason_code=ReasonCode.MISSING_EVIDENCE,
                observed_value=None,
                expected_value=rule.expected_value,
                evidence_ids=evidence_ids,
                rule_version="1.0",
                evaluated_at=now,
            )

        # PRECEDENCE 8: Deterministic operator evaluation against effective observed value
        raw_obs_val = verified_with_val[0].verified_value if verified_with_val else facts_with_val[0].value
        resolved_obs_val, is_unambiguous = cls._resolve_scalar(raw_obs_val)

        if not is_unambiguous:
            return RuleEvaluationRead(
                id=eval_id,
                bidder_id=context.get("bidder_id", "UNKNOWN_BIDDER"),
                requirement_id=rule.id,
                status=ComplianceStatus.REVIEW_REQUIRED,
                reason_code=ReasonCode.AMBIGUOUS_VERIFIED_VALUE,
                observed_value=raw_obs_val,
                expected_value=rule.expected_value,
                evidence_ids=evidence_ids,
                rule_version="1.0",
                evaluated_at=now,
            )

        status, reason_code = cls._evaluate_operator(
            rule.operator, resolved_obs_val, rule.expected_value
        )

        return RuleEvaluationRead(
            id=eval_id,
            bidder_id=context.get("bidder_id", "UNKNOWN_BIDDER"),
            requirement_id=rule.id,
            status=status,
            reason_code=reason_code,
            observed_value=resolved_obs_val,
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
            return ComplianceStatus.UNKNOWN, ReasonCode.OBSERVED_VALUE_NULL

        if operator == OperatorEnum.EQ:
            match = cls._values_equivalent(observed, expected)
            return (ComplianceStatus.PASS, ReasonCode.EQUAL) if match else (ComplianceStatus.FAIL, ReasonCode.NOT_EQUAL)

        elif operator == OperatorEnum.NE:
            match = cls._values_equivalent(observed, expected)
            return (ComplianceStatus.FAIL, ReasonCode.EQUAL) if match else (ComplianceStatus.PASS, ReasonCode.NOT_EQUAL)

        elif operator in (OperatorEnum.GT, OperatorEnum.GTE, OperatorEnum.LT, OperatorEnum.LTE):
            obs_num = cls._normalize_number(observed)
            exp_num = cls._normalize_number(expected)

            if obs_num is None or exp_num is None:
                return ComplianceStatus.REVIEW_REQUIRED, ReasonCode.TYPE_CONVERSION_ERROR

            if operator == OperatorEnum.GT:
                return (ComplianceStatus.PASS, ReasonCode.GREATER_THAN) if obs_num > exp_num else (ComplianceStatus.FAIL, ReasonCode.NOT_GREATER_THAN)
            elif operator == OperatorEnum.GTE:
                return (ComplianceStatus.PASS, ReasonCode.GREATER_THAN_OR_EQUAL) if obs_num >= exp_num else (ComplianceStatus.FAIL, ReasonCode.LESS_THAN)
            elif operator == OperatorEnum.LT:
                return (ComplianceStatus.PASS, ReasonCode.LESS_THAN) if obs_num < exp_num else (ComplianceStatus.FAIL, ReasonCode.NOT_LESS_THAN)
            elif operator == OperatorEnum.LTE:
                return (ComplianceStatus.PASS, ReasonCode.LESS_THAN_OR_EQUAL) if obs_num <= exp_num else (ComplianceStatus.FAIL, ReasonCode.GREATER_THAN)

        elif operator == OperatorEnum.COUNT_GTE:
            if isinstance(observed, (list, tuple, set)):
                obs_count = len(observed)
            else:
                num = cls._normalize_number(observed)
                if num is None:
                    return ComplianceStatus.REVIEW_REQUIRED, ReasonCode.TYPE_CONVERSION_ERROR
                obs_count = int(num)

            exp_num = cls._normalize_number(expected)
            if exp_num is None or obs_count < 0:
                return ComplianceStatus.REVIEW_REQUIRED, ReasonCode.TYPE_CONVERSION_ERROR

            exp_count = int(exp_num)
            return (ComplianceStatus.PASS, ReasonCode.COUNT_SUFFICIENT) if obs_count >= exp_count else (ComplianceStatus.FAIL, ReasonCode.COUNT_INSUFFICIENT)

        elif operator in (OperatorEnum.DATE_BEFORE, OperatorEnum.DATE_AFTER):
            obs_dt = cls._normalize_date(observed)
            exp_dt = cls._normalize_date(expected)

            if obs_dt is None or exp_dt is None:
                return ComplianceStatus.REVIEW_REQUIRED, ReasonCode.MALFORMED_DATE

            if operator == OperatorEnum.DATE_BEFORE:
                return (ComplianceStatus.PASS, ReasonCode.BEFORE_DATE) if obs_dt < exp_dt else (ComplianceStatus.FAIL, ReasonCode.ON_OR_AFTER_DATE)
            elif operator == OperatorEnum.DATE_AFTER:
                return (ComplianceStatus.PASS, ReasonCode.AFTER_DATE) if obs_dt > exp_dt else (ComplianceStatus.FAIL, ReasonCode.ON_OR_BEFORE_DATE)

        elif operator == OperatorEnum.IN:
            exp_list = expected if isinstance(expected, (list, tuple, set)) else [expected]
            match = any(cls._values_equivalent(observed, item) for item in exp_list)
            return (ComplianceStatus.PASS, ReasonCode.VALUE_IN_SET) if match else (ComplianceStatus.FAIL, ReasonCode.VALUE_NOT_IN_SET)

        elif operator == OperatorEnum.NOT_IN:
            exp_list = expected if isinstance(expected, (list, tuple, set)) else [expected]
            match = any(cls._values_equivalent(observed, item) for item in exp_list)
            return (ComplianceStatus.PASS, ReasonCode.VALUE_NOT_IN_SET) if not match else (ComplianceStatus.FAIL, ReasonCode.VALUE_IN_SET)

        return ComplianceStatus.UNKNOWN, ReasonCode.UNSUPPORTED_OPERATOR
