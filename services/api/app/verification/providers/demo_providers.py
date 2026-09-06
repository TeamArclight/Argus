from datetime import datetime, timezone
import uuid
from typing import Any
from app.schemas.canonical import (
    VerificationMode,
    VerificationResultRead,
    VerificationSource,
    VerificationStatus,
)
from app.verification.providers.base import BaseVerificationProvider


class DemoProvider(BaseVerificationProvider):
    """Deterministic SIH demo mode provider supporting flagship profiles (ALPHA, BHARAT, CREST)."""

    def __init__(self, domain: str, source: VerificationSource):
        self.domain = domain
        self._source = source

    @property
    def mode(self) -> VerificationMode:
        return VerificationMode.DEMO

    @property
    def source(self) -> VerificationSource:
        return self._source

    async def verify(
        self, bidder_data: dict[str, Any], field: str
    ) -> VerificationResultRead:
        now = datetime.now(timezone.utc)
        ver_id = str(uuid.uuid4())
        bidder_id = bidder_data.get("id", "UNKNOWN_BIDDER")
        bidder_name = bidder_data.get("bidder_name", "").upper()
        simulated_outcome = (bidder_data.get("simulated_outcome") or "success").lower()

        # Handle specific simulated edge cases if passed in tests
        if simulated_outcome == "timeout":
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_id,
                field=field,
                claimed_value=bidder_data.get("gstin") or bidder_name,
                verified_value=None,
                status=VerificationStatus.TIMEOUT,
                source=self.source,
                mode=self.mode,
                checked_at=now,
                error_message=f"Simulated timeout in DEMO mode for domain '{self.domain}'",
            )

        if simulated_outcome == "unavailable":
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_id,
                field=field,
                claimed_value=bidder_data.get("gstin") or bidder_name,
                verified_value=None,
                status=VerificationStatus.UNAVAILABLE,
                source=self.source,
                mode=self.mode,
                checked_at=now,
                error_message=f"Simulated unavailability in DEMO mode for domain '{self.domain}'",
            )

        # Flagship Profile 1: ALPHA (Full Compliance Match)
        if "ALPHA" in bidder_name or "ACME" in bidder_name:
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_id,
                field=field,
                claimed_value=bidder_data.get("gstin") or bidder_data.get("udyam_number") or bidder_name,
                verified_value={
                    "profile": "ALPHA_FLAGSHIP",
                    "status": "ACTIVE",
                    "verified_entity": bidder_data.get("bidder_name"),
                    "valid": True,
                },
                status=VerificationStatus.VERIFIED,
                source=self.source,
                mode=self.mode,
                checked_at=now,
                verification_reference=f"DEMO-ALPHA-{self.domain.upper()}-8819",
            )

        # Flagship Profile 2: BHARAT (Verified Turnover Lower than Claimed)
        if "BHARAT" in bidder_name:
            if field == "financial.average_annual_turnover":
                return VerificationResultRead(
                    id=ver_id,
                    bidder_id=bidder_id,
                    field=field,
                    claimed_value=150000000,
                    verified_value=85000000,  # 8.5 Cr verified vs 15 Cr claimed -> lower than 10 Cr requirement
                    status=VerificationStatus.MISMATCH,
                    source=self.source,
                    mode=self.mode,
                    checked_at=now,
                    verification_reference="DEMO-BHARAT-TURNOVER-LOWER",
                    error_message="CA verified turnover (INR 8.5 Cr) is lower than claimed (INR 15 Cr)",
                )

            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_id,
                field=field,
                claimed_value=bidder_data.get("gstin") or bidder_name,
                verified_value={
                    "profile": "BHARAT_FLAGSHIP",
                    "status": "ACTIVE",
                    "verified_entity": bidder_data.get("bidder_name"),
                },
                status=VerificationStatus.VERIFIED,
                source=self.source,
                mode=self.mode,
                checked_at=now,
                verification_reference=f"DEMO-BHARAT-{self.domain.upper()}-1024",
            )

        # Flagship Profile 3: CREST (Suspicious OEM / Conflicting Verification -> REVIEW_REQUIRED)
        if "CREST" in bidder_name or "MALICIOUS" in bidder_name:
            if self.domain == "blacklist" or "MALICIOUS" in bidder_name:
                return VerificationResultRead(
                    id=ver_id,
                    bidder_id=bidder_id,
                    field=field,
                    claimed_value=False,
                    verified_value={"debarred": True, "reason": "Debarred by CPP Portal for submission of forged OEM certificate"},
                    status=VerificationStatus.MISMATCH,
                    source=self.source,
                    mode=self.mode,
                    checked_at=now,
                    verification_reference="DEMO-CREST-DEBARRED",
                    error_message="Listed in Central Debarment Directory for OEM fraud",
                )

            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_id,
                field=field,
                claimed_value=bidder_data.get("gstin") or bidder_name,
                verified_value={
                    "profile": "CREST_FLAGSHIP",
                    "status": "SUSPICIOUS_OEM_MAINTENANCE",
                    "conflict_detected": True,
                },
                status=VerificationStatus.MISMATCH,
                source=self.source,
                mode=self.mode,
                checked_at=now,
                verification_reference=f"DEMO-CREST-{self.domain.upper()}-CONFLICT",
                error_message="Conflicting OEM registration entity detected",
            )

        # Default fallback demo verification
        return VerificationResultRead(
            id=ver_id,
            bidder_id=bidder_id,
            field=field,
            claimed_value=bidder_data.get("gstin") or bidder_name,
            verified_value={
                "status": "ACTIVE",
                "verified_entity": bidder_data.get("bidder_name"),
            },
            status=VerificationStatus.VERIFIED,
            source=self.source,
            mode=self.mode,
            checked_at=now,
            verification_reference=f"DEMO-GENERIC-{self.domain.upper()}-{uuid.uuid4().hex[:6].upper()}",
        )
