from dataclasses import dataclass
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


@dataclass(frozen=True)
class FinancialContext:
    """Typed financial and numeric context for exact, safe comparisons."""

    raw_value: Any
    base_decimal_value: Decimal | None
    currency: str | None
    scale_token: str | None
    scale_multiplier: Decimal
    metric: str | None
    financial_year: str | None
    averaging_period: str | None
    is_base_unit: bool
    is_valid: bool
    error_reason: str | None = None


class ComplianceEngine:
    """Pure deterministic compliance evaluation engine.

    Evaluates tender requirement rules against extracted facts and verification results.
    Prohibited from invoking LLMs, databases, network calls, or side effects.
    Uses exact Decimal arithmetic for numeric and financial comparisons.
    Requires explicit clock context and enforces strict context compatibility.
    """

    ENGINE_VERSION = "2.0.0"
    NORMALIZATION_POLICY_VERSION = "1.0.0"
    OPERATOR_SEMANTICS_VERSION = "1.1.0"
    FINANCIAL_CONTEXT_POLICY_VERSION = "1.0.0"
    TEMPORAL_POLICY_VERSION = "1.0.0"

    SUPPORTED_REPLAY_VERSIONS = {"2.0.0"}

    MAX_INPUT_STR_LENGTH = 100
    MAX_DECIMAL_DIGITS = 38
    MAX_DECIMAL_EXPONENT = 30

    UNIT_SCALE_MAP: dict[str, tuple[Decimal, str]] = {
        "crore": (Decimal("10000000"), "Crore"),
        "crores": (Decimal("10000000"), "Crore"),
        "cr": (Decimal("10000000"), "Crore"),
        "cr.": (Decimal("10000000"), "Crore"),
        "lakh": (Decimal("100000"), "Lakh"),
        "lakhs": (Decimal("100000"), "Lakh"),
        "lac": (Decimal("100000"), "Lakh"),
        "lacs": (Decimal("100000"), "Lakh"),
        "l": (Decimal("100000"), "Lakh"),
        "thousand": (Decimal("1000"), "Thousand"),
        "thousands": (Decimal("1000"), "Thousand"),
        "k": (Decimal("1000"), "Thousand"),
        "million": (Decimal("1000000"), "Million"),
        "millions": (Decimal("1000000"), "Million"),
        "m": (Decimal("1000000"), "Million"),
        "mn": (Decimal("1000000"), "Million"),
        "billion": (Decimal("1000000000"), "Billion"),
        "billions": (Decimal("1000000000"), "Billion"),
        "b": (Decimal("1000000000"), "Billion"),
        "bn": (Decimal("1000000000"), "Billion"),
    }

    @staticmethod
    def compute_rules_hash(rules: list[TenderRequirementRead | dict[str, Any]]) -> str:
        """Computes a deterministic canonical SHA-256 hash of approved rule definitions and policy metadata."""
        canonical_items = []
        for r in rules:
            if hasattr(r, "model_dump"):
                r_dict = r.model_dump(mode="json")
            elif hasattr(r, "__dict__"):
                r_dict = r.__dict__
            else:
                r_dict = dict(r)

            # Extract semantic policy metadata while strictly excluding IDs and mutable timestamps
            raw_meta = r_dict.get("metadata_json") or {}
            policy_meta = {}
            if isinstance(raw_meta, dict):
                for k in (
                    "applicability",
                    "applicability_policy",
                    "applicable",
                    "is_applicable",
                    "currency",
                    "fy",
                    "financial_year",
                    "metric",
                    "averaging_period",
                    "is_base_unit",
                    "applicable_bidder_types",
                    "applicable_categories",
                    "optional_missing_policy",
                    "exemption",
                    "optional_exemption",
                    "exemption_reason",
                    "is_time_dependent",
                    "relative_time",
                ):
                    if k in raw_meta and raw_meta[k] is not None:
                        policy_meta[k] = raw_meta[k]

            canonical_items.append({
                "clause": str(r_dict.get("clause") or "").strip(),
                "field": str(r_dict.get("field") or "").strip(),
                "operator": str(r_dict.get("operator") or "").strip(),
                "expected_value": r_dict.get("expected_value"),
                "unit": str(r_dict.get("unit") or "").strip() if r_dict.get("unit") else None,
                "mandatory": bool(r_dict.get("mandatory", True)),
                "requirement_type": str(r_dict.get("requirement_type") or "").strip(),
                "policy_metadata": policy_meta,
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
    def _is_financial_domain(
        cls,
        rule: TenderRequirementRead | None = None,
        obs_val: Any = None,
        exp_val: Any = None,
        obs_meta: dict[str, Any] | None = None,
        exp_meta: dict[str, Any] | None = None,
    ) -> bool:
        """Determines if an evaluation context belongs to the financial domain requiring FinancialContext."""
        obs_meta = obs_meta or {}
        exp_meta = exp_meta or {}

        # 1. Rule type or field name indicates financial
        if rule:
            req_type = getattr(rule, "requirement_type", None)
            if req_type in (RequirementType.TURNOVER, "TURNOVER", "FINANCIAL"):
                return True
            field_l = str(getattr(rule, "field", "")).lower()
            if any(
                tok in field_l
                for tok in (
                    "turnover",
                    "net_worth",
                    "revenue",
                    "financial",
                    "amount",
                    "capital",
                    "cost",
                    "price",
                    "bid_value",
                    "fee",
                    "deposit",
                    "emd",
                    "pbg",
                    "solvency",
                )
            ):
                return True
            if rule.unit:
                unit_s = str(rule.unit)
                if any(sym in unit_s for sym in ("₹", "$", "€", "£")):
                    return True
                if re.search(r"\b(inr|usd|eur|gbp|rs\.?|rupees?|crores?|lakhs?|cr\.?|lac|million|billion)\b", unit_s.lower()):
                    return True

        # 2. Metadata indicates financial attributes
        for meta in (obs_meta, exp_meta):
            if (
                meta.get("currency")
                or meta.get("fy")
                or meta.get("financial_year")
                or meta.get("averaging_period")
                or meta.get("metric")
                or meta.get("is_base_unit")
            ):
                return True

        # 3. String representations contain financial tokens
        for val in (obs_val, exp_val):
            if isinstance(val, str):
                s_l = val.lower()
                if any(sym in val for sym in ("₹", "$", "€", "£")):
                    return True
                if re.search(r"\b(inr|usd|eur|gbp|rs\.?|rupees?|crores?|lakhs?|cr\.?|lac|million|billion)\b", s_l):
                    return True

        return False

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
            return False

        western_pattern = r"^\d{1,3}(,\d{3})*$"
        indian_pattern = r"^\d{1,2}(,\d{2})*,\d{3}$"

        if re.match(western_pattern, int_part) or re.match(indian_pattern, int_part):
            return True
        return False

    @classmethod
    def _normalize_number(cls, val: Any) -> Decimal | None:
        """Normalizes pure non-financial numeric representations to exact Decimal.

        Rejects NaN, Infinity, boolean types, corrupt commas, and malformed strings.
        Enforces precision and input length bounds.
        """
        if val is None or isinstance(val, bool):
            return None
        if isinstance(val, Decimal):
            if val.is_nan() or val.is_infinite():
                return None
            if abs(val.as_tuple().exponent) > cls.MAX_DECIMAL_EXPONENT:
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
            if not s or len(s) > cls.MAX_INPUT_STR_LENGTH:
                return None
            s_lower = s.lower()
            if s_lower in ("nan", "inf", "-inf", "+inf", "infinity", "-infinity", "+infinity"):
                return None

            if not cls._validate_comma_formatting(s):
                return None

            s_clean = s.replace(",", "").strip()
            try:
                dec = Decimal(s_clean)
                if dec.is_nan() or dec.is_infinite():
                    return None
                if abs(dec.as_tuple().exponent) > cls.MAX_DECIMAL_EXPONENT:
                    return None
                if len(dec.as_tuple().digits) > cls.MAX_DECIMAL_DIGITS:
                    return None
                return dec
            except (InvalidOperation, TypeError, ValueError):
                return None

        return None

    @classmethod
    def _parse_financial_context(
        cls, val: Any, meta: dict[str, Any] | None = None, default_unit: str | None = None
    ) -> FinancialContext:
        """Parses a typed FinancialContext from a raw value and its metadata.

        Derives scale from explicit representation only. Never blindly applies rule default_unit to inputs.
        Recognizes already-normalized base-unit values and avoids double scaling.
        """
        meta = meta or {}
        if val is None:
            return FinancialContext(
                raw_value=val,
                base_decimal_value=None,
                currency=meta.get("currency"),
                scale_token=None,
                scale_multiplier=Decimal("1"),
                metric=meta.get("metric"),
                financial_year=meta.get("financial_year") or meta.get("fy"),
                averaging_period=meta.get("averaging_period") or meta.get("period"),
                is_base_unit=False,
                is_valid=False,
                error_reason=ReasonCode.OBSERVED_VALUE_NULL,
            )

        if isinstance(val, bool):
            return FinancialContext(
                raw_value=val,
                base_decimal_value=None,
                currency=meta.get("currency"),
                scale_token=None,
                scale_multiplier=Decimal("1"),
                metric=meta.get("metric"),
                financial_year=meta.get("financial_year") or meta.get("fy"),
                averaging_period=meta.get("averaging_period") or meta.get("period"),
                is_base_unit=False,
                is_valid=False,
                error_reason=ReasonCode.TYPE_CONVERSION_ERROR,
            )

        val_str = str(val).strip()
        if len(val_str) > cls.MAX_INPUT_STR_LENGTH:
            return FinancialContext(
                raw_value=val,
                base_decimal_value=None,
                currency=meta.get("currency"),
                scale_token=None,
                scale_multiplier=Decimal("1"),
                metric=meta.get("metric"),
                financial_year=meta.get("financial_year") or meta.get("fy"),
                averaging_period=meta.get("averaging_period") or meta.get("period"),
                is_base_unit=False,
                is_valid=False,
                error_reason=ReasonCode.MALFORMED_NUMBER,
            )

        val_lower = val_str.lower()
        if val_lower in ("nan", "inf", "-inf", "+inf", "infinity", "-infinity", "+infinity"):
            return FinancialContext(
                raw_value=val,
                base_decimal_value=None,
                currency=meta.get("currency"),
                scale_token=None,
                scale_multiplier=Decimal("1"),
                metric=meta.get("metric"),
                financial_year=meta.get("financial_year") or meta.get("fy"),
                averaging_period=meta.get("averaging_period") or meta.get("period"),
                is_base_unit=False,
                is_valid=False,
                error_reason=ReasonCode.MALFORMED_NUMBER,
            )

        # 1. Currency resolution
        meta_curr = meta.get("currency")
        input_meta_unit = meta.get("unit") or default_unit
        if not meta_curr and input_meta_unit and isinstance(input_meta_unit, str):
            unit_u = input_meta_unit.upper()
            if "INR" in unit_u or "RS" in unit_u or "₹" in input_meta_unit:
                meta_curr = "INR"
            elif "USD" in unit_u or "$" in input_meta_unit:
                meta_curr = "USD"
            elif "EUR" in unit_u or "€" in input_meta_unit:
                meta_curr = "EUR"
            elif "GBP" in unit_u or "£" in input_meta_unit:
                meta_curr = "GBP"

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
        if meta_curr and text_curr and meta_curr.upper() != text_curr.upper():
            return FinancialContext(
                raw_value=val,
                base_decimal_value=None,
                currency=meta_curr,
                scale_token=None,
                scale_multiplier=Decimal("1"),
                metric=meta.get("metric"),
                financial_year=meta.get("financial_year") or meta.get("fy"),
                averaging_period=meta.get("averaging_period") or meta.get("period"),
                is_base_unit=False,
                is_valid=False,
                error_reason=ReasonCode.CURRENCY_MISMATCH,
            )

        resolved_currency = (meta_curr or text_curr or "").upper() or None

        # 2. Base-unit representation check
        is_base_unit = False
        has_explicit_base_flag = False
        for flag_name in ("is_base_unit", "normalized", "is_normalized"):
            if flag_name in meta and meta[flag_name] is not None:
                parsed_flag = cls._parse_boolean_strict(meta[flag_name])
                if parsed_flag is None:
                    return FinancialContext(
                        raw_value=val,
                        base_decimal_value=None,
                        currency=resolved_currency,
                        scale_token=None,
                        scale_multiplier=Decimal("1"),
                        metric=meta.get("metric"),
                        financial_year=meta.get("financial_year") or meta.get("fy"),
                        averaging_period=meta.get("averaging_period") or meta.get("period"),
                        is_base_unit=False,
                        is_valid=False,
                        error_reason=ReasonCode.MALFORMED_NUMBER,
                    )
                has_explicit_base_flag = True
                if parsed_flag is True:
                    is_base_unit = True

        if not has_explicit_base_flag and meta.get("unit"):
            unit_str_val = str(meta.get("unit")).strip().upper()
            if unit_str_val in ("INR", "USD", "EUR", "GBP", "BASE", "UNITS"):
                is_base_unit = True

        # 3. Scale resolution
        meta_scale = Decimal("1")
        canonical_meta_unit = None
        input_meta_unit = meta.get("unit") or default_unit
        if input_meta_unit and isinstance(input_meta_unit, str) and not is_base_unit:
            unit_clean = input_meta_unit.lower().strip()
            for c_token in ("inr", "usd", "eur", "gbp", "₹", "$", "€", "£"):
                unit_clean = unit_clean.replace(c_token, "").strip()
            if unit_clean in cls.UNIT_SCALE_MAP:
                meta_scale, canonical_meta_unit = cls.UNIT_SCALE_MAP[unit_clean]
            elif unit_clean:
                # Unsupported unit scale in metadata
                return FinancialContext(
                    raw_value=val,
                    base_decimal_value=None,
                    currency=resolved_currency,
                    scale_token=input_meta_unit.strip(),
                    scale_multiplier=Decimal("1"),
                    metric=meta.get("metric"),
                    financial_year=meta.get("financial_year") or meta.get("fy"),
                    averaging_period=meta.get("averaging_period") or meta.get("period"),
                    is_base_unit=is_base_unit,
                    is_valid=False,
                    error_reason=ReasonCode.UNIT_MISMATCH,
                )

        text_scale = Decimal("1")
        canonical_text_unit = None
        for scale_token, (scale_factor, canon_name) in cls.UNIT_SCALE_MAP.items():
            pattern = r"\b" + re.escape(scale_token) + r"\b"
            if re.search(pattern, val_lower):
                text_scale = scale_factor
                canonical_text_unit = canon_name
                break

        # Check scale contradiction between metadata and text
        if (
            canonical_meta_unit
            and canonical_text_unit
            and canonical_meta_unit.lower() != canonical_text_unit.lower()
        ):
            return FinancialContext(
                raw_value=val,
                base_decimal_value=None,
                currency=resolved_currency,
                scale_token=canonical_meta_unit,
                scale_multiplier=Decimal("1"),
                metric=meta.get("metric"),
                financial_year=meta.get("financial_year") or meta.get("fy"),
                averaging_period=meta.get("averaging_period") or meta.get("period"),
                is_base_unit=is_base_unit,
                is_valid=False,
                error_reason=ReasonCode.UNIT_MISMATCH,
            )

        resolved_scale_token = canonical_text_unit or canonical_meta_unit
        if is_base_unit:
            scale_multiplier = Decimal("1")
        else:
            scale_multiplier = text_scale if canonical_text_unit else (meta_scale if canonical_meta_unit else Decimal("1"))

        # 4. Numeric base value extraction
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
            return FinancialContext(
                raw_value=val,
                base_decimal_value=None,
                currency=resolved_currency,
                scale_token=resolved_scale_token,
                scale_multiplier=scale_multiplier,
                metric=meta.get("metric"),
                financial_year=meta.get("financial_year") or meta.get("fy"),
                averaging_period=meta.get("averaging_period") or meta.get("period"),
                is_base_unit=is_base_unit,
                is_valid=False,
                error_reason=ReasonCode.MALFORMED_NUMBER,
            )

        clean_s = clean_s.replace(",", "").strip()
        if not clean_s:
            return FinancialContext(
                raw_value=val,
                base_decimal_value=None,
                currency=resolved_currency,
                scale_token=resolved_scale_token,
                scale_multiplier=scale_multiplier,
                metric=meta.get("metric"),
                financial_year=meta.get("financial_year") or meta.get("fy"),
                averaging_period=meta.get("averaging_period") or meta.get("period"),
                is_base_unit=is_base_unit,
                is_valid=False,
                error_reason=ReasonCode.MALFORMED_NUMBER,
            )

        try:
            base_dec = Decimal(clean_s)
            if base_dec.is_nan() or base_dec.is_infinite():
                return FinancialContext(
                    raw_value=val,
                    base_decimal_value=None,
                    currency=resolved_currency,
                    scale_token=resolved_scale_token,
                    scale_multiplier=scale_multiplier,
                    metric=meta.get("metric"),
                    financial_year=meta.get("financial_year") or meta.get("fy"),
                    averaging_period=meta.get("averaging_period") or meta.get("period"),
                    is_base_unit=is_base_unit,
                    is_valid=False,
                    error_reason=ReasonCode.MALFORMED_NUMBER,
                )
            if abs(base_dec.as_tuple().exponent) > cls.MAX_DECIMAL_EXPONENT or len(base_dec.as_tuple().digits) > cls.MAX_DECIMAL_DIGITS:
                return FinancialContext(
                    raw_value=val,
                    base_decimal_value=None,
                    currency=resolved_currency,
                    scale_token=resolved_scale_token,
                    scale_multiplier=scale_multiplier,
                    metric=meta.get("metric"),
                    financial_year=meta.get("financial_year") or meta.get("fy"),
                    averaging_period=meta.get("averaging_period") or meta.get("period"),
                    is_base_unit=is_base_unit,
                    is_valid=False,
                    error_reason=ReasonCode.MALFORMED_NUMBER,
                )

            scaled_base_value = base_dec * scale_multiplier
            return FinancialContext(
                raw_value=val,
                base_decimal_value=scaled_base_value,
                currency=resolved_currency,
                scale_token=resolved_scale_token,
                scale_multiplier=scale_multiplier,
                metric=meta.get("metric"),
                financial_year=meta.get("financial_year") or meta.get("fy"),
                averaging_period=meta.get("averaging_period") or meta.get("period"),
                is_base_unit=is_base_unit,
                is_valid=True,
                error_reason=None,
            )
        except (InvalidOperation, TypeError, ValueError):
            return FinancialContext(
                raw_value=val,
                base_decimal_value=None,
                currency=resolved_currency,
                scale_token=resolved_scale_token,
                scale_multiplier=scale_multiplier,
                metric=meta.get("metric"),
                financial_year=meta.get("financial_year") or meta.get("fy"),
                averaging_period=meta.get("averaging_period") or meta.get("period"),
                is_base_unit=is_base_unit,
                is_valid=False,
                error_reason=ReasonCode.MALFORMED_NUMBER,
            )

    @classmethod
    def _parse_currency_and_scale(
        cls, val: Any, meta: dict[str, Any] | None = None, rule_unit: str | None = None
    ) -> tuple[Decimal | None, str | None, str | None, bool]:
        """Legacy helper returning (scaled_decimal_value, currency, canonical_unit, is_valid)."""
        ctx = cls._parse_financial_context(val, meta, default_unit=rule_unit)
        if not ctx.is_valid:
            return None, ctx.currency, ctx.scale_token, False
        is_explicit = bool(ctx.currency or ctx.scale_token)
        return ctx.base_decimal_value, ctx.currency, ctx.scale_token, is_explicit

    @classmethod
    def _check_financial_compatibility(
        cls,
        ctx_a: FinancialContext,
        ctx_b: FinancialContext,
        *,
        require_financial_context: bool = True,
    ) -> tuple[bool, str | None]:
        """Checks whether two financial contexts are strictly compatible before comparison.

        Rejects one-sided missing metadata and does not invent currency, FY, period, or metric conversions.
        """
        if not ctx_a.is_valid:
            return False, ctx_a.error_reason or ReasonCode.MALFORMED_NUMBER
        if not ctx_b.is_valid:
            return False, ctx_b.error_reason or ReasonCode.MALFORMED_NUMBER

        # 1. Currency compatibility
        if ctx_a.currency and ctx_b.currency:
            if ctx_a.currency.upper() != ctx_b.currency.upper():
                return False, ReasonCode.CURRENCY_MISMATCH
        elif require_financial_context:
            if (ctx_a.currency is None) != (ctx_b.currency is None):
                return False, ReasonCode.MISSING_FINANCIAL_CONTEXT
            if ctx_a.currency is None and ctx_b.currency is None:
                return False, ReasonCode.MISSING_FINANCIAL_CONTEXT

        # 2. Financial Year compatibility
        if ctx_a.financial_year and ctx_b.financial_year:
            if str(ctx_a.financial_year).strip().lower() != str(ctx_b.financial_year).strip().lower():
                return False, ReasonCode.FINANCIAL_CONTEXT_MISMATCH
        elif require_financial_context and (ctx_a.financial_year is not None or ctx_b.financial_year is not None):
            if (ctx_a.financial_year is None) != (ctx_b.financial_year is None):
                return False, ReasonCode.FINANCIAL_CONTEXT_MISMATCH

        # 3. Averaging period compatibility
        if ctx_a.averaging_period and ctx_b.averaging_period:
            if str(ctx_a.averaging_period).strip().lower() != str(ctx_b.averaging_period).strip().lower():
                return False, ReasonCode.FINANCIAL_CONTEXT_MISMATCH
        elif require_financial_context and (ctx_a.averaging_period is not None or ctx_b.averaging_period is not None):
            if (ctx_a.averaging_period is None) != (ctx_b.averaging_period is None):
                return False, ReasonCode.FINANCIAL_CONTEXT_MISMATCH

        # 4. Metric compatibility
        if ctx_a.metric and ctx_b.metric:
            if str(ctx_a.metric).strip().lower() != str(ctx_b.metric).strip().lower():
                return False, ReasonCode.FINANCIAL_CONTEXT_MISMATCH
        elif require_financial_context and (ctx_a.metric is not None or ctx_b.metric is not None):
            if (ctx_a.metric is None) != (ctx_b.metric is None):
                return False, ReasonCode.FINANCIAL_CONTEXT_MISMATCH

        return True, None

    @classmethod
    def _parse_boolean_strict(cls, val: Any) -> bool | None:
        """Strictly parses canonical booleans without treating arbitrary non-empty strings as True."""
        if val is None:
            return None
        if isinstance(val, bool):
            return val
        if isinstance(val, (int, Decimal)):
            if val == 1:
                return True
            if val == 0:
                return False
            return None
        if isinstance(val, str):
            s = val.strip().lower()
            if s in ("true", "1", "yes"):
                return True
            if s in ("false", "0", "no"):
                return False
            return None
        return None

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

    @classmethod
    def _normalize_count(cls, val: Any) -> int | None:
        """Normalizes count to a non-negative bounded integer.

        Uses exact Decimal/integer parsing. Rejects floats, negative numbers, fractional values, and booleans.
        """
        if val is None or isinstance(val, bool):
            return None
        if isinstance(val, (list, tuple, set)):
            return len(val)
        if isinstance(val, int):
            return val if (0 <= val <= 1_000_000_000) else None
        if isinstance(val, Decimal):
            if val.is_nan() or val.is_infinite():
                return None
            if 0 <= val <= 1_000_000_000 and val % 1 == 0:
                return int(val)
            return None
        if isinstance(val, float):
            return None
        if isinstance(val, str):
            s = val.strip()
            if not s or len(s) > cls.MAX_INPUT_STR_LENGTH:
                return None
            try:
                dec = Decimal(s)
                if dec.is_nan() or dec.is_infinite():
                    return None
                if 0 <= dec <= 1_000_000_000 and dec % 1 == 0:
                    return int(dec)
                return None
            except (InvalidOperation, ValueError, TypeError):
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
    def _parse_date_or_datetime(cls, val: Any) -> tuple[datetime | date | None, str | None]:
        """Parses date or datetime with strict temporal typing.

        Returns: (parsed_obj, temporal_type)
        where temporal_type is 'DATE_ONLY', 'AWARE_DATETIME', 'NAIVE_DATETIME', or None.
        """
        if val is None:
            return None, None
        if isinstance(val, datetime):
            return val, "AWARE_DATETIME" if val.tzinfo is not None else "NAIVE_DATETIME"
        if isinstance(val, date) and not isinstance(val, datetime):
            return val, "DATE_ONLY"
        if isinstance(val, str):
            s = val.strip()
            if not s or len(s) > cls.MAX_INPUT_STR_LENGTH:
                return None, None

            # Try ISO 8601 datetime with or without timezone
            try:
                if "T" in s or (" " in s and ":" in s):
                    if s.endswith("Z"):
                        dt = datetime.fromisoformat(s[:-1] + "+00:00")
                        return dt, "AWARE_DATETIME"
                    dt = datetime.fromisoformat(s)
                    return dt, "AWARE_DATETIME" if dt.tzinfo is not None else "NAIVE_DATETIME"
                else:
                    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
                        d = date.fromisoformat(s)
                        return d, "DATE_ONLY"
            except (ValueError, TypeError):
                pass

            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
                try:
                    dt = datetime.strptime(s, fmt)
                    return dt.date(), "DATE_ONLY"
                except ValueError:
                    continue

        return None, None

    @classmethod
    def _normalize_date(cls, val: Any) -> datetime | None:
        """Legacy helper normalizing to timezone-aware datetime when applicable."""
        obj, t_type = cls._parse_date_or_datetime(val)
        if obj is None:
            return None
        if t_type == "AWARE_DATETIME" and isinstance(obj, datetime):
            return obj
        if t_type == "NAIVE_DATETIME" and isinstance(obj, datetime):
            return obj.replace(tzinfo=timezone.utc)
        if t_type == "DATE_ONLY" and isinstance(obj, date):
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
        rule: TenderRequirementRead | None = None,
    ) -> bool:
        """Determines semantic equality across numeric, boolean, date, and string domains with strict context."""
        if a is None or b is None:
            return a is b
        if a == b and not meta_a and not meta_b and not unit and not rule_unit and not rule:
            return True

        meta_a = meta_a or {}
        meta_b = meta_b or {}

        # 1. Financial check
        if cls._is_financial_domain(rule=rule, obs_val=a, exp_val=b, obs_meta=meta_a, exp_meta=meta_b):
            ctx_a = cls._parse_financial_context(a, meta_a, default_unit=None)
            ctx_b = cls._parse_financial_context(b, meta_b, default_unit=rule_unit or unit)

            if not ctx_a.is_valid or not ctx_b.is_valid:
                return False

            is_compat, _ = cls._check_financial_compatibility(ctx_a, ctx_b, require_financial_context=True)
            if not is_compat:
                return False

            return ctx_a.base_decimal_value == ctx_b.base_decimal_value

        # 2. Pure Non-Financial Numeric comparison
        num_a = cls._normalize_number(a)
        num_b = cls._normalize_number(b)
        if num_a is not None and num_b is not None:
            return num_a == num_b

        # 3. Boolean comparison
        bool_a = cls._normalize_bool(a)
        bool_b = cls._normalize_bool(b)
        if bool_a is not None and bool_b is not None:
            return bool_a == bool_b

        # 4. Date / Datetime comparison
        obj_a, t_type_a = cls._parse_date_or_datetime(a)
        obj_b, t_type_b = cls._parse_date_or_datetime(b)
        if obj_a is not None and obj_b is not None:
            if t_type_a == "DATE_ONLY" and t_type_b == "DATE_ONLY":
                return obj_a == obj_b
            if t_type_a == "AWARE_DATETIME" and t_type_b == "AWARE_DATETIME":
                dt_a = obj_a.astimezone(timezone.utc)
                dt_b = obj_b.astimezone(timezone.utc)
                return dt_a == dt_b
            if t_type_a == "NAIVE_DATETIME" and t_type_b == "NAIVE_DATETIME":
                return obj_a == obj_b
            return False

        # 5. String comparison
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
                if k
                in (
                    "unit",
                    "currency",
                    "source",
                    "source_page",
                    "source_text",
                    "confidence",
                    "location_metadata",
                    "fy",
                    "financial_year",
                    "metric",
                    "averaging_period",
                    "is_base_unit",
                )
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
    def _determine_applicability(
        cls,
        rule: TenderRequirementRead,
        context: dict[str, Any] | None = None,
        facts: list[FactRead] | None = None,
        verification_results: list[VerificationResultRead] | None = None,
    ) -> tuple[bool | None, str, list[str]]:
        """Evaluates requirement applicability from approved policy metadata, bidder context, and evidence.

        Returns (is_applicable, reason_code, evidence_ids).
        is_applicable is True (applicable), False (not applicable), or None (unknown).
        """
        context = context or {}
        facts = facts or []
        verification_results = verification_results or []
        rule_meta = getattr(rule, "metadata_json", {}) or {}
        current_bidder_id = context.get("bidder_id")

        # 1. Explicit boolean applicability in metadata (tender-wide rule applicability)
        for bool_key in ("is_applicable", "applicable"):
            if bool_key in rule_meta and rule_meta[bool_key] is not None:
                parsed_bool = cls._parse_boolean_strict(rule_meta[bool_key])
                if parsed_bool is True:
                    return True, "APPLICABLE", []
                elif parsed_bool is False:
                    return False, ReasonCode.NOT_APPLICABLE_EXPLICIT, []
                else:
                    return None, ReasonCode.INVALID_APPLICABILITY_POLICY, []

        # 2. String policy in metadata (tender-wide rule applicability)
        if "applicability" in rule_meta and rule_meta["applicability"] is not None:
            app_str = str(rule_meta["applicability"]).strip().upper()
            if app_str in ("NOT_APPLICABLE", "INAPPLICABLE"):
                return False, ReasonCode.NOT_APPLICABLE_EXPLICIT, []
            elif app_str in ("APPLICABLE", "MANDATORY"):
                return True, "APPLICABLE", []
            elif app_str == "EXEMPT":
                # Exemption policy declared on rule, evaluate bidder qualification below
                pass
            else:
                return None, ReasonCode.INVALID_APPLICABILITY_POLICY, []

        # 3. Categorical bidder-type applicability
        applicable_types = rule_meta.get("applicable_bidder_types") or rule_meta.get("applicable_categories")
        if applicable_types and isinstance(applicable_types, (list, tuple, set)):
            bidder_type = context.get("bidder_type") or context.get("bidder_category")
            if bidder_type:
                if str(bidder_type).strip().lower() in [str(t).strip().lower() for t in applicable_types]:
                    return True, "APPLICABLE", []
                else:
                    return False, ReasonCode.NOT_APPLICABLE_EXPLICIT, []
            else:
                # Do not treat missing bidder category as a categorical mismatch
                return None, "UNKNOWN_APPLICABILITY", []

        # 4. Evidence-backed & Requirement-scoped Exemption Policy
        has_exemption_policy = False
        if "exemption" in rule_meta and rule_meta["exemption"] is not None:
            ex_val = rule_meta["exemption"]
            parsed_ex = cls._parse_boolean_strict(ex_val)
            if parsed_ex is True:
                has_exemption_policy = True
            elif parsed_ex is False:
                has_exemption_policy = False
            elif isinstance(ex_val, str) and ex_val.strip().upper() in (
                "APPROVED", "VALID", "GRANTED", "YES", "MSME", "STARTUP", "EXEMPTION_ALLOWED", "EXEMPT"
            ):
                has_exemption_policy = True
            else:
                return None, ReasonCode.INVALID_APPLICABILITY_POLICY, []

        if "optional_exemption" in rule_meta and rule_meta["optional_exemption"] is not None:
            opt_ex = cls._parse_boolean_strict(rule_meta["optional_exemption"])
            if opt_ex is True and not rule.mandatory:
                has_exemption_policy = True
            elif opt_ex is None:
                return None, ReasonCode.INVALID_APPLICABILITY_POLICY, []

        if has_exemption_policy:
            # Check requirement-scoped trusted determinations in context and facts
            scoped_ex_record = None

            # A) Mapping in context under "exemptions" or "approved_exemptions"
            exemptions_map = context.get("exemptions") or context.get("approved_exemptions")
            if isinstance(exemptions_map, dict):
                if rule.id in exemptions_map:
                    scoped_ex_record = exemptions_map[rule.id]
                elif rule.field in exemptions_map:
                    scoped_ex_record = exemptions_map[rule.field]
            elif isinstance(exemptions_map, (list, tuple)):
                for ex_item in exemptions_map:
                    if isinstance(ex_item, dict):
                        ex_req_id = ex_item.get("requirement_id") or ex_item.get("rule_id")
                        if ex_req_id == rule.id:
                            scoped_ex_record = ex_item
                            break

            # B) Explicit requirement-scoped key in context
            if not scoped_ex_record and f"exemption_{rule.id}" in context:
                scoped_ex_record = context[f"exemption_{rule.id}"]

            # Validate requirement-scoped record
            if scoped_ex_record is not None:
                if isinstance(scoped_ex_record, dict):
                    rec_bidder = scoped_ex_record.get("bidder_id")
                    if rec_bidder and current_bidder_id and str(rec_bidder) != str(current_bidder_id):
                        # Unrelated bidder's exemption cannot be reused
                        return None, ReasonCode.UNVERIFIED_EXEMPTION_ELIGIBILITY, []

                    is_approved = (
                        scoped_ex_record.get("is_approved") is True
                        or scoped_ex_record.get("approved") is True
                        or str(scoped_ex_record.get("status", "")).upper() in ("APPROVED", "VALID")
                    )
                    if is_approved:
                        ev_refs = scoped_ex_record.get("evidence_ids") or []
                        if isinstance(ev_refs, str):
                            ev_refs = [ev_refs]
                        elif not isinstance(ev_refs, list):
                            ev_refs = list(ev_refs)
                        if not ev_refs and scoped_ex_record.get("decision_id"):
                            ev_refs = [str(scoped_ex_record["decision_id"])]
                        elif not ev_refs and scoped_ex_record.get("document_id"):
                            ev_refs = [str(scoped_ex_record["document_id"])]
                        elif not ev_refs and scoped_ex_record.get("id"):
                            ev_refs = [str(scoped_ex_record["id"])]
                        return False, ReasonCode.NOT_APPLICABLE_EXEMPTION, ev_refs
                    elif scoped_ex_record.get("is_approved") is False or scoped_ex_record.get("approved") is False:
                        return True, "APPLICABLE", []
                elif scoped_ex_record is True:
                    return False, ReasonCode.NOT_APPLICABLE_EXEMPTION, []
                elif scoped_ex_record is False:
                    return True, "APPLICABLE", []

            # C) Check requirement-scoped exemption facts and verifications
            ex_facts = [
                f for f in facts
                if f.field in (f"exemption.{rule.id}", f"exemption.{rule.field}", "exemption")
                and (not current_bidder_id or getattr(f, "bidder_id", None) == current_bidder_id)
            ]
            if ex_facts:
                truthy_facts = [f for f in ex_facts if cls._normalize_bool(f.value) is True]
                falsy_facts = [f for f in ex_facts if cls._normalize_bool(f.value) is False]
                if truthy_facts and falsy_facts:
                    # Conflicting exemption evidence fails closed
                    all_ids = [f.id for f in ex_facts]
                    return None, ReasonCode.UNVERIFIED_EXEMPTION_ELIGIBILITY, all_ids
                elif truthy_facts:
                    return False, ReasonCode.NOT_APPLICABLE_EXEMPTION, [f.id for f in truthy_facts]
                elif falsy_facts:
                    return True, "APPLICABLE", [f.id for f in falsy_facts]

            # D) Check category-based exemption from verified facts or bidder category
            ex_category = str(rule_meta.get("exemption", "")).strip().upper()
            if ex_category in ("MSME", "STARTUP"):
                cat_facts = [
                    f for f in facts
                    if f.field in ("bidder.category", "bidder.type", f"bidder.{ex_category.lower()}_status")
                    and (not current_bidder_id or getattr(f, "bidder_id", None) == current_bidder_id)
                ]
                if cat_facts:
                    matching_cat_facts = [
                        f for f in cat_facts
                        if str(f.value).strip().upper() == ex_category or cls._normalize_bool(f.value) is True
                    ]
                    non_matching_cat_facts = [
                        f for f in cat_facts
                        if str(f.value).strip().upper() != ex_category and cls._normalize_bool(f.value) is False
                    ]
                    if matching_cat_facts and non_matching_cat_facts:
                        all_ids = [f.id for f in cat_facts]
                        return None, ReasonCode.UNVERIFIED_EXEMPTION_ELIGIBILITY, all_ids
                    elif matching_cat_facts:
                        return False, ReasonCode.NOT_APPLICABLE_EXEMPTION, [f.id for f in matching_cat_facts]

                bidder_cat = context.get("bidder_category") or context.get("bidder_type")
                if bidder_cat and str(bidder_cat).strip().upper() == ex_category:
                    return False, ReasonCode.NOT_APPLICABLE_EXEMPTION, []

            # Global unsupported boolean alone without requirement scope or evidence is rejected
            return None, ReasonCode.UNVERIFIED_EXEMPTION_ELIGIBILITY, []

        return None, "UNKNOWN_APPLICABILITY", []

    @classmethod
    def _is_time_dependent_rule(cls, rule: TenderRequirementRead) -> bool:
        """Determines if a rule depends on the current evaluation clock."""
        rule_meta = getattr(rule, "metadata_json", {}) or {}
        if rule_meta.get("is_time_dependent") or rule_meta.get("relative_time"):
            return True
        exp_str = str(rule.expected_value).strip().lower()
        if exp_str in ("now", "today", "current_date", "current_time"):
            return True
        return False

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
            raise ValueError(
                f"ComplianceEngine cannot evaluate unapproved requirement candidate '{getattr(rule, 'id', 'UNKNOWN')}'."
            )

        # Explicit recorded evaluation timestamp
        eval_ts = evaluation_timestamp or context.get("evaluation_timestamp") or context.get("evaluated_at")
        if eval_ts is None and cls._is_time_dependent_rule(rule):
            return RuleEvaluationRead(
                id=str(uuid.uuid4()),
                bidder_id=context.get("bidder_id", "UNKNOWN_BIDDER"),
                requirement_id=rule.id,
                status=ComplianceStatus.UNKNOWN,
                reason_code=ReasonCode.MISSING_EVALUATION_CLOCK,
                observed_value=None,
                expected_value=rule.expected_value,
                evidence_ids=[],
                rule_version=f"{cls.ENGINE_VERSION}",
                evaluated_at=None,
            )

        eval_id = str(uuid.uuid4())
        is_ci = cls._is_status_or_enum_field(rule.field, rule.requirement_type)

        # Check explicit applicability policy and requirement-scoped exemptions
        is_applicable, app_reason, app_evidence_ids = cls._determine_applicability(
            rule, context, facts=facts, verification_results=verification_results
        )
        if is_applicable is False:
            return RuleEvaluationRead(
                id=eval_id,
                bidder_id=context.get("bidder_id", "UNKNOWN_BIDDER"),
                requirement_id=rule.id,
                status=ComplianceStatus.NOT_APPLICABLE,
                reason_code=app_reason or ReasonCode.NOT_APPLICABLE_EXPLICIT,
                observed_value=None,
                expected_value=rule.expected_value,
                evidence_ids=app_evidence_ids,
                rule_version=f"{cls.ENGINE_VERSION}",
                evaluated_at=eval_ts,
            )
        elif app_reason in (ReasonCode.INVALID_APPLICABILITY_POLICY, ReasonCode.UNVERIFIED_EXEMPTION_ELIGIBILITY):
            return RuleEvaluationRead(
                id=eval_id,
                bidder_id=context.get("bidder_id", "UNKNOWN_BIDDER"),
                requirement_id=rule.id,
                status=ComplianceStatus.UNKNOWN,
                reason_code=app_reason,
                observed_value=None,
                expected_value=rule.expected_value,
                evidence_ids=app_evidence_ids,
                rule_version=f"{cls.ENGINE_VERSION}",
                evaluated_at=eval_ts,
            )

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
                rule_version=f"{cls.ENGINE_VERSION}",
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
                if not cls._values_equivalent(first_v, v.verified_value, unit=rule.unit, case_insensitive=is_ci, rule=rule):
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
                rule_version=f"{cls.ENGINE_VERSION}",
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
                rule_version=f"{cls.ENGINE_VERSION}",
                evaluated_at=eval_ts,
            )

        # PRECEDENCE 4: Conflicting facts across documents (context + value compatibility)
        facts_with_val = [f for f in matching_facts if f.value is not None]
        has_fact_conflict = False
        if len(facts_with_val) > 1:
            first_f = facts_with_val[0]
            first_meta = getattr(first_f, "metadata_json", None) or {}
            for f in facts_with_val[1:]:
                f_meta = getattr(f, "metadata_json", None) or {}
                if not cls._values_equivalent(
                    first_f.value,
                    f.value,
                    meta_a=first_meta,
                    meta_b=f_meta,
                    unit=rule.unit,
                    case_insensitive=is_ci,
                    rule=rule,
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
                rule_version=f"{cls.ENGINE_VERSION}",
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
                    rule_version=f"{cls.ENGINE_VERSION}",
                    evaluated_at=eval_ts,
                )

            fact_meta = getattr(facts_with_val[0], "metadata_json", {}) or {}
            if not cls._values_equivalent(
                claimed_val,
                resolved_verified_val,
                meta_a=fact_meta,
                meta_b=ver_meta,
                unit=rule.unit,
                case_insensitive=is_ci,
                rule=rule,
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
                    rule_version=f"{cls.ENGINE_VERSION}",
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
                    rule_version=f"{cls.ENGINE_VERSION}",
                    evaluated_at=eval_ts,
                )
            else:
                rule_meta = getattr(rule, "metadata_json", {}) or {}
                if rule.mandatory:
                    status = ComplianceStatus.UNKNOWN
                    reason = ReasonCode.MISSING_EVIDENCE
                else:
                    if rule_meta.get("optional_missing_policy") == "NOT_APPLICABLE" or rule_meta.get("optional_exemption") is True:
                        status = ComplianceStatus.NOT_APPLICABLE
                        reason = ReasonCode.NOT_APPLICABLE_OPTIONAL
                    else:
                        status = ComplianceStatus.UNKNOWN if is_applicable is not False else ComplianceStatus.NOT_APPLICABLE
                        reason = ReasonCode.MISSING_EVIDENCE if status == ComplianceStatus.UNKNOWN else ReasonCode.NOT_APPLICABLE_OPTIONAL
                return RuleEvaluationRead(
                    id=eval_id,
                    bidder_id=context.get("bidder_id", "UNKNOWN_BIDDER"),
                    requirement_id=rule.id,
                    status=status,
                    reason_code=reason,
                    observed_value=None,
                    expected_value=True,
                    evidence_ids=[],
                    rule_version=f"{cls.ENGINE_VERSION}",
                    evaluated_at=eval_ts,
                )

        if rule.operator == OperatorEnum.NOT_EXISTS:
            if not has_usable_evidence:
                rule_meta = getattr(rule, "metadata_json", {}) or {}
                if rule.mandatory:
                    status = ComplianceStatus.UNKNOWN
                    reason = ReasonCode.MISSING_EVIDENCE
                else:
                    if rule_meta.get("optional_missing_policy") == "NOT_APPLICABLE" or rule_meta.get("optional_exemption") is True:
                        status = ComplianceStatus.NOT_APPLICABLE
                        reason = ReasonCode.NOT_APPLICABLE_OPTIONAL
                    else:
                        status = ComplianceStatus.UNKNOWN if is_applicable is not False else ComplianceStatus.NOT_APPLICABLE
                        reason = ReasonCode.MISSING_EVIDENCE if status == ComplianceStatus.UNKNOWN else ReasonCode.NOT_APPLICABLE_OPTIONAL
                return RuleEvaluationRead(
                    id=eval_id,
                    bidder_id=context.get("bidder_id", "UNKNOWN_BIDDER"),
                    requirement_id=rule.id,
                    status=status,
                    reason_code=reason,
                    observed_value=None,
                    expected_value=False,
                    evidence_ids=[],
                    rule_version=f"{cls.ENGINE_VERSION}",
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
                    rule_version=f"{cls.ENGINE_VERSION}",
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
                    rule_version=f"{cls.ENGINE_VERSION}",
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
                    rule_version=f"{cls.ENGINE_VERSION}",
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
                rule_version=f"{cls.ENGINE_VERSION}",
                evaluated_at=eval_ts,
            )

        # PRECEDENCE 7: Handle missing evidence for standard operators
        if not has_usable_evidence:
            rule_meta = getattr(rule, "metadata_json", {}) or {}
            if rule.mandatory:
                status = ComplianceStatus.UNKNOWN
                reason = ReasonCode.MISSING_EVIDENCE
            else:
                if rule_meta.get("optional_missing_policy") == "NOT_APPLICABLE" or rule_meta.get("optional_exemption") is True:
                    status = ComplianceStatus.NOT_APPLICABLE
                    reason = ReasonCode.NOT_APPLICABLE_OPTIONAL
                else:
                    status = ComplianceStatus.UNKNOWN if is_applicable is not False else ComplianceStatus.NOT_APPLICABLE
                    reason = ReasonCode.MISSING_EVIDENCE if status == ComplianceStatus.UNKNOWN else ReasonCode.NOT_APPLICABLE_OPTIONAL
            return RuleEvaluationRead(
                id=eval_id,
                bidder_id=context.get("bidder_id", "UNKNOWN_BIDDER"),
                requirement_id=rule.id,
                status=status,
                reason_code=reason,
                observed_value=None,
                expected_value=rule.expected_value,
                evidence_ids=[],
                rule_version=f"{cls.ENGINE_VERSION}",
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
                rule_version=f"{cls.ENGINE_VERSION}",
                evaluated_at=eval_ts,
            )

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
            rule_version=f"{cls.ENGINE_VERSION}",
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
        is_financial = cls._is_financial_domain(
            rule=rule, obs_val=observed, exp_val=expected, obs_meta=obs_meta, exp_meta=rule_meta
        )

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

            # 2. Check if financial domain comparison
            if is_financial:
                ctx_exp = cls._parse_financial_context(expected, rule_meta, default_unit=rule.unit)
                ctx_obs = cls._parse_financial_context(observed, obs_meta, default_unit=None)

                if not ctx_obs.is_valid:
                    return ComplianceStatus.REVIEW_REQUIRED, ctx_obs.error_reason or ReasonCode.TYPE_CONVERSION_ERROR
                if not ctx_exp.is_valid:
                    return ComplianceStatus.REVIEW_REQUIRED, ctx_exp.error_reason or ReasonCode.TYPE_CONVERSION_ERROR

                is_compat, compat_reason = cls._check_financial_compatibility(
                    ctx_exp, ctx_obs, require_financial_context=True
                )
                if not is_compat:
                    return ComplianceStatus.REVIEW_REQUIRED, compat_reason or ReasonCode.CURRENCY_MISMATCH

                match = ctx_obs.base_decimal_value == ctx_exp.base_decimal_value
                if operator == OperatorEnum.EQ:
                    return (ComplianceStatus.PASS, ReasonCode.EQUAL) if match else (ComplianceStatus.FAIL, ReasonCode.NOT_EQUAL)
                else:
                    return (ComplianceStatus.FAIL, ReasonCode.EQUAL) if match else (ComplianceStatus.PASS, ReasonCode.NOT_EQUAL)

            # 3. Check if date / datetime requirement
            dt_exp_obj, t_exp_type = cls._parse_date_or_datetime(expected)
            if dt_exp_obj is not None and isinstance(expected, (datetime, date, str)) and (
                isinstance(expected, (datetime, date))
                or "/" in str(expected)
                or "-" in str(expected)
                or "date" in rule.field.lower()
            ):
                dt_obs_obj, t_obs_type = cls._parse_date_or_datetime(observed)
                if dt_obs_obj is None:
                    return ComplianceStatus.REVIEW_REQUIRED, ReasonCode.MALFORMED_DATE

                if t_obs_type == "DATE_ONLY" and t_exp_type == "DATE_ONLY":
                    match = dt_obs_obj == dt_exp_obj
                elif t_obs_type == "AWARE_DATETIME" and t_exp_type == "AWARE_DATETIME":
                    match = dt_obs_obj.astimezone(timezone.utc) == dt_exp_obj.astimezone(timezone.utc)
                elif t_obs_type == "NAIVE_DATETIME" and t_exp_type == "NAIVE_DATETIME":
                    match = dt_obs_obj == dt_exp_obj
                elif (t_obs_type == "AWARE_DATETIME" and t_exp_type == "NAIVE_DATETIME") or (
                    t_obs_type == "NAIVE_DATETIME" and t_exp_type == "AWARE_DATETIME"
                ):
                    return ComplianceStatus.REVIEW_REQUIRED, ReasonCode.AMBIGUOUS_TIMEZONE
                else:
                    return ComplianceStatus.REVIEW_REQUIRED, ReasonCode.TEMPORAL_CONTEXT_MISMATCH

                if operator == OperatorEnum.EQ:
                    return (ComplianceStatus.PASS, ReasonCode.EQUAL) if match else (ComplianceStatus.FAIL, ReasonCode.NOT_EQUAL)
                else:
                    return (ComplianceStatus.FAIL, ReasonCode.EQUAL) if match else (ComplianceStatus.PASS, ReasonCode.NOT_EQUAL)

            # 4. Pure Non-Financial Numeric comparison
            num_obs = cls._normalize_number(observed)
            num_exp = cls._normalize_number(expected)
            if num_obs is not None and num_exp is not None:
                match = num_obs == num_exp
                if operator == OperatorEnum.EQ:
                    return (ComplianceStatus.PASS, ReasonCode.EQUAL) if match else (ComplianceStatus.FAIL, ReasonCode.NOT_EQUAL)
                else:
                    return (ComplianceStatus.FAIL, ReasonCode.EQUAL) if match else (ComplianceStatus.PASS, ReasonCode.NOT_EQUAL)

            # 5. General string / enum comparison
            match = cls._values_equivalent(
                observed,
                expected,
                meta_a=obs_meta,
                meta_b=rule_meta,
                unit=rule.unit,
                case_insensitive=is_ci,
                rule=rule,
            )
            if operator == OperatorEnum.EQ:
                return (ComplianceStatus.PASS, ReasonCode.EQUAL) if match else (ComplianceStatus.FAIL, ReasonCode.NOT_EQUAL)
            else:
                return (ComplianceStatus.FAIL, ReasonCode.EQUAL) if match else (ComplianceStatus.PASS, ReasonCode.NOT_EQUAL)

        elif operator in (OperatorEnum.GT, OperatorEnum.GTE, OperatorEnum.LT, OperatorEnum.LTE):
            if is_financial:
                ctx_obs = cls._parse_financial_context(observed, obs_meta, default_unit=None)
                ctx_exp = cls._parse_financial_context(expected, rule_meta, default_unit=rule.unit)

                if not ctx_obs.is_valid:
                    return ComplianceStatus.REVIEW_REQUIRED, ctx_obs.error_reason or ReasonCode.TYPE_CONVERSION_ERROR
                if not ctx_exp.is_valid:
                    return ComplianceStatus.REVIEW_REQUIRED, ctx_exp.error_reason or ReasonCode.TYPE_CONVERSION_ERROR

                is_compat, compat_reason = cls._check_financial_compatibility(
                    ctx_exp, ctx_obs, require_financial_context=True
                )
                if not is_compat:
                    return ComplianceStatus.REVIEW_REQUIRED, compat_reason or ReasonCode.CURRENCY_MISMATCH

                dec_obs = ctx_obs.base_decimal_value
                dec_exp = ctx_exp.base_decimal_value
            else:
                dec_obs = cls._normalize_number(observed)
                dec_exp = cls._normalize_number(expected)
                if dec_obs is None or dec_exp is None:
                    return ComplianceStatus.REVIEW_REQUIRED, ReasonCode.TYPE_CONVERSION_ERROR

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
            obs_obj, obs_type = cls._parse_date_or_datetime(observed)
            exp_obj, exp_type = cls._parse_date_or_datetime(expected)

            if obs_obj is None or exp_obj is None:
                return ComplianceStatus.REVIEW_REQUIRED, ReasonCode.MALFORMED_DATE

            if obs_type == "DATE_ONLY" and exp_type == "DATE_ONLY":
                if operator == OperatorEnum.DATE_BEFORE:
                    return (ComplianceStatus.PASS, ReasonCode.BEFORE_DATE) if obs_obj < exp_obj else (ComplianceStatus.FAIL, ReasonCode.ON_OR_AFTER_DATE)
                else:
                    return (ComplianceStatus.PASS, ReasonCode.AFTER_DATE) if obs_obj > exp_obj else (ComplianceStatus.FAIL, ReasonCode.ON_OR_BEFORE_DATE)

            if obs_type == "AWARE_DATETIME" and exp_type == "AWARE_DATETIME":
                obs_dt = obs_obj.astimezone(timezone.utc)
                exp_dt = exp_obj.astimezone(timezone.utc)
                if operator == OperatorEnum.DATE_BEFORE:
                    return (ComplianceStatus.PASS, ReasonCode.BEFORE_DATE) if obs_dt < exp_dt else (ComplianceStatus.FAIL, ReasonCode.ON_OR_AFTER_DATE)
                else:
                    return (ComplianceStatus.PASS, ReasonCode.AFTER_DATE) if obs_dt > exp_dt else (ComplianceStatus.FAIL, ReasonCode.ON_OR_BEFORE_DATE)

            if obs_type == "NAIVE_DATETIME" and exp_type == "NAIVE_DATETIME":
                if operator == OperatorEnum.DATE_BEFORE:
                    return (ComplianceStatus.PASS, ReasonCode.BEFORE_DATE) if obs_obj < exp_obj else (ComplianceStatus.FAIL, ReasonCode.ON_OR_AFTER_DATE)
                else:
                    return (ComplianceStatus.PASS, ReasonCode.AFTER_DATE) if obs_obj > exp_obj else (ComplianceStatus.FAIL, ReasonCode.ON_OR_BEFORE_DATE)

            if (obs_type == "AWARE_DATETIME" and exp_type == "NAIVE_DATETIME") or (
                obs_type == "NAIVE_DATETIME" and exp_type == "AWARE_DATETIME"
            ):
                return ComplianceStatus.REVIEW_REQUIRED, ReasonCode.AMBIGUOUS_TIMEZONE

            return ComplianceStatus.REVIEW_REQUIRED, ReasonCode.TEMPORAL_CONTEXT_MISMATCH

        elif operator == OperatorEnum.IN:
            exp_list = expected if isinstance(expected, (list, tuple, set)) else [expected]
            match = any(
                cls._values_equivalent(
                    observed,
                    item,
                    meta_a=obs_meta,
                    rule_unit=rule.unit,
                    case_insensitive=is_ci,
                    rule=rule,
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
                    rule=rule,
                )
                for item in exp_list
            )
            return (ComplianceStatus.PASS, ReasonCode.VALUE_NOT_IN_SET) if not match else (ComplianceStatus.FAIL, ReasonCode.VALUE_IN_SET)

        return ComplianceStatus.UNKNOWN, ReasonCode.UNSUPPORTED_OPERATOR

    @classmethod
    def reconstruct_historical_evaluation(
        cls,
        snapshot: dict[str, Any],
        rule_id: str,
    ) -> RuleEvaluationRead:
        """Performs historical evaluation reconstruction against an authoritative stored snapshot.

        Historical reconstruction retrieves and validates recorded evaluations from the stored snapshot
        rather than re-executing historical rules or claiming full semantic replay across disparate engine versions.
        Preserves recorded evaluation results and exact contributing evidence linkages.
        """
        snap_ts = snapshot.get("started_at") or snapshot.get("created_at") or snapshot.get("evaluated_at")
        if isinstance(snap_ts, str):
            try:
                snap_ts = datetime.fromisoformat(snap_ts.replace("Z", "+00:00"))
            except Exception:
                snap_ts = None
        elif not isinstance(snap_ts, datetime):
            snap_ts = None

        recorded_engine_version = snapshot.get("engine_version") or snapshot.get("rule_version")
        if recorded_engine_version and recorded_engine_version not in cls.SUPPORTED_REPLAY_VERSIONS:
            return RuleEvaluationRead(
                id=str(uuid.uuid4()),
                bidder_id=snapshot.get("bidder_id", "UNKNOWN_BIDDER"),
                requirement_id=rule_id,
                status=ComplianceStatus.REVIEW_REQUIRED,
                reason_code=ReasonCode.HISTORICAL_VERSION_UNSUPPORTED,
                observed_value=None,
                expected_value=None,
                evidence_ids=[],
                rule_version=f"{cls.ENGINE_VERSION}",
                evaluated_at=snap_ts,
            )

        recorded_evals = snapshot.get("rule_evaluations") or []
        for ev in recorded_evals:
            ev_req_id = ev.get("requirement_id") if isinstance(ev, dict) else getattr(ev, "requirement_id", None)
            if ev_req_id == rule_id:
                if isinstance(ev, dict):
                    return RuleEvaluationRead.model_validate(ev)
                return ev

        return RuleEvaluationRead(
            id=str(uuid.uuid4()),
            bidder_id=snapshot.get("bidder_id", "UNKNOWN_BIDDER"),
            requirement_id=rule_id,
            status=ComplianceStatus.UNKNOWN,
            reason_code=ReasonCode.HISTORICAL_EVALUATION_NOT_FOUND,
            observed_value=None,
            expected_value=None,
            evidence_ids=[],
            rule_version=f"{cls.ENGINE_VERSION}",
            evaluated_at=snap_ts,
        )

    # Historical reconstruction backward-compatible alias
    replay_evaluation = reconstruct_historical_evaluation
