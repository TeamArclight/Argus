from datetime import datetime, timezone
import math
import re
from typing import Any
from pydantic import BaseModel, Field

from app.compliance.engine import ComplianceEngine
from app.schemas.canonical import (
    FactRead,
    RiskInputRef,
    RiskInputType,
    RiskSeverity,
    VerificationMode,
    VerificationResultRead,
    VerificationStatus,
)


class RiskSignalCandidate(BaseModel):
    severity: RiskSeverity
    signal_type: str
    title: str
    description: str
    reason_code: str
    input_refs: list[RiskInputRef] = Field(default_factory=list)
    source_mode: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)

    @property
    def input_ids(self) -> list[str]:
        """Returns flat list of IDs for backward compatibility."""
        return [ref.id for ref in self.input_refs]


class RiskEngine:
    """Pure, stateless risk detection component evaluating consistency, freshness, and anomalies across facts, verifications, and document metadata."""

    PAN_REGEX = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
    GSTIN_REGEX = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$")
    UDYAM_REGEX = re.compile(r"^UDYAM-[A-Z]{2}-[0-9]{2}-[0-9]{7}$", re.IGNORECASE)

    DEFAULT_FRESHNESS_DAYS = {
        "gst": 30,
        "mca": 30,
        "udyam": 90,
        "epfo": 30,
        "esic": 30,
        "blacklist": 7,
        "general.gstin": 30,
        "general.udyam": 90,
        "general.cin": 30,
        "general.epfo": 30,
        "general.esic": 30,
        "debarment.status": 7,
    }

    @classmethod
    def evaluate_risks(
        cls,
        facts: list[FactRead],
        verifications: list[VerificationResultRead],
        documents: list[dict[str, Any]],
        bidder_data: dict[str, Any],
        comparison_metadata: list[dict[str, Any]] | None = None,
        comparison_authorized: bool = True,
        freshness_policy: dict[str, int] | None = None,
        evaluation_timestamp: datetime | None = None,
    ) -> list[RiskSignalCandidate]:
        """
        Pure deterministic risk detection.
        No DB queries, no HTTP requests, no AI/ML calls, zero side effects.
        """
        eval_ts = evaluation_timestamp or datetime.now(timezone.utc)
        candidates: list[RiskSignalCandidate] = []
        policy = {**cls.DEFAULT_FRESHNESS_DAYS, **(freshness_policy or {})}

        # 1. Identifier Consistency Checks
        cls._check_identifier_formats(bidder_data, facts, candidates)
        cls._check_gstin_pan_consistency(bidder_data, facts, candidates)
        cls._check_legal_name_consistency(bidder_data, facts, verifications, candidates)

        # 2. Cross-Document Consistency & OEM Expiry Checks
        cls._check_cross_document_financials(facts, candidates)
        cls._check_duplicate_document_hashes(documents, comparison_metadata, comparison_authorized, candidates)
        cls._check_oem_authorizations(facts, eval_ts, candidates)

        # 3. Financial Consistency & Claimed vs Verified Checks
        cls._check_claimed_vs_verified_financials(facts, verifications, candidates)

        # 4. Cached Record Freshness Checks
        cls._check_cached_freshness(verifications, policy, eval_ts, candidates)

        return candidates

    @classmethod
    def _check_identifier_formats(
        cls, bidder_data: dict[str, Any], facts: list[FactRead], candidates: list[RiskSignalCandidate]
    ) -> None:
        bidder_id = bidder_data.get("id") or "BIDDER_RECORD"
        pan = bidder_data.get("pan")
        if pan and isinstance(pan, str) and not cls.PAN_REGEX.match(pan.upper()):
            candidates.append(
                RiskSignalCandidate(
                    severity=RiskSeverity.MEDIUM,
                    signal_type="INVALID_PAN_FORMAT",
                    title="Invalid PAN Format",
                    description=f"Claimed PAN '{pan}' does not conform to standard 10-character PAN structure (e.g. ABCDE1234F).",
                    reason_code="INVALID_PAN_FORMAT",
                    input_refs=[RiskInputRef(ref_type=RiskInputType.BIDDER_RECORD, id=bidder_id)],
                    metadata_json={"claimed_pan": pan},
                )
            )

        gstin = bidder_data.get("gstin")
        if gstin and isinstance(gstin, str) and not cls.GSTIN_REGEX.match(gstin.upper()):
            candidates.append(
                RiskSignalCandidate(
                    severity=RiskSeverity.MEDIUM,
                    signal_type="INVALID_GSTIN_FORMAT",
                    title="Invalid GSTIN Format",
                    description=f"Claimed GSTIN '{gstin}' does not conform to standard 15-character GSTIN structure.",
                    reason_code="INVALID_GSTIN_FORMAT",
                    input_refs=[RiskInputRef(ref_type=RiskInputType.BIDDER_RECORD, id=bidder_id)],
                    metadata_json={"claimed_gstin": gstin},
                )
            )

        udyam = bidder_data.get("udyam_number")
        if udyam and isinstance(udyam, str) and not cls.UDYAM_REGEX.match(udyam):
            candidates.append(
                RiskSignalCandidate(
                    severity=RiskSeverity.MEDIUM,
                    signal_type="INVALID_UDYAM_FORMAT",
                    title="Invalid Udyam Registration Format",
                    description=f"Claimed Udyam number '{udyam}' does not conform to standard format (UDYAM-XX-00-0000000).",
                    reason_code="INVALID_UDYAM_FORMAT",
                    input_refs=[RiskInputRef(ref_type=RiskInputType.BIDDER_RECORD, id=bidder_id)],
                    metadata_json={"claimed_udyam": udyam},
                )
            )

    @classmethod
    def _check_gstin_pan_consistency(
        cls, bidder_data: dict[str, Any], facts: list[FactRead], candidates: list[RiskSignalCandidate]
    ) -> None:
        bidder_id = bidder_data.get("id") or "BIDDER_RECORD"
        gstin = bidder_data.get("gstin")
        pan = bidder_data.get("pan")
        contributing_fact_ids: list[str] = []

        for f in facts:
            if not gstin and f.field == "general.gstin" and isinstance(f.value, str):
                gstin = f.value
                contributing_fact_ids.append(f.id)
            if not pan and f.field == "general.pan" and isinstance(f.value, str):
                pan = f.value
                contributing_fact_ids.append(f.id)

        if gstin and isinstance(gstin, str) and len(gstin) == 15 and cls.GSTIN_REGEX.match(gstin.upper()):
            embedded_pan = gstin[2:12].upper()
            if pan and isinstance(pan, str) and cls.PAN_REGEX.match(pan.upper()):
                if pan.upper() != embedded_pan:
                    refs = [RiskInputRef(ref_type=RiskInputType.BIDDER_RECORD, id=bidder_id)]
                    for fid in contributing_fact_ids:
                        refs.append(RiskInputRef(ref_type=RiskInputType.EXTRACTED_FACT, id=fid))

                    candidates.append(
                        RiskSignalCandidate(
                            severity=RiskSeverity.HIGH,
                            signal_type="GSTIN_PAN_MISMATCH",
                            title="GSTIN and PAN Identifier Mismatch",
                            description=f"Embedded PAN '{embedded_pan}' in GSTIN '{gstin}' does not match claimed PAN '{pan}'.",
                            reason_code="GSTIN_PAN_MISMATCH",
                            input_refs=refs,
                            metadata_json={"gstin": gstin, "embedded_pan": embedded_pan, "claimed_pan": pan},
                        )
                    )

    @classmethod
    def _normalize_name(cls, name: str | None) -> str:
        if not name or not isinstance(name, str):
            return ""
        n = name.lower().strip()
        for suffix in ["private limited", "pvt ltd", "pvt. ltd.", "limited", "ltd.", "ltd", "inc.", "inc", "corp"]:
            if n.endswith(suffix):
                n = n[:-len(suffix)].strip()
        return re.sub(r"[^\w\s]", "", n)

    @classmethod
    def _check_legal_name_consistency(
        cls,
        bidder_data: dict[str, Any],
        facts: list[FactRead],
        verifications: list[VerificationResultRead],
        candidates: list[RiskSignalCandidate],
    ) -> None:
        bidder_id = bidder_data.get("id") or "BIDDER_RECORD"
        primary_name = bidder_data.get("bidder_name")
        norm_primary = cls._normalize_name(primary_name)
        if not norm_primary:
            return

        for f in facts:
            if f.field in ("general.bidder_name", "legal.company_name") and isinstance(f.value, str):
                norm_fact_name = cls._normalize_name(f.value)
                if norm_fact_name and norm_fact_name != norm_primary:
                    candidates.append(
                        RiskSignalCandidate(
                            severity=RiskSeverity.MEDIUM,
                            signal_type="LEGAL_NAME_MISMATCH",
                            title="Discrepancy in Extracted Legal Name",
                            description=f"Claimed bidder name '{primary_name}' differs from extracted document legal name '{f.value}'.",
                            reason_code="LEGAL_NAME_MISMATCH",
                            input_refs=[
                                RiskInputRef(ref_type=RiskInputType.BIDDER_RECORD, id=bidder_id),
                                RiskInputRef(ref_type=RiskInputType.EXTRACTED_FACT, id=f.id),
                            ],
                            metadata_json={"claimed_name": primary_name, "extracted_name": f.value},
                        )
                    )

    @classmethod
    def _parse_currency_and_scale(cls, val: Any, meta: dict[str, Any]) -> tuple[str | None, str | None, float | None, bool]:
        """
        Parses currency, unit/scale, and numeric base value from string or metadata.
        Returns: (currency, unit, scaled_value, is_explicit_unit)
        """
        currency = meta.get("currency")
        unit = meta.get("unit")
        is_explicit = bool(unit)

        val_str = str(val) if val is not None else ""
        val_lower = val_str.lower().strip()

        # Currency detection
        if not currency:
            if "$" in val_str or "usd" in val_lower:
                currency = "USD"
            elif "₹" in val_str or "inr" in val_lower or "rs" in val_lower:
                currency = "INR"

        # Unit / Scale detection
        scale = 1.0
        if "crore" in val_lower or "cr" in val_lower:
            scale = 10_000_000.0
            unit = unit or "Crore"
            is_explicit = True
        elif "lakh" in val_lower or "lac" in val_lower:
            scale = 100_000.0
            unit = unit or "Lakh"
            is_explicit = True
        elif "billion" in val_lower or "b" in val_lower:
            scale = 1_000_000_000.0
            unit = unit or "Billion"
            is_explicit = True
        elif "million" in val_lower or "m" in val_lower:
            scale = 1_000_000.0
            unit = unit or "Million"
            is_explicit = True

        val_to_parse: Any = val
        if isinstance(val, str):
            clean_s = val
            tokens_to_remove = [
                "USD", "usd", "$", "INR", "inr", "₹", "Rs.", "rs.", "Rs", "rs", "EUR", "eur", "GBP", "gbp", "Rupees", "rupees",
                "crore", "crores", "cr", "lakh", "lakhs", "lac", "lacs", "billion", "million"
            ]
            for token in tokens_to_remove:
                if token in ("$", "₹"):
                    clean_s = clean_s.replace(token, "")
                else:
                    clean_s = re.sub(r'\b' + re.escape(token) + r'\b', '', clean_s, flags=re.IGNORECASE)
            clean_s = clean_s.replace(",", "").strip()
            val_to_parse = clean_s

        raw_num = ComplianceEngine._normalize_number(val_to_parse)
        if raw_num is None or math.isnan(raw_num) or math.isinf(raw_num):
            return currency, unit, None, is_explicit

        scaled_val = float(raw_num * scale)
        return currency, unit, scaled_val, is_explicit

    @classmethod
    def _check_cross_document_financials(
        cls, facts: list[FactRead], candidates: list[RiskSignalCandidate]
    ) -> None:
        """
        Cross-document financial checks with strict metric, FY, currency, and unit safety.
        """
        # Separate facts by metric
        fact_groups: dict[tuple[str, str], list[FactRead]] = {}

        for f in facts:
            if f.field in ("financial.average_annual_turnover", "financial.turnover", "financial.annual_turnover"):
                meta = f.metadata_json if isinstance(f.metadata_json, dict) else {}
                fy = meta.get("financial_year") or "UNSPECIFIED_FY"
                fact_groups.setdefault((f.field, fy), []).append(f)

        # 1. Detect metric mismatch across average vs single-year turnover
        avg_facts = [f for f in facts if f.field == "financial.average_annual_turnover"]
        single_facts = [f for f in facts if f.field in ("financial.turnover", "financial.annual_turnover")]

        if avg_facts and single_facts:
            candidates.append(
                RiskSignalCandidate(
                    severity=RiskSeverity.LOW,
                    signal_type="AMBIGUOUS_FINANCIAL_METRIC",
                    title="Average vs Single-Year Turnover Comparison Requiring Review",
                    description="Extracted claims include both multi-year average annual turnover and single-year annual turnover. Officer review required before comparing metrics.",
                    reason_code="AMBIGUOUS_FINANCIAL_METRIC",
                    input_refs=[RiskInputRef(ref_type=RiskInputType.EXTRACTED_FACT, id=f.id) for f in avg_facts + single_facts],
                    metadata_json={"avg_fact_count": len(avg_facts), "single_fact_count": len(single_facts)},
                )
            )

        # 2. Detect averaging period mismatch within average_annual_turnover facts
        if len(avg_facts) > 1:
            periods = {}
            for f in avg_facts:
                meta = f.metadata_json if isinstance(f.metadata_json, dict) else {}
                period = meta.get("averaging_period") or "UNSPECIFIED_PERIOD"
                periods.setdefault(period, []).append(f)

            if len(periods) > 1 and "UNSPECIFIED_PERIOD" not in periods:
                candidates.append(
                    RiskSignalCandidate(
                        severity=RiskSeverity.MEDIUM,
                        signal_type="AVERAGING_PERIOD_MISMATCH",
                        title="Incompatible Turnover Averaging Periods",
                        description="Extracted average annual turnover claims use conflicting averaging periods (e.g. 3-year vs 5-year average). Numerical comparison deferred.",
                        reason_code="AVERAGING_PERIOD_MISMATCH",
                        input_refs=[RiskInputRef(ref_type=RiskInputType.EXTRACTED_FACT, id=f.id) for f in avg_facts],
                        metadata_json={"observed_periods": list(periods.keys())},
                    )
                )

        # 3. Compare facts sharing identical metric AND identical FY
        for (field_name, fy), fact_list in fact_groups.items():
            if fy == "UNSPECIFIED_FY" or len(fact_list) < 2:
                continue

            base_fact = fact_list[0]
            base_meta = base_fact.metadata_json if isinstance(base_fact.metadata_json, dict) else {}
            curr_1, unit_1, val_1, exp_1 = cls._parse_currency_and_scale(base_fact.value, base_meta)

            for other_fact in fact_list[1:]:
                other_meta = other_fact.metadata_json if isinstance(other_fact.metadata_json, dict) else {}
                curr_2, unit_2, val_2, exp_2 = cls._parse_currency_and_scale(other_fact.value, other_meta)

                # Check non-finite / malformed
                if val_1 is None or val_2 is None:
                    candidates.append(
                        RiskSignalCandidate(
                            severity=RiskSeverity.LOW,
                            signal_type="MALFORMED_FINANCIAL_VALUE",
                            title="Unparseable Financial Value",
                            description=f"Turnover claim value '{base_fact.value}' or '{other_fact.value}' is non-numeric or malformed.",
                            reason_code="MALFORMED_FINANCIAL_VALUE",
                            input_refs=[
                                RiskInputRef(ref_type=RiskInputType.EXTRACTED_FACT, id=base_fact.id),
                                RiskInputRef(ref_type=RiskInputType.EXTRACTED_FACT, id=other_fact.id),
                            ],
                            metadata_json={"value_1": str(base_fact.value), "value_2": str(other_fact.value)},
                        )
                    )
                    continue

                # Check currency compatibility
                if curr_1 and curr_2 and curr_1 != curr_2:
                    candidates.append(
                        RiskSignalCandidate(
                            severity=RiskSeverity.MEDIUM,
                            signal_type="AMBIGUOUS_FINANCIAL_CURRENCY",
                            title="Incompatible Currencies in Financial Claims",
                            description=f"Turnover claims for FY {fy} use incompatible currencies ({curr_1} vs {curr_2}). Silent currency conversion prohibited.",
                            reason_code="AMBIGUOUS_FINANCIAL_CURRENCY",
                            input_refs=[
                                RiskInputRef(ref_type=RiskInputType.EXTRACTED_FACT, id=base_fact.id),
                                RiskInputRef(ref_type=RiskInputType.EXTRACTED_FACT, id=other_fact.id),
                            ],
                            metadata_json={"currency_1": curr_1, "currency_2": curr_2, "financial_year": fy},
                        )
                    )
                    continue

                # Check unit compatibility (explicit vs missing bare number)
                if exp_1 != exp_2:
                    candidates.append(
                        RiskSignalCandidate(
                            severity=RiskSeverity.MEDIUM,
                            signal_type="AMBIGUOUS_FINANCIAL_UNIT",
                            title="Missing or Mismatched Financial Scale/Unit",
                            description=f"Turnover claims for FY {fy} differ in scale context (e.g. bare number vs explicit Crore/Lakh unit). Manual officer review required.",
                            reason_code="AMBIGUOUS_FINANCIAL_UNIT",
                            input_refs=[
                                RiskInputRef(ref_type=RiskInputType.EXTRACTED_FACT, id=base_fact.id),
                                RiskInputRef(ref_type=RiskInputType.EXTRACTED_FACT, id=other_fact.id),
                            ],
                            metadata_json={"unit_1": unit_1, "unit_2": unit_2, "financial_year": fy},
                        )
                    )
                    continue

                # Compatible metric, FY, currency, unit -> numerical comparison
                if abs(val_1 - val_2) > 0.01:
                    candidates.append(
                        RiskSignalCandidate(
                            severity=RiskSeverity.HIGH,
                            signal_type="CONFLICTING_TURNOVER_SAME_FY",
                            title="Conflicting Turnover for Same Financial Year",
                            description=f"Multiple conflicting turnover values observed for financial year {fy}: {val_1} vs {val_2}.",
                            reason_code="CONFLICTING_TURNOVER_SAME_FY",
                            input_refs=[
                                RiskInputRef(ref_type=RiskInputType.EXTRACTED_FACT, id=base_fact.id),
                                RiskInputRef(ref_type=RiskInputType.EXTRACTED_FACT, id=other_fact.id),
                            ],
                            metadata_json={
                                "financial_year": fy,
                                "raw_value_1": base_fact.value,
                                "raw_value_2": other_fact.value,
                                "normalized_value_1": val_1,
                                "normalized_value_2": val_2,
                            },
                        )
                    )

    @classmethod
    def _check_duplicate_document_hashes(
        cls,
        documents: list[dict[str, Any]],
        comparison_metadata: list[dict[str, Any]] | None,
        comparison_authorized: bool,
        candidates: list[RiskSignalCandidate],
    ) -> None:
        if not comparison_authorized or not comparison_metadata:
            return

        doc_hashes = {d.get("sha256"): d for d in documents if d.get("sha256")}

        for other_doc in comparison_metadata:
            sha = other_doc.get("sha256")
            if sha and sha in doc_hashes:
                local_doc = doc_hashes[sha]
                local_doc_id = local_doc.get("id", "UNKNOWN_DOC")

                candidates.append(
                    RiskSignalCandidate(
                        severity=RiskSeverity.MEDIUM,
                        signal_type="DUPLICATE_DOCUMENT_HASH_CROSS_BIDDER",
                        title="Duplicate Document Hash Observed Across Bidders",
                        description=f"Document '{local_doc.get('filename')}' shares an identical SHA-256 content digest with a document submitted in another bid within authorized tender scope. This indicates standard form/template reuse and requires officer review.",
                        reason_code="DUPLICATE_DOCUMENT_HASH_CROSS_BIDDER",
                        input_refs=[RiskInputRef(ref_type=RiskInputType.DOCUMENT, id=local_doc_id)],
                        metadata_json={
                            "document_id": local_doc_id,
                            "sha256": sha,
                            "comparison_scope": "AUTHORIZED_TENDER_METADATA",
                        },
                    )
                )

    @classmethod
    def _check_oem_authorizations(
        cls, facts: list[FactRead], eval_ts: datetime, candidates: list[RiskSignalCandidate]
    ) -> None:
        """Evaluates OEM authorization validity end date against explicit evaluation_timestamp."""
        for f in facts:
            if f.field in ("oem.validity_end_date", "oem.expiry_date") and f.value:
                dt_val = None
                val_str = str(f.value).strip()

                # Parse ISO datetime or YYYY-MM-DD date-only
                if isinstance(f.value, str):
                    try:
                        if len(val_str) == 10 and re.match(r"^\d{4}-\d{2}-\d{2}$", val_str):
                            dt_val = datetime.fromisoformat(val_str + "T23:59:59+00:00")
                        else:
                            dt_val = datetime.fromisoformat(val_str.replace("Z", "+00:00"))
                    except Exception:
                        pass

                if dt_val:
                    if dt_val.tzinfo is None:
                        dt_val = dt_val.replace(tzinfo=timezone.utc)

                    if dt_val < eval_ts:
                        candidates.append(
                            RiskSignalCandidate(
                                severity=RiskSeverity.HIGH,
                                signal_type="OEM_AUTHORIZATION_EXPIRED",
                                title="OEM Authorization Expired",
                                description=f"OEM authorization validity date '{f.value}' is expired relative to evaluation timestamp '{eval_ts.isoformat()}'.",
                                reason_code="OEM_AUTHORIZATION_EXPIRED",
                                input_refs=[RiskInputRef(ref_type=RiskInputType.EXTRACTED_FACT, id=f.id)],
                                metadata_json={"expiry_date": str(f.value), "evaluation_timestamp": eval_ts.isoformat()},
                            )
                        )
                else:
                    candidates.append(
                        RiskSignalCandidate(
                            severity=RiskSeverity.LOW,
                            signal_type="MALFORMED_OEM_EXPIRY_DATE",
                            title="Unparseable OEM Authorization Date",
                            description=f"OEM authorization expiry date claim '{f.value}' could not be parsed into a valid date structure.",
                            reason_code="MALFORMED_OEM_EXPIRY_DATE",
                            input_refs=[RiskInputRef(ref_type=RiskInputType.EXTRACTED_FACT, id=f.id)],
                            metadata_json={"raw_expiry_value": str(f.value)},
                        )
                    )

    @classmethod
    def _check_claimed_vs_verified_financials(
        cls,
        facts: list[FactRead],
        verifications: list[VerificationResultRead],
        candidates: list[RiskSignalCandidate],
    ) -> None:
        for v in verifications:
            if v.field in ("financial.turnover", "financial.average_annual_turnover") and v.status == VerificationStatus.MISMATCH:
                mode_str = v.mode.value if hasattr(v.mode, "value") else str(v.mode)
                if v.mode == VerificationMode.LIVE:
                    desc = f"Claimed turnover '{v.claimed_value}' does not match live verified turnover '{v.verified_value}'."
                elif v.mode == VerificationMode.PORTAL_CACHED:
                    desc = f"Claimed turnover '{v.claimed_value}' does not match portal cached verified turnover '{v.verified_value}'."
                elif v.mode == VerificationMode.DOCUMENT:
                    desc = f"Claimed turnover '{v.claimed_value}' does not match document extracted turnover '{v.verified_value}'."
                else:
                    desc = f"Claimed turnover '{v.claimed_value}' does not match demo simulated turnover '{v.verified_value}'."

                candidates.append(
                    RiskSignalCandidate(
                        severity=RiskSeverity.HIGH,
                        signal_type="FINANCIAL_TURNOVER_VERIFICATION_MISMATCH",
                        title="Turnover Verification Mismatch",
                        description=desc,
                        reason_code="FINANCIAL_TURNOVER_VERIFICATION_MISMATCH",
                        input_refs=[RiskInputRef(ref_type=RiskInputType.VERIFICATION_RESULT, id=v.id)],
                        source_mode=mode_str,
                        metadata_json={"claimed_value": v.claimed_value, "verified_value": v.verified_value, "verification_mode": mode_str},
                    )
                )

    @classmethod
    def _check_cached_freshness(
        cls,
        verifications: list[VerificationResultRead],
        policy: dict[str, int],
        eval_ts: datetime,
        candidates: list[RiskSignalCandidate],
    ) -> None:
        for v in verifications:
            if v.mode == VerificationMode.PORTAL_CACHED:
                domain = v.field

                # Check policy threshold for this exact field/domain
                if domain not in policy:
                    candidates.append(
                        RiskSignalCandidate(
                            severity=RiskSeverity.LOW,
                            signal_type="MISSING_FRESHNESS_POLICY",
                            title="Missing Domain Freshness Policy",
                            description=f"Field '{v.field}' has no configured freshness policy threshold; freshness operational status is UNKNOWN.",
                            reason_code="MISSING_FRESHNESS_POLICY",
                            input_refs=[RiskInputRef(ref_type=RiskInputType.VERIFICATION_RESULT, id=v.id)],
                            source_mode="PORTAL_CACHED",
                            metadata_json={"field": v.field},
                        )
                    )
                    continue

                max_days = policy[domain]
                if not isinstance(max_days, int) or max_days <= 0:
                    candidates.append(
                        RiskSignalCandidate(
                            severity=RiskSeverity.LOW,
                            signal_type="INVALID_FRESHNESS_POLICY",
                            title="Invalid Freshness Policy Threshold",
                            description=f"Freshness threshold for '{v.field}' ({max_days}) is non-positive or invalid.",
                            reason_code="INVALID_FRESHNESS_POLICY",
                            input_refs=[RiskInputRef(ref_type=RiskInputType.VERIFICATION_RESULT, id=v.id)],
                            source_mode="PORTAL_CACHED",
                            metadata_json={"field": v.field, "policy_threshold": max_days},
                        )
                    )
                    continue

                raw_meta = getattr(v, "location_metadata", None) or getattr(v, "metadata_json", None) or {}
                meta = raw_meta if isinstance(raw_meta, dict) else {}
                obs_at_raw = meta.get("source_observed_at") or meta.get("observed_at")

                if not obs_at_raw:
                    candidates.append(
                        RiskSignalCandidate(
                            severity=RiskSeverity.LOW,
                            signal_type="MISSING_FRESHNESS_TIMESTAMP",
                            title="Missing Cached Source Observation Timestamp",
                            description=f"Portal cached record for '{v.field}' lacks source observation timestamp; freshness is unknown.",
                            reason_code="MISSING_FRESHNESS_TIMESTAMP",
                            input_refs=[RiskInputRef(ref_type=RiskInputType.VERIFICATION_RESULT, id=v.id)],
                            source_mode="PORTAL_CACHED",
                            metadata_json={"field": v.field},
                        )
                    )
                    continue

                obs_at = None
                if isinstance(obs_at_raw, str):
                    try:
                        if len(obs_at_raw.strip()) == 10 and re.match(r"^\d{4}-\d{2}-\d{2}$", obs_at_raw.strip()):
                            obs_at = datetime.fromisoformat(obs_at_raw.strip() + "T00:00:00+00:00")
                        else:
                            obs_at = datetime.fromisoformat(obs_at_raw.replace("Z", "+00:00"))
                    except Exception:
                        pass
                elif isinstance(obs_at_raw, datetime):
                    obs_at = obs_at_raw

                if obs_at:
                    if obs_at.tzinfo is None:
                        obs_at = obs_at.replace(tzinfo=timezone.utc)

                    # Check future source observation timestamp
                    if obs_at > eval_ts:
                        candidates.append(
                            RiskSignalCandidate(
                                severity=RiskSeverity.MEDIUM,
                                signal_type="FUTURE_FRESHNESS_TIMESTAMP",
                                title="Future Source Observation Timestamp",
                                description=f"Cached record for '{v.field}' has observation timestamp '{obs_at.isoformat()}' in the future relative to evaluation date '{eval_ts.isoformat()}'. Review required.",
                                reason_code="FUTURE_FRESHNESS_TIMESTAMP",
                                input_refs=[RiskInputRef(ref_type=RiskInputType.VERIFICATION_RESULT, id=v.id)],
                                source_mode="PORTAL_CACHED",
                                metadata_json={"field": v.field, "source_observed_at": obs_at.isoformat(), "evaluation_timestamp": eval_ts.isoformat()},
                            )
                        )
                        continue

                    age_days = (eval_ts - obs_at).days
                    if age_days > max_days:
                        candidates.append(
                            RiskSignalCandidate(
                                severity=RiskSeverity.LOW,
                                signal_type="STALE_CACHED_RECORD",
                                title="Stale Cached Verification Record",
                                description=f"Cached record for '{v.field}' observed {age_days} days ago exceeds operational policy threshold of {max_days} days.",
                                reason_code="STALE_CACHED_RECORD",
                                input_refs=[RiskInputRef(ref_type=RiskInputType.VERIFICATION_RESULT, id=v.id)],
                                source_mode="PORTAL_CACHED",
                                metadata_json={
                                    "field": v.field,
                                    "age_days": age_days,
                                    "policy_threshold_days": max_days,
                                    "source_observed_at": obs_at.isoformat(),
                                    "evaluation_timestamp": eval_ts.isoformat(),
                                },
                            )
                        )
