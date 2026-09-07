from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re
from typing import Any
import uuid

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
    Uses exact Decimal arithmetic for numeric and financial comparisons.
    """

    ENGINE_VERSION = "1.2.0"
    NORMALIZATION_POLICY_VERSION = "1.2.0"
    OPERATOR_SEMANTICS_VERSION = "1.2.0"

    UNIT_SCALE_MAP: dict[str, tuple[Decimal, str]] = {
        "crore": (Decimal("10000000"), "Crore"),
        "crores": (Decimal("10000000"), "Crore"),
        "cr": (Decimal("10000000"), "Crore"),
        "cr.": (Decimal("10000000"), "Crore"),
        "lakh": (Decimal("100000"), "Lakh"),
        "lakhs": (Decimal("100000"), "Lakh"),
        "lac": (Decimal("100000"), "Lakh"),
        "lacs": (Decimal("100000"), "Lakh"),
        "thousand": (Decimal("1000"), "Thousand"),
        "thousands": (Decimal("1000"), "Thousand"),
        "k": (Decimal("1000"), "Thousand"),
        "million": (Decimal("1000000"), "Million"),
        "millions": (Decimal("1000000"), "Million"),
        "mn": (Decimal("1000000"), "Million"),
        "billion": (Decimal("1000000000"), "Billion"),
        "billions": (Decimal("1000000000"), "Billion"),
        "bn": (Decimal("1000000000"), "Billion"),
    }

    @staticmethod
    def compute_rules_hash(rules: list[TenderRequirementRead | dict[str, Any]]) -> str:
        """Computes a deterministic canonical SHA-256 hash of approved rule definitions."""
        canonical_items = []
        for r in rules:
            if hasattr(r, "model_dump"):
                r_dict = r.model_dump(mode="json")
            elif hasattr(r, "__dict__"):
                r_dict = r.__dict__
            else:
                r_dict = dict(r)
            canonical_items.append({
                "clause": str(r_dict.get("clause") or ""),
                "field": str(r_dict.get("field") or ""),
                "operator": str(r_dict.get("operator") or ""),
                "expected_value": r_dict.get("expected_value"),
                "unit": str(r_dict.get("unit") or ""),
                "mandatory": bool(r_dict.get("mandatory", True)),
                "requirement_type": str(r_dict.get("requirement_type") or ""),
            })
        canonical_items.sort(key=lambda x: (x["field"], x["operator"], str(x["expected_value"]), x["clause"]))
        serialized = json.dumps(canonical_items, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

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
            or f_lower in (
                "status",
                "state",
                "mode",
                "type",
                "category",
                "flag",
                "debarment.status",
                "gst.status",
                "registration.status",
            )
        )

    @classmethod
    def _validate_comma_formatting(cls, s: str) -> bool:
        """Validates that commas follow standard Western or Indian numeral grouping without corruption."""
        if "," not in s:
            return True
        if ",," in s or s.startswith(",") or s.endswith(","):
            return False
        parts = s.split(".")
        if len(parts) > 2:
            return False
        int_part = parts[0]
        if len(parts) == 2 and "," in parts[1]:
            # No commas allowed after decimal point
            return False

        # Western grouping: 1,234,567
        western_pattern = r"^\d{1,3}(,\d{3})*$"
        # Indian grouping: 1,23,45,678 or 12,34,567
        indian_pattern = r"^\d{1,2}(,\d{2})*,\d{3}$"

        if re.match(western_pattern, int_part) or re.match(indian_pattern, int_part):
            return True
        return False

    @classmethod
    def _normalize_number(cls, val: Any) -> Decimal | None:
        """Normalizes numeric representations to exact Decimal.

        Rejects NaN, Infinity, -Infinity, boolean types, corrupt commas, and malformed strings.
        Converts finite float inputs through their string representation.
        """
        if val is None or isinstance(val, bool):
            return None
        if isinstance(val, Decimal):
            if val.is_nan() or val.is_infinite():
                return None
            return val
        if isinstance(val, int):
            return Decimal(val)
        if isinstance(val, float):
            if math.isnan(val) or math.isinf(val):
                return None
            return Decimal(str(val))
        if isinstance(val, str):
            s = val.strip()
            if not s:
                return None
            s_lower = s.lower()
            if s_lower in ("nan", "inf", "-inf", "+inf", "infinity", "-infinity", "+infinity"):
                return None

            # Strip standard currency tokens
            s_clean = s.replace("₹", "").replace("$", "").replace("€", "").replace("£", "")
            tokens_to_remove = [
                "INR", "inr", "Rs.", "rs.", "Rs", "rs", "USD", "usd", "EUR", "eur", "GBP", "gbp", "Rupees", "rupees"
            ]
            for token in tokens_to_remove:
                s_clean = re.sub(r"\b" + re.escape(token) + r"\b", "", s_clean, flags=re.IGNORECASE)
            s_clean = s_clean.strip()
            if not s_clean:
                return None

            # Check scale words in string
            scale_mult = Decimal("1")
            for scale_token, (mult, _) in cls.UNIT_SCALE_MAP.items():
                pattern = r"\b" + re.escape(scale_token) + r"\b"
                if re.search(pattern, s_clean, flags=re.IGNORECASE):
                    scale_mult = mult
                    s_clean = re.sub(pattern, "", s_clean, flags=re.IGNORECASE).strip()
                    break

            if not cls._validate_comma_formatting(s_clean):
                return None

            s_clean = s_clean.replace(",", "").strip()
            try:
                dec = Decimal(s_clean)
                if dec.is_nan() or dec.is_infinite():
                    return None
                return dec * scale_mult
            except (InvalidOperation, TypeError, ValueError):
                return None

        return None

    @classmethod
    def _parse_currency_and_scale(
        cls, val: Any, meta: dict[str, Any] | None = None, rule_unit: str | None = None
    ) -> tuple[Decimal | None, str | None, str | None, bool]:
        """Parses currency, unit/scale, and Decimal numeric base value from string or metadata.

        Applies scale exactly once. Requires explicit compatibility.
        Returns: (scaled_decimal_value, currency, canonical_unit, is_valid)
        """
        meta = meta or {}
        currency = meta.get("currency")
        meta_unit = meta.get("unit") or rule_unit

        val_str = str(val) if val is not None else ""
        val_lower = val_str.lower().strip()

        # Currency detection
        text_curr = None
        if "$" in val_str or re.search(r"\b(usd)\b", val_lower):
            text_curr = "USD"
        elif "₹" in val_str or re.search(r"\b(inr|rs\.?|rupees?)\b", val_lower):
            text_curr = "INR"
        elif "€" in val_str or re.search(r"\b(eur|euros?)\b", val_lower):
            text_curr = "EUR"
        elif "£" in val_str or re.search(r"\b(gbp|pounds?)\b", val_lower):
            text_curr = "GBP"

        # Check currency contradiction between metadata and text
        if currency and text_curr and currency.upper() != text_curr.upper():
            return None, currency, meta_unit, False

        currency = currency or text_curr

        # Scale resolution from metadata
        meta_scale = Decimal("1")
        canonical_meta_unit = None
        if meta_unit and isinstance(meta_unit, str):
            meta_unit_clean = meta_unit.lower().strip()
            # Extract currency if embedded in unit (e.g. "INR Crore")
            if "inr" in meta_unit_clean and not currency:
                currency = "INR"
            if "usd" in meta_unit_clean and not currency:
                currency = "USD"
            meta_unit_clean = (
                meta_unit_clean.replace("inr", "")
                .replace("usd", "")
                .replace("eur", "")
                .replace("₹", "")
                .replace("$", "")
                .strip()
            )

            if meta_unit_clean in cls.UNIT_SCALE_MAP:
                meta_scale, canonical_meta_unit = cls.UNIT_SCALE_MAP[meta_unit_clean]
            elif meta_unit_clean:
                canonical_meta_unit = meta_unit.strip()

        # Scale resolution from text
        text_scale = Decimal("1")
        canonical_text_unit = None
        for scale_token, (scale_factor, canon_name) in cls.UNIT_SCALE_MAP.items():
            if re.search(r"\b" + re.escape(scale_token) + r"\b", val_lower):
                text_scale = scale_factor
                canonical_text_unit = canon_name
                break

        # Check unit/scale contradiction between metadata and text
        if (
            canonical_meta_unit
            and canonical_text_unit
            and canonical_meta_unit.lower() != canonical_text_unit.lower()
        ):
            return None, currency, canonical_meta_unit, False

        resolved_unit = canonical_meta_unit or canonical_text_unit
        # Apply scale exactly once: if both match, use the single resolved scale
        scale = meta_scale if canonical_meta_unit else (text_scale if canonical_text_unit else Decimal("1"))
        is_explicit = bool(resolved_unit or currency)

        # Clean text tokens from value before numeric parse
        clean_s = val_str
        tokens_to_remove = [
            "USD", "usd", "$", "INR", "inr", "₹", "Rs.", "rs.", "Rs", "rs", "EUR", "eur", "GBP", "gbp", "Rupees", "rupees",
            "crore", "crores", "cr.", "cr", "lakh", "lakhs", "lac", "lacs", "billion", "billions", "bn", "million", "millions", "mn", "thousand", "thousands", "k"
        ]
        for token in tokens_to_remove:
            if token in ("$", "₹", "€", "£"):
                clean_s = clean_s.replace(token, "")
            else:
                clean_s = re.sub(r"\b" + re.escape(token) + r"\b", "", clean_s, flags=re.IGNORECASE)

        if not cls._validate_comma_formatting(clean_s.strip()):
            return None, currency, resolved_unit, False

        clean_s = clean_s.replace(",", "").strip()
        if not clean_s:
            return None, currency, resolved_unit, is_explicit

        try:
            base_dec = Decimal(clean_s)
            if base_dec.is_nan() or base_dec.is_infinite():
                return None, currency, resolved_unit, False
            scaled_val = base_dec * scale
            return scaled_val, currency, resolved_unit, is_explicit
        except (InvalidOperation, TypeError, ValueError):
            return None, currency, resolved_unit, False

    @staticmethod
    def _normalize_bool(val: Any) -> bool | None:
        """Normalizes boolean values from bools, numbers, and case-insensitive string keywords."""
        if val is None:
            return None
        if isinstance(val, bool):
            return val
        if isinstance(val, (int, float, Decimal)):
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
        """Normalizes count to a non-negative integer. Rejects negative, fractional, or boolean values."""
        if val is None or isinstance(val, bool):
            return None
        if isinstance(val, (list, tuple, set)):
            return len(val)
        if isinstance(val, int):
            return val if val >= 0 else None
        if isinstance(val, (float, Decimal)):
            if val >= 0 and val == int(val):
                return int(val)
            return None
        if isinstance(val, str):
            s = val.strip()
            if not s:
                return None
            try:
                num = int(s)
                return num if num >= 0 else None
            except ValueError:
                try:
                    f = float(s)
                    if f >= 0 and f.is_integer():
                        return int(f)
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

    @classmethod
    def _parse_date_or_datetime(cls, val: Any) -> tuple[datetime | date | None, bool]:
        """Parses timezone-aware datetime or date-only objects.

        Returns: (parsed_object, is_datetime)
        """
        if val is None:
            return None, False
        if isinstance(val, datetime):
            return val, True
        if isinstance(val, date) and not isinstance(val, datetime):
            return val, False
        if isinstance(val, str):
            s = val.strip()
            if not s:
                return None, False

            # Try ISO 8601 with or without timezone
            try:
                # Differentiate date-only from datetime
                if "T" in s or (" " in s and ":" in s):
                    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
                    return dt, True
                else:
                    # Date-only ISO format YYYY-MM-DD
                    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
                        d = date.fromisoformat(s)
                        return d, False
            except (ValueError, TypeError):
                pass

            # Standard date-only formats with leap-year boundary validation
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
                try:
                    dt = datetime.strptime(s, fmt)
                    return dt.date(), False
                except ValueError:
                    continue

        return None, False

    @classmethod
    def _normalize_date(cls, val: Any) -> datetime | None:
        """Legacy helper normalizing to timezone-aware datetime."""
        obj, is_dt = cls._parse_date_or_datetime(val)
        if obj is None:
            return None
        if is_dt and isinstance(obj, datetime):
            if obj.tzinfo is None:
                return obj.replace(tzinfo=timezone.utc)
            return obj
        if isinstance(obj, date):
            return datetime.combine(obj, datetime.min.time(), tzinfo=timezone.utc)
        return None

    @classmethod
    def _values_equivalent(
        cls,
        a: Any,
        b: Any,
        *,
        meta_a: dict[str, Any] | None = None,
        meta_b: dict[str, Any] | None = None,
        unit: str | None = None,
        rule_unit: str | None = None,
        case_insensitive: bool = False,
    ) -> bool:
        """Determines semantic equality across numeric, boolean, date, and string domains."""
        if a is None or b is None:
            return a is b
        if a == b:
            return True

        eff_unit = unit or rule_unit

        # 1. Numeric / Financial comparison with Decimal and Scale
        dec_a, curr_a, unit_a, valid_a = cls._parse_currency_and_scale(a, meta_a, rule_unit=eff_unit)
        dec_b, curr_b, unit_b, valid_b = cls._parse_currency_and_scale(b, meta_b, rule_unit=eff_unit)
        if dec_a is not None and dec_b is not None:
            # If currencies are explicitly provided and differ, they are not equivalent (no implicit FX)
            if curr_a and curr_b and curr_a.upper() != curr_b.upper():
                return False
            return dec_a == dec_b

        # Fallback simple numeric comparison
        num_a = cls._normalize_number(a)
        num_b = cls._normalize_number(b)
        if num_a is not None and num_b is not None:
            return num_a == num_b

        # 2. Boolean comparison
        bool_a = cls._normalize_bool(a)
        bool_b = cls._normalize_bool(b)
        if bool_a is not None and bool_b is not None:
            return bool_a == bool_b

        # 3. Date / Datetime comparison
        obj_a, is_dt_a = cls._parse_date_or_datetime(a)
        obj_b, is_dt_b = cls._parse_date_or_datetime(b)
        if obj_a is not None and obj_b is not None:
            if not is_dt_a and not is_dt_b:
                return obj_a == obj_b
            if is_dt_a and is_dt_b:
                dt_a = obj_a.astimezone(timezone.utc) if obj_a.tzinfo else obj_a
                dt_b = obj_b.astimezone(timezone.utc) if obj_b.tzinfo else obj_b
                return dt_a == dt_b
            # Mixed date and datetime
            d_a = obj_a.date() if isinstance(obj_a, datetime) else obj_a
            d_b = obj_b.date() if isinstance(obj_b, datetime) else obj_b
            return d_a == d_b

        # 4. String comparison (case-insensitive only when explicitly justified)
        str_a = cls._normalize_string(a)
        str_b = cls._normalize_string(b)
        if str_a is not None and str_b is not None:
            if case_insensitive:
                return str_a.lower() == str_b.lower()
            return str_a == str_b

        return False

    @staticmethod
    def _resolve_scalar(val: Any) -> tuple[Any, bool, dict[str, Any]]:
        """Resolves scalar value and metadata from structured dicts deterministically."""
        if isinstance(val, dict):
            recognized_keys = {"value", "verified_value", "claimed_value"}
            meta = {
                k: v
                for k, v in val.items()
                if k in ("unit", "currency", "source", "source_page", "source_text", "confidence", "location_metadata", "fy", "metric")
            }
            scalar_keys = [k for k in val if k in recognized_keys]

            if len(scalar_keys) == 1:
                k = scalar_keys[0]
                other_keys = [x for x in val if x != k]
                if all(x in meta for x in other_keys):
                    return val[k], True, meta
                return val, False, meta
            elif len(scalar_keys) > 1:
                return val, False, meta
            else:
                if len(val) == 1:
                    return list(val.values())[0], True, meta
                return val, False, meta
        return val, True, {}

    @classmethod
    def evaluate(
        cls,
        rule: TenderRequirementRead,
        facts: list[FactRead],
        verification_results: list[VerificationResultRead],
        context: dict[str, Any] | None = None,
        evaluation_timestamp: datetime | None = None,
    ) -> RuleEvaluationRead:
        context = context or {}
        if hasattr(rule, "is_approved") and rule.is_approved is False:
            raise ValueError(f"ComplianceEngine cannot evaluate unapproved requirement candidate '{getattr(rule, 'id', 'UNKNOWN')}'.")

        # Deterministic evaluation timestamp: accept explicit timestamp or context timestamp
        eval_ts = evaluation_timestamp or context.get("evaluation_timestamp") or context.get("evaluated_at")
        if eval_ts is None:
            eval_ts = datetime.now(timezone.utc)

        eval_id = str(uuid.uuid4())
        is_ci = cls._is_status_or_enum_field(rule.field, rule.requirement_type)

        matching_facts = [f for f in facts if f.field == rule.field]
        matching_verifications = [v for v in verification_results if v.field == rule.field]

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
                evidence_ids=[v.id for v in unhealthy_verifications],
                rule_version="1.2.0",
                evaluated_at=eval_ts,
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
                if not cls._values_equivalent(first_v, v.verified_value, unit=rule.unit, case_insensitive=is_ci):
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
                evidence_ids=[v.id for v in verified_with_val],
                rule_version="1.2.0",
                evaluated_at=eval_ts,
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
                evidence_ids=[v.id for v in mismatch_verifications],
                rule_version="1.2.0",
                evaluated_at=eval_ts,
            )

        # PRECEDENCE 4: Conflicting facts across documents
        facts_with_val = [f for f in matching_facts if f.value is not None]
        has_fact_conflict = False
        if len(facts_with_val) > 1:
            first_f = facts_with_val[0]
            for f in facts_with_val[1:]:
                if not cls._values_equivalent(
                    first_f.value,
                    f.value,
                    meta_a=getattr(first_f, "metadata_json", None),
                    meta_b=getattr(f, "metadata_json", None),
                    unit=rule.unit,
                    case_insensitive=is_ci,
                ):
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
                evidence_ids=[f.id for f in facts_with_val],
                rule_version="1.2.0",
                evaluated_at=eval_ts,
            )

        # PRECEDENCE 5: Claim vs verified mismatch (when both exist)
        if facts_with_val and verified_with_val:
            claimed_val = facts_with_val[0].value
            raw_verified_val = verified_with_val[0].verified_value

            resolved_verified_val, is_unambiguous, ver_meta = cls._resolve_scalar(raw_verified_val)
            if not is_unambiguous:
                return RuleEvaluationRead(
                    id=eval_id,
                    bidder_id=context.get("bidder_id", "UNKNOWN_BIDDER"),
                    requirement_id=rule.id,
                    status=ComplianceStatus.REVIEW_REQUIRED,
                    reason_code=ReasonCode.AMBIGUOUS_VERIFIED_VALUE,
                    observed_value={"claimed": claimed_val, "verified": raw_verified_val},
                    expected_value=rule.expected_value,
                    evidence_ids=[facts_with_val[0].id, verified_with_val[0].id],
                    rule_version="1.2.0",
                    evaluated_at=eval_ts,
                )

            fact_meta = getattr(facts_with_val[0], "metadata_json", {})
            if not cls._values_equivalent(
                claimed_val,
                resolved_verified_val,
                meta_a=fact_meta,
                meta_b=ver_meta,
                unit=rule.unit,
                case_insensitive=is_ci,
            ):
                return RuleEvaluationRead(
                    id=eval_id,
                    bidder_id=context.get("bidder_id", "UNKNOWN_BIDDER"),
                    requirement_id=rule.id,
                    status=ComplianceStatus.REVIEW_REQUIRED,
                    reason_code=ReasonCode.CLAIM_VERIFICATION_MISMATCH,
                    observed_value={"claimed": claimed_val, "verified": resolved_verified_val},
                    expected_value=rule.expected_value,
                    evidence_ids=[facts_with_val[0].id, verified_with_val[0].id],
                    rule_version="1.2.0",
                    evaluated_at=eval_ts,
                )

        # Determine single primary contributing input for standard evaluation
        primary_input_ids = []
        if verified_with_val:
            primary_input_ids = [verified_with_val[0].id]
        elif facts_with_val:
            primary_input_ids = [facts_with_val[0].id]

        # PRECEDENCE 6: Handle EXISTS / NOT_EXISTS operators
        has_usable_evidence = bool(facts_with_val or verified_with_val)

        if rule.operator == OperatorEnum.EXISTS:
            if has_usable_evidence:
                raw_obs_val = verified_with_val[0].verified_value if verified_with_val else facts_with_val[0].value
                resolved_obs_val, _, _ = cls._resolve_scalar(raw_obs_val)
                return RuleEvaluationRead(
                    id=eval_id,
                    bidder_id=context.get("bidder_id", "UNKNOWN_BIDDER"),
                    requirement_id=rule.id,
                    status=ComplianceStatus.PASS,
                    reason_code=ReasonCode.EVIDENCE_EXISTS,
                    observed_value=resolved_obs_val if resolved_obs_val is not None else True,
                    expected_value=True,
                    evidence_ids=primary_input_ids,
                    rule_version="1.2.0",
                    evaluated_at=eval_ts,
                )
            else:
                status = ComplianceStatus.UNKNOWN if rule.mandatory else ComplianceStatus.NOT_APPLICABLE
                return RuleEvaluationRead(
                    id=eval_id,
                    bidder_id=context.get("bidder_id", "UNKNOWN_BIDDER"),
                    requirement_id=rule.id,
                    status=status,
                    reason_code=ReasonCode.MISSING_EVIDENCE,
                    observed_value=None,
                    expected_value=True,
                    evidence_ids=[],
                    rule_version="1.2.0",
                    evaluated_at=eval_ts,
                )

        if rule.operator == OperatorEnum.NOT_EXISTS:
            if not has_usable_evidence:
                status = ComplianceStatus.UNKNOWN if rule.mandatory else ComplianceStatus.NOT_APPLICABLE
                return RuleEvaluationRead(
                    id=eval_id,
                    bidder_id=context.get("bidder_id", "UNKNOWN_BIDDER"),
                    requirement_id=rule.id,
                    status=status,
                    reason_code=ReasonCode.MISSING_EVIDENCE,
                    observed_value=None,
                    expected_value=False,
                    evidence_ids=[],
                    rule_version="1.2.0",
                    evaluated_at=eval_ts,
                )

            raw_obs_val = verified_with_val[0].verified_value if verified_with_val else facts_with_val[0].value
            resolved_obs_val, is_unambiguous, _ = cls._resolve_scalar(raw_obs_val)

            if not is_unambiguous:
                return RuleEvaluationRead(
                    id=eval_id,
                    bidder_id=context.get("bidder_id", "UNKNOWN_BIDDER"),
                    requirement_id=rule.id,
                    status=ComplianceStatus.REVIEW_REQUIRED,
                    reason_code=ReasonCode.AMBIGUOUS_VERIFIED_VALUE,
                    observed_value=raw_obs_val,
                    expected_value=False,
                    evidence_ids=primary_input_ids,
                    rule_version="1.2.0",
                    evaluated_at=eval_ts,
                )

            bool_val = cls._normalize_bool(resolved_obs_val)
            if bool_val is not None:
                status = ComplianceStatus.PASS if bool_val is False else ComplianceStatus.FAIL
                reason = ReasonCode.EVIDENCE_ABSENT if status == ComplianceStatus.PASS else ReasonCode.EVIDENCE_PRESENT
                return RuleEvaluationRead(
                    id=eval_id,
                    bidder_id=context.get("bidder_id", "UNKNOWN_BIDDER"),
                    requirement_id=rule.id,
                    status=status,
                    reason_code=reason,
                    observed_value=resolved_obs_val,
                    expected_value=False,
                    evidence_ids=primary_input_ids,
                    rule_version="1.2.0",
                    evaluated_at=eval_ts,
                )

            if isinstance(resolved_obs_val, (list, tuple, set, dict)):
                status = ComplianceStatus.PASS if len(resolved_obs_val) == 0 else ComplianceStatus.FAIL
                reason = ReasonCode.EVIDENCE_ABSENT if status == ComplianceStatus.PASS else ReasonCode.EVIDENCE_PRESENT
                return RuleEvaluationRead(
                    id=eval_id,
                    bidder_id=context.get("bidder_id", "UNKNOWN_BIDDER"),
                    requirement_id=rule.id,
                    status=status,
                    reason_code=reason,
                    observed_value=resolved_obs_val,
                    expected_value=False,
                    evidence_ids=primary_input_ids,
                    rule_version="1.2.0",
                    evaluated_at=eval_ts,
                )

            return RuleEvaluationRead(
                id=eval_id,
                bidder_id=context.get("bidder_id", "UNKNOWN_BIDDER"),
                requirement_id=rule.id,
                status=ComplianceStatus.REVIEW_REQUIRED,
                reason_code=ReasonCode.TYPE_CONVERSION_ERROR,
                observed_value=resolved_obs_val,
                expected_value=False,
                evidence_ids=primary_input_ids,
                rule_version="1.2.0",
                evaluated_at=eval_ts,
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
                evidence_ids=[],
                rule_version="1.2.0",
                evaluated_at=eval_ts,
            )

        # PRECEDENCE 8: Deterministic operator evaluation against effective observed value
        raw_obs_val = verified_with_val[0].verified_value if verified_with_val else facts_with_val[0].value
        resolved_obs_val, is_unambiguous, obs_meta = cls._resolve_scalar(raw_obs_val)

        if not is_unambiguous:
            return RuleEvaluationRead(
                id=eval_id,
                bidder_id=context.get("bidder_id", "UNKNOWN_BIDDER"),
                requirement_id=rule.id,
                status=ComplianceStatus.REVIEW_REQUIRED,
                reason_code=ReasonCode.AMBIGUOUS_VERIFIED_VALUE,
                observed_value=raw_obs_val,
                expected_value=rule.expected_value,
                evidence_ids=primary_input_ids,
                rule_version="1.2.0",
                evaluated_at=eval_ts,
            )

        # Attach fact metadata if present
        if facts_with_val and hasattr(facts_with_val[0], "metadata_json") and facts_with_val[0].metadata_json:
            obs_meta = {**facts_with_val[0].metadata_json, **obs_meta}

        status, reason_code = cls._evaluate_operator(rule, resolved_obs_val, rule.expected_value, obs_meta=obs_meta)

        return RuleEvaluationRead(
            id=eval_id,
            bidder_id=context.get("bidder_id", "UNKNOWN_BIDDER"),
            requirement_id=rule.id,
            status=status,
            reason_code=reason_code,
            observed_value=resolved_obs_val,
            expected_value=rule.expected_value,
            evidence_ids=primary_input_ids,
            rule_version="1.2.0",
            evaluated_at=eval_ts,
        )

    @classmethod
    def _evaluate_operator(
        cls,
        rule: TenderRequirementRead,
        observed: Any,
        expected: Any,
        obs_meta: dict[str, Any] | None = None,
    ) -> tuple[ComplianceStatus, str]:
        operator = rule.operator
        obs_meta = obs_meta or {}
        rule_meta = getattr(rule, "metadata_json", {}) or {}

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

            # 2. Check if expected establishes financial / numeric requirement with Decimal
            dec_exp, curr_exp, _, valid_exp = cls._parse_currency_and_scale(expected, rule_meta, rule_unit=rule.unit)
            if dec_exp is not None and not isinstance(expected, bool) and not (
                isinstance(expected, str) and expected.strip().lower() in ("true", "false", "yes", "no")
            ):
                dec_obs, curr_obs, _, valid_obs = cls._parse_currency_and_scale(observed, obs_meta, rule_unit=rule.unit)
                if dec_obs is None:
                    return ComplianceStatus.REVIEW_REQUIRED, ReasonCode.TYPE_CONVERSION_ERROR
                # Strict currency check
                if curr_exp and curr_obs and curr_exp.upper() != curr_obs.upper():
                    return ComplianceStatus.REVIEW_REQUIRED, ReasonCode.CURRENCY_MISMATCH
                match = dec_obs == dec_exp
                if operator == OperatorEnum.EQ:
                    return (ComplianceStatus.PASS, ReasonCode.EQUAL) if match else (ComplianceStatus.FAIL, ReasonCode.NOT_EQUAL)
                else:
                    return (ComplianceStatus.FAIL, ReasonCode.EQUAL) if match else (ComplianceStatus.PASS, ReasonCode.NOT_EQUAL)

            # 3. Check if expected establishes date / datetime requirement
            dt_exp_obj, is_dt_exp = cls._parse_date_or_datetime(expected)
            if dt_exp_obj is not None and isinstance(expected, (datetime, date, str)) and (
                isinstance(expected, (datetime, date))
                or "/" in str(expected)
                or "-" in str(expected)
                or "date" in rule.field.lower()
            ):
                dt_obs_obj, is_dt_obs = cls._parse_date_or_datetime(observed)
                if dt_obs_obj is None:
                    return ComplianceStatus.REVIEW_REQUIRED, ReasonCode.MALFORMED_DATE
                match = cls._values_equivalent(observed, expected, case_insensitive=is_ci)
                if operator == OperatorEnum.EQ:
                    return (ComplianceStatus.PASS, ReasonCode.EQUAL) if match else (ComplianceStatus.FAIL, ReasonCode.NOT_EQUAL)
                else:
                    return (ComplianceStatus.FAIL, ReasonCode.EQUAL) if match else (ComplianceStatus.PASS, ReasonCode.NOT_EQUAL)

            # 4. General string / enum comparison
            match = cls._values_equivalent(
                observed,
                expected,
                meta_a=obs_meta,
                meta_b=rule_meta,
                unit=rule.unit,
                case_insensitive=is_ci,
            )
            if operator == OperatorEnum.EQ:
                return (ComplianceStatus.PASS, ReasonCode.EQUAL) if match else (ComplianceStatus.FAIL, ReasonCode.NOT_EQUAL)
            else:
                return (ComplianceStatus.FAIL, ReasonCode.EQUAL) if match else (ComplianceStatus.PASS, ReasonCode.NOT_EQUAL)

        elif operator in (OperatorEnum.GT, OperatorEnum.GTE, OperatorEnum.LT, OperatorEnum.LTE):
            dec_obs, curr_obs, _, _ = cls._parse_currency_and_scale(observed, obs_meta, rule_unit=rule.unit)
            dec_exp, curr_exp, _, _ = cls._parse_currency_and_scale(expected, rule_meta, rule_unit=rule.unit)

            if dec_obs is None or dec_exp is None:
                return ComplianceStatus.REVIEW_REQUIRED, ReasonCode.TYPE_CONVERSION_ERROR

            # Strict currency mismatch check
            if curr_exp and curr_obs and curr_exp.upper() != curr_obs.upper():
                return ComplianceStatus.REVIEW_REQUIRED, ReasonCode.CURRENCY_MISMATCH

            if operator == OperatorEnum.GT:
                return (ComplianceStatus.PASS, ReasonCode.GREATER_THAN) if dec_obs > dec_exp else (ComplianceStatus.FAIL, ReasonCode.NOT_GREATER_THAN)
            elif operator == OperatorEnum.GTE:
                return (ComplianceStatus.PASS, ReasonCode.GREATER_THAN_OR_EQUAL) if dec_obs >= dec_exp else (ComplianceStatus.FAIL, ReasonCode.LESS_THAN)
            elif operator == OperatorEnum.LT:
                return (ComplianceStatus.PASS, ReasonCode.LESS_THAN) if dec_obs < dec_exp else (ComplianceStatus.FAIL, ReasonCode.NOT_LESS_THAN)
            elif operator == OperatorEnum.LTE:
                return (ComplianceStatus.PASS, ReasonCode.LESS_THAN_OR_EQUAL) if dec_obs <= dec_exp else (ComplianceStatus.FAIL, ReasonCode.GREATER_THAN)

        elif operator == OperatorEnum.COUNT_GTE:
            obs_cnt = cls._normalize_count(observed)
            exp_cnt = cls._normalize_count(expected)

            if obs_cnt is None or exp_cnt is None:
                return ComplianceStatus.REVIEW_REQUIRED, ReasonCode.TYPE_CONVERSION_ERROR

            return (ComplianceStatus.PASS, ReasonCode.COUNT_SUFFICIENT) if obs_cnt >= exp_cnt else (ComplianceStatus.FAIL, ReasonCode.COUNT_INSUFFICIENT)

        elif operator in (OperatorEnum.DATE_BEFORE, OperatorEnum.DATE_AFTER):
            obs_obj, obs_is_dt = cls._parse_date_or_datetime(observed)
            exp_obj, exp_is_dt = cls._parse_date_or_datetime(expected)

            if obs_obj is None or exp_obj is None:
                return ComplianceStatus.REVIEW_REQUIRED, ReasonCode.MALFORMED_DATE

            # Date vs Date comparison
            if not obs_is_dt and not exp_is_dt:
                if operator == OperatorEnum.DATE_BEFORE:
                    return (ComplianceStatus.PASS, ReasonCode.BEFORE_DATE) if obs_obj < exp_obj else (ComplianceStatus.FAIL, ReasonCode.ON_OR_AFTER_DATE)
                else:
                    return (ComplianceStatus.PASS, ReasonCode.AFTER_DATE) if obs_obj > exp_obj else (ComplianceStatus.FAIL, ReasonCode.ON_OR_BEFORE_DATE)

            # Datetime vs Datetime comparison
            if obs_is_dt and exp_is_dt:
                if (obs_obj.tzinfo is None) != (exp_obj.tzinfo is None):
                    # Timezone naive vs aware mismatch
                    return ComplianceStatus.REVIEW_REQUIRED, ReasonCode.MALFORMED_DATE
                obs_dt = obs_obj.astimezone(timezone.utc) if obs_obj.tzinfo else obs_obj
                exp_dt = exp_obj.astimezone(timezone.utc) if exp_obj.tzinfo else exp_obj
                if operator == OperatorEnum.DATE_BEFORE:
                    return (ComplianceStatus.PASS, ReasonCode.BEFORE_DATE) if obs_dt < exp_dt else (ComplianceStatus.FAIL, ReasonCode.ON_OR_AFTER_DATE)
                else:
                    return (ComplianceStatus.PASS, ReasonCode.AFTER_DATE) if obs_dt > exp_dt else (ComplianceStatus.FAIL, ReasonCode.ON_OR_BEFORE_DATE)

            # Mixed Date and Datetime comparison
            obs_d = obs_obj.date() if isinstance(obs_obj, datetime) else obs_obj
            exp_d = exp_obj.date() if isinstance(exp_obj, datetime) else exp_obj
            if operator == OperatorEnum.DATE_BEFORE:
                return (ComplianceStatus.PASS, ReasonCode.BEFORE_DATE) if obs_d < exp_d else (ComplianceStatus.FAIL, ReasonCode.ON_OR_AFTER_DATE)
            else:
                return (ComplianceStatus.PASS, ReasonCode.AFTER_DATE) if obs_d > exp_d else (ComplianceStatus.FAIL, ReasonCode.ON_OR_BEFORE_DATE)

        elif operator == OperatorEnum.IN:
            exp_list = expected if isinstance(expected, (list, tuple, set)) else [expected]
            match = any(
                cls._values_equivalent(
                    observed,
                    item,
                    meta_a=obs_meta,
                    rule_unit=rule.unit,
                    case_insensitive=is_ci,
                )
                for item in exp_list
            )
            return (ComplianceStatus.PASS, ReasonCode.VALUE_IN_SET) if match else (ComplianceStatus.FAIL, ReasonCode.VALUE_NOT_IN_SET)

        elif operator == OperatorEnum.NOT_IN:
            exp_list = expected if isinstance(expected, (list, tuple, set)) else [expected]
            match = any(
                cls._values_equivalent(
                    observed,
                    item,
                    meta_a=obs_meta,
                    rule_unit=rule.unit,
                    case_insensitive=is_ci,
                )
                for item in exp_list
            )
            return (ComplianceStatus.PASS, ReasonCode.VALUE_NOT_IN_SET) if not match else (ComplianceStatus.FAIL, ReasonCode.VALUE_IN_SET)

        return ComplianceStatus.UNKNOWN, ReasonCode.UNSUPPORTED_OPERATOR

