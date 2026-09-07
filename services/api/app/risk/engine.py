from datetime import datetime, timezone
import re
from typing import Any
from pydantic import BaseModel, Field
from app.compliance.engine import ComplianceEngine
from app.schemas.canonical import (
    FactRead,
    RiskSeverity,
    VerificationMode,
    VerificationResultRead,
)


class RiskSignalCandidate(BaseModel):
    severity: RiskSeverity
    signal_type: str
    title: str
    description: str
    reason_code: str
    input_ids: list[str] = Field(default_factory=list)
    source_mode: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)


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
        freshness_policy: dict[str, int] | None = None,
    ) -> list[RiskSignalCandidate]:
        """
        Pure deterministic risk detection.
        No DB queries, no HTTP requests, no AI/ML calls, zero side effects.
        """
        candidates: list[RiskSignalCandidate] = []
        policy = {**cls.DEFAULT_FRESHNESS_DAYS, **(freshness_policy or {})}

        # 1. Identifier Consistency Checks
        cls._check_identifier_formats(bidder_data, facts, candidates)
        cls._check_gstin_pan_consistency(bidder_data, facts, candidates)
        cls._check_legal_name_consistency(bidder_data, facts, verifications, candidates)

        # 2. Cross-Document Consistency Checks
        cls._check_cross_document_financials(facts, candidates)
        cls._check_duplicate_document_hashes(documents, comparison_metadata, candidates)
        cls._check_oem_authorizations(facts, candidates)

        # 3. Financial Consistency & Claimed vs Verified Checks
        cls._check_claimed_vs_verified_financials(facts, verifications, candidates)

        # 4. Cached Record Freshness Checks
        cls._check_cached_freshness(verifications, policy, candidates)

        return candidates

    @classmethod
    def _check_identifier_formats(
        cls, bidder_data: dict[str, Any], facts: list[FactRead], candidates: list[RiskSignalCandidate]
    ) -> None:
        pan = bidder_data.get("pan")
        if pan and isinstance(pan, str) and not cls.PAN_REGEX.match(pan.upper()):
            candidates.append(
                RiskSignalCandidate(
                    severity=RiskSeverity.MEDIUM,
                    signal_type="INVALID_PAN_FORMAT",
                    title="Invalid PAN Format",
                    description=f"Claimed PAN '{pan}' does not conform to standard 10-character PAN structure (e.g. ABCDE1234F).",
                    reason_code="INVALID_PAN_FORMAT",
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
                    metadata_json={"claimed_udyam": udyam},
                )
            )

    @classmethod
    def _check_gstin_pan_consistency(
        cls, bidder_data: dict[str, Any], facts: list[FactRead], candidates: list[RiskSignalCandidate]
    ) -> None:
        gstin = bidder_data.get("gstin")
        pan = bidder_data.get("pan")

        # Find GSTIN/PAN in facts if not present in bidder_data
        for f in facts:
            if not gstin and f.field == "general.gstin" and isinstance(f.value, str):
                gstin = f.value
            if not pan and f.field == "general.pan" and isinstance(f.value, str):
                pan = f.value

        if gstin and isinstance(gstin, str) and len(gstin) == 15 and cls.GSTIN_REGEX.match(gstin.upper()):
            embedded_pan = gstin[2:12].upper()
            if pan and isinstance(pan, str) and cls.PAN_REGEX.match(pan.upper()):
                if pan.upper() != embedded_pan:
                    candidates.append(
                        RiskSignalCandidate(
                            severity=RiskSeverity.HIGH,
                            signal_type="GSTIN_PAN_MISMATCH",
                            title="GSTIN and PAN Identifier Mismatch",
                            description=f"Embedded PAN '{embedded_pan}' in GSTIN '{gstin}' does not match claimed PAN '{pan}'.",
                            reason_code="GSTIN_PAN_MISMATCH",
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
                            input_ids=[f.id],
                            metadata_json={"claimed_name": primary_name, "extracted_name": f.value},
                        )
                    )

    @classmethod
    def _check_cross_document_financials(
        cls, facts: list[FactRead], candidates: list[RiskSignalCandidate]
    ) -> None:
        fy_map: dict[str, list[tuple[FactRead, float]]] = {}

        for f in facts:
            if f.field in ("financial.average_annual_turnover", "financial.turnover"):
                num_val = ComplianceEngine._normalize_number(f.value)
                if num_val is None:
                    continue

                fy = f.metadata_json.get("financial_year") if isinstance(f.metadata_json, dict) else None
                if not fy:
                    fy = "UNSPECIFIED_FY"

                fy_map.setdefault(fy, []).append((f, num_val))

        for fy, fact_list in fy_map.items():
            if fy == "UNSPECIFIED_FY":
                continue

            if len(fact_list) > 1:
                base_fact, base_val = fact_list[0]
                for other_fact, other_val in fact_list[1:]:
                    if abs(base_val - other_val) > 0.01:
                        candidates.append(
                            RiskSignalCandidate(
                                severity=RiskSeverity.HIGH,
                                signal_type="CONFLICTING_TURNOVER_SAME_FY",
                                title="Conflicting Turnover for Same Financial Year",
                                description=f"Multiple extracted turnover values observed for financial year {fy}: {base_val} vs {other_val}.",
                                reason_code="CONFLICTING_TURNOVER_SAME_FY",
                                input_ids=[base_fact.id, other_fact.id],
                                metadata_json={"financial_year": fy, "value_1": base_val, "value_2": other_val},
                            )
                        )

    @classmethod
    def _check_duplicate_document_hashes(
        cls,
        documents: list[dict[str, Any]],
        comparison_metadata: list[dict[str, Any]] | None,
        candidates: list[RiskSignalCandidate],
    ) -> None:
        if not comparison_metadata:
            return

        doc_hashes = {d.get("sha256"): d for d in documents if d.get("sha256")}

        for other_doc in comparison_metadata:
            sha = other_doc.get("sha256")
            if sha and sha in doc_hashes:
                local_doc = doc_hashes[sha]
                candidates.append(
                    RiskSignalCandidate(
                        severity=RiskSeverity.MEDIUM,
                        signal_type="DUPLICATE_DOCUMENT_HASH_CROSS_BIDDER",
                        title="Duplicate Document Hash Observed Across Bidders",
                        description=f"Document '{local_doc.get('filename')}' shares an identical SHA-256 content digest with a document submitted in another bid. This indicates standard form/template reuse or requires officer review.",
                        reason_code="DUPLICATE_DOCUMENT_HASH_CROSS_BIDDER",
                        input_ids=[local_doc.get("id")],
                        metadata_json={
                            "document_id": local_doc.get("id"),
                            "sha256": sha,
                            "comparison_scope": "AUTHORIZED_TENDER_METADATA",
                        },
                    )
                )

    @classmethod
    def _check_oem_authorizations(
        cls, facts: list[FactRead], candidates: list[RiskSignalCandidate]
    ) -> None:
        now = datetime.now(timezone.utc)
        for f in facts:
            if f.field in ("oem.validity_end_date", "oem.expiry_date") and f.value:
                dt_val = None
                if isinstance(f.value, str):
                    try:
                        dt_val = datetime.fromisoformat(f.value.replace("Z", "+00:00"))
                    except Exception:
                        pass
                if dt_val and dt_val < now:
                    candidates.append(
                        RiskSignalCandidate(
                            severity=RiskSeverity.HIGH,
                            signal_type="OEM_AUTHORIZATION_EXPIRED",
                            title="OEM Authorization Expired",
                            description=f"OEM authorization validity date '{f.value}' is expired relative to current evaluation date.",
                            reason_code="OEM_AUTHORIZATION_EXPIRED",
                            input_ids=[f.id],
                            metadata_json={"expiry_date": f.value},
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
                candidates.append(
                    RiskSignalCandidate(
                        severity=RiskSeverity.HIGH,
                        signal_type="FINANCIAL_TURNOVER_VERIFICATION_MISMATCH",
                        title="Turnover Verification Mismatch",
                        description=f"Claimed turnover '{v.claimed_value}' does not match official verified turnover '{v.verified_value}'.",
                        reason_code="FINANCIAL_TURNOVER_VERIFICATION_MISMATCH",
                        input_ids=[v.id],
                        source_mode=v.mode.value if hasattr(v.mode, "value") else str(v.mode),
                        metadata_json={"claimed_value": v.claimed_value, "verified_value": v.verified_value},
                    )
                )

    @classmethod
    def _check_cached_freshness(
        cls,
        verifications: list[VerificationResultRead],
        policy: dict[str, int],
        candidates: list[RiskSignalCandidate],
    ) -> None:
        for v in verifications:
            if v.mode == VerificationMode.PORTAL_CACHED:
                domain = v.field
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
                            input_ids=[v.id],
                            source_mode="PORTAL_CACHED",
                            metadata_json={"field": v.field},
                        )
                    )
                    continue

                obs_at = None
                if isinstance(obs_at_raw, str):
                    try:
                        obs_at = datetime.fromisoformat(obs_at_raw.replace("Z", "+00:00"))
                    except Exception:
                        pass
                elif isinstance(obs_at_raw, datetime):
                    obs_at = obs_at_raw

                if obs_at:
                    max_days = policy.get(domain, policy.get("gst", 30))
                    age_days = (v.checked_at - obs_at).days
                    if age_days > max_days:
                        candidates.append(
                            RiskSignalCandidate(
                                severity=RiskSeverity.LOW,
                                signal_type="STALE_CACHED_RECORD",
                                title="Stale Cached Verification Record",
                                description=f"Cached record for '{v.field}' observed {age_days} days ago exceeds operational policy threshold of {max_days} days.",
                                reason_code="STALE_CACHED_RECORD",
                                input_ids=[v.id],
                                source_mode="PORTAL_CACHED",
                                metadata_json={"field": v.field, "age_days": age_days, "policy_threshold_days": max_days},
                            )
                        )
