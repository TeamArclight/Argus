from datetime import datetime, timezone
import uuid
from typing import Any

from app.compliance.reason_codes import ReasonCode
from app.schemas.canonical import (
    ComplianceStatus,
    FactRead,
    OperatorEnum,
    RequirementType,
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
    def _is_status_or_enum_field(field_name: str, requirement_type: Any = None) -> bool:
        """Determines if a field is an enum or status-like field justifying case-insensitivity."""
        if not field_name:
            return False
        f_lower = field_name.strip().lower()
        return (
            f_lower.endswith(".status")
            or f_lower.endswith(".state")
            or f_lower.endswith(".type")
            or f_lower.endswith(".mode")
            or f_lower.endswith(".flag")
            or f_lower.endswith(".category")
            or f_lower in ("status", "state", "mode", "type", "category", "flag", "debarment.status", "gst.status", "registration.status")
        )

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
        """Normalizes boolean values from bools, numbers, and case-insensitive string keywords."""
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
    def _normalize_count(val: Any) -> int | None:
        """Normalizes count to a non-negative integer. Rejects negative or fractional values."""
        if val is None or isinstance(val, bool):
            return None
        if isinstance(val, (list, tuple, set)):
            return len(val)
        if isinstance(val, int):
            if val >= 0:
                return val
            return None
        if isinstance(val, float):
            if val >= 0 and val.is_integer():
                return int(val)
            return None
        if isinstance(val, str):
            s = val.strip()
            if not s:
                return None
            try:
                num = float(s)
                if num >= 0 and num.is_integer():
                    return int(num)
                return None
            except ValueError:
                return None
        return None

    @staticmethod
    def _normalize_string(val: Any) -> str | None:
        """Trims whitespace from string representation while preserving case."""
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
    def _values_equivalent(cls, a: Any, b: Any, *, case_insensitive: bool = False) -> bool:
        """Determines semantic equality across numeric, boolean, date, and string domains."""
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

        # 4. String comparison (case-insensitive only when explicitly justified)
        str_a = cls._normalize_string(a)
        str_b = cls._normalize_string(b)
        if str_a is not None and str_b is not None:
            if case_insensitive:
                return str_a.lower() == str_b.lower()
            return str_a == str_b

        return False

    @staticmethod
    def _resolve_scalar(val: Any) -> tuple[Any, bool]:
        """Resolves scalar value from structured dicts deterministically without guessing."""
        if isinstance(val, dict):
            recognized_keys = {"value", "verified_value", "claimed_value"}
            harmless_meta_keys = {
                "unit",
                "currency",
                "source",
                "source_page",
                "source_text",
                "confidence",
                "location_metadata",
            }
            scalar_keys = [k for k in val if k in recognized_keys]

            if len(scalar_keys) == 1:
                k = scalar_keys[0]
                other_keys = [x for x in val if x != k]
                if all(x in harmless_meta_keys for x in other_keys):
                    return val[k], True
                return val, False
            elif len(scalar_keys) > 1:
                return val, False
            else:
                if len(val) == 1:
                    return list(val.values())[0], True
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

        is_ci = cls._is_status_or_enum_field(rule.field, rule.requirement_type)

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
                if not cls._values_equivalent(first_v, v.verified_value, case_insensitive=is_ci):
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
                if not cls._values_equivalent(first_f, f.value, case_insensitive=is_ci):
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

            if not cls._values_equivalent(claimed_val, resolved_verified_val, case_insensitive=is_ci):
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

        # PRECEDENCE 6: Handle EXISTS / NOT_EXISTS operators
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

        # PRECEDENCE 7: Handle missing evidence for standard operators
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

        status, reason_code = cls._evaluate_operator(rule, resolved_obs_val, rule.expected_value)

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
        cls, rule: TenderRequirementRead, observed: Any, expected: Any
    ) -> tuple[ComplianceStatus, str]:
        operator = rule.operator
        if observed is None:
            return ComplianceStatus.UNKNOWN, ReasonCode.OBSERVED_VALUE_NULL

        is_ci = cls._is_status_or_enum_field(rule.field, rule.requirement_type)

        if operator in (OperatorEnum.EQ, OperatorEnum.NE):
            # 1. Check if expected establishes boolean requirement
            bool_exp = cls._normalize_bool(expected)
            if isinstance(expected, bool) or (
                bool_exp is not None
                and isinstance(expected, (str, int))
                and str(expected).strip().lower() in ("true", "false", "yes", "no")
            ):
                bool_obs = cls._normalize_bool(observed)
                if bool_obs is None:
                    return ComplianceStatus.REVIEW_REQUIRED, ReasonCode.TYPE_CONVERSION_ERROR
                match = bool_obs == bool_exp
                if operator == OperatorEnum.EQ:
                    return (ComplianceStatus.PASS, ReasonCode.EQUAL) if match else (ComplianceStatus.FAIL, ReasonCode.NOT_EQUAL)
                else:
                    return (ComplianceStatus.FAIL, ReasonCode.EQUAL) if match else (ComplianceStatus.PASS, ReasonCode.NOT_EQUAL)

            # 2. Check if expected establishes numeric requirement
            num_exp = cls._normalize_number(expected)
            if num_exp is not None and not isinstance(expected, bool) and not (
                isinstance(expected, str) and expected.strip().lower() in ("true", "false", "yes", "no")
            ):
                num_obs = cls._normalize_number(observed)
                if num_obs is None:
                    return ComplianceStatus.REVIEW_REQUIRED, ReasonCode.TYPE_CONVERSION_ERROR
                match = num_obs == num_exp
                if operator == OperatorEnum.EQ:
                    return (ComplianceStatus.PASS, ReasonCode.EQUAL) if match else (ComplianceStatus.FAIL, ReasonCode.NOT_EQUAL)
                else:
                    return (ComplianceStatus.FAIL, ReasonCode.EQUAL) if match else (ComplianceStatus.PASS, ReasonCode.NOT_EQUAL)

            # 3. Check if expected establishes date requirement
            dt_exp = cls._normalize_date(expected)
            if dt_exp is not None and isinstance(expected, (datetime, str)) and (
                isinstance(expected, datetime) or "/" in str(expected) or "-" in str(expected) or "date" in rule.field.lower()
            ):
                dt_obs = cls._normalize_date(observed)
                if dt_obs is None:
                    return ComplianceStatus.REVIEW_REQUIRED, ReasonCode.MALFORMED_DATE
                match = dt_obs == dt_exp
                if operator == OperatorEnum.EQ:
                    return (ComplianceStatus.PASS, ReasonCode.EQUAL) if match else (ComplianceStatus.FAIL, ReasonCode.NOT_EQUAL)
                else:
                    return (ComplianceStatus.FAIL, ReasonCode.EQUAL) if match else (ComplianceStatus.PASS, ReasonCode.NOT_EQUAL)

            # 4. General string / enum comparison
            match = cls._values_equivalent(observed, expected, case_insensitive=is_ci)
            if operator == OperatorEnum.EQ:
                return (ComplianceStatus.PASS, ReasonCode.EQUAL) if match else (ComplianceStatus.FAIL, ReasonCode.NOT_EQUAL)
            else:
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
            obs_cnt = cls._normalize_count(observed)
            exp_cnt = cls._normalize_count(expected)

            if obs_cnt is None or exp_cnt is None:
                return ComplianceStatus.REVIEW_REQUIRED, ReasonCode.TYPE_CONVERSION_ERROR

            return (ComplianceStatus.PASS, ReasonCode.COUNT_SUFFICIENT) if obs_cnt >= exp_cnt else (ComplianceStatus.FAIL, ReasonCode.COUNT_INSUFFICIENT)

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
            match = any(cls._values_equivalent(observed, item, case_insensitive=is_ci) for item in exp_list)
            return (ComplianceStatus.PASS, ReasonCode.VALUE_IN_SET) if match else (ComplianceStatus.FAIL, ReasonCode.VALUE_NOT_IN_SET)

        elif operator == OperatorEnum.NOT_IN:
            exp_list = expected if isinstance(expected, (list, tuple, set)) else [expected]
            match = any(cls._values_equivalent(observed, item, case_insensitive=is_ci) for item in exp_list)
            return (ComplianceStatus.PASS, ReasonCode.VALUE_NOT_IN_SET) if not match else (ComplianceStatus.FAIL, ReasonCode.VALUE_IN_SET)

        return ComplianceStatus.UNKNOWN, ReasonCode.UNSUPPORTED_OPERATOR
