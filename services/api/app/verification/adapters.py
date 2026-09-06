from abc import ABC, abstractmethod
from datetime import datetime, timezone
import uuid
from typing import Any
from app.schemas.canonical import (
    VerificationResultRead,
    VerificationSource,
    VerificationStatus,
)


class BaseVerificationAdapter(ABC):
    """Abstract base protocol for government registry verification adapters."""

    @property
    @abstractmethod
    def source(self) -> VerificationSource:
        pass

    @abstractmethod
    async def verify(
        self, bidder_data: dict[str, Any], field: str
    ) -> VerificationResultRead:
        pass


class GSTVerificationAdapter(BaseVerificationAdapter):
    """Mock verification adapter for GST registry."""

    @property
    def source(self) -> VerificationSource:
        return VerificationSource.GST_MOCK

    async def verify(
        self, bidder_data: dict[str, Any], field: str
    ) -> VerificationResultRead:
        gstin = bidder_data.get("gstin")
        simulated_mode = bidder_data.get("simulated_mode", "success")
        now = datetime.now(timezone.utc)
        ver_id = str(uuid.uuid4())

        if simulated_mode == "timeout":
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_data.get("id", "UNKNOWN"),
                field=field,
                claimed_value=gstin,
                verified_value=None,
                status=VerificationStatus.TIMEOUT,
                source=self.source,
                checked_at=now,
                error_message="GST portal request timed out after 30s",
            )

        if simulated_mode == "unavailable":
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_data.get("id", "UNKNOWN"),
                field=field,
                claimed_value=gstin,
                verified_value=None,
                status=VerificationStatus.UNAVAILABLE,
                source=self.source,
                checked_at=now,
                error_message="GST API endpoint maintenance mode",
            )

        if not gstin or len(gstin) != 15:
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_data.get("id", "UNKNOWN"),
                field=field,
                claimed_value=gstin,
                verified_value=None,
                status=VerificationStatus.UNVERIFIED,
                source=self.source,
                checked_at=now,
                error_message="Invalid GSTIN format provided",
            )

        if simulated_mode == "mismatch" or gstin.endswith("99"):
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_data.get("id", "UNKNOWN"),
                field=field,
                claimed_value=gstin,
                verified_value="MISMATCHED_LEGAL_ENTITY",
                status=VerificationStatus.MISMATCH,
                source=self.source,
                checked_at=now,
                verification_reference=f"GST-REF-{gstin[:5]}",
                error_message="Claimed GSTIN entity name does not match tax records",
            )

        # Standard successful verification
        return VerificationResultRead(
            id=ver_id,
            bidder_id=bidder_data.get("id", "UNKNOWN"),
            field=field,
            claimed_value=gstin,
            verified_value={"gstin": gstin, "status": "ACTIVE", "entity": bidder_data.get("bidder_name")},
            status=VerificationStatus.VERIFIED,
            source=self.source,
            checked_at=now,
            verification_reference=f"GST-REF-{gstin[:6]}",
        )


class UdyamVerificationAdapter(BaseVerificationAdapter):
    """Mock verification adapter for MSME Udyam portal."""

    @property
    def source(self) -> VerificationSource:
        return VerificationSource.UDYAM_MOCK

    async def verify(
        self, bidder_data: dict[str, Any], field: str
    ) -> VerificationResultRead:
        udyam = bidder_data.get("udyam_number")
        simulated_mode = bidder_data.get("simulated_mode", "success")
        now = datetime.now(timezone.utc)
        ver_id = str(uuid.uuid4())

        if simulated_mode == "unavailable":
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_data.get("id", "UNKNOWN"),
                field=field,
                claimed_value=udyam,
                verified_value=None,
                status=VerificationStatus.UNAVAILABLE,
                source=self.source,
                checked_at=now,
                error_message="Udyam portal registration service unavailable",
            )

        if not udyam:
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_data.get("id", "UNKNOWN"),
                field=field,
                claimed_value=None,
                verified_value=None,
                status=VerificationStatus.UNVERIFIED,
                source=self.source,
                checked_at=now,
                error_message="No Udyam registration number provided",
            )

        if simulated_mode == "mismatch":
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_data.get("id", "UNKNOWN"),
                field=field,
                claimed_value=udyam,
                verified_value="EXPIRED_CATEGORY",
                status=VerificationStatus.MISMATCH,
                source=self.source,
                checked_at=now,
                error_message="Udyam registration category lapsed",
            )

        return VerificationResultRead(
            id=ver_id,
            bidder_id=bidder_data.get("id", "UNKNOWN"),
            field=field,
            claimed_value=udyam,
            verified_value={"udyam_number": udyam, "category": "MEDIUM_ENTERPRISE", "valid": True},
            status=VerificationStatus.VERIFIED,
            source=self.source,
            checked_at=now,
            verification_reference=f"UDYAM-REF-{uuid.uuid4().hex[:6].upper()}",
        )


class MCAVerificationAdapter(BaseVerificationAdapter):
    """Mock verification adapter for Ministry of Corporate Affairs (MCA)."""

    @property
    def source(self) -> VerificationSource:
        return VerificationSource.MCA_MOCK

    async def verify(
        self, bidder_data: dict[str, Any], field: str
    ) -> VerificationResultRead:
        cin = bidder_data.get("cin")
        now = datetime.now(timezone.utc)
        ver_id = str(uuid.uuid4())

        if not cin:
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_data.get("id", "UNKNOWN"),
                field=field,
                claimed_value=None,
                verified_value=None,
                status=VerificationStatus.UNVERIFIED,
                source=self.source,
                checked_at=now,
                error_message="CIN not provided for corporate entity",
            )

        return VerificationResultRead(
            id=ver_id,
            bidder_id=bidder_data.get("id", "UNKNOWN"),
            field=field,
            claimed_value=cin,
            verified_value={"cin": cin, "status": "ACTIVE_COMPANY", "paid_up_capital": 50000000},
            status=VerificationStatus.VERIFIED,
            source=self.source,
            checked_at=now,
            verification_reference=f"MCA-REF-{cin[:8]}",
        )


class EPFOVerificationAdapter(BaseVerificationAdapter):
    """Mock verification adapter for Employees' Provident Fund Organisation (EPFO)."""

    @property
    def source(self) -> VerificationSource:
        return VerificationSource.EPFO_MOCK

    async def verify(
        self, bidder_data: dict[str, Any], field: str
    ) -> VerificationResultRead:
        pan = bidder_data.get("pan")
        now = datetime.now(timezone.utc)
        ver_id = str(uuid.uuid4())

        return VerificationResultRead(
            id=ver_id,
            bidder_id=bidder_data.get("id", "UNKNOWN"),
            field=field,
            claimed_value=pan,
            verified_value={"active_subscribers": 142, "compliance_status": "REGULAR_PAYER"},
            status=VerificationStatus.VERIFIED,
            source=self.source,
            checked_at=now,
            verification_reference=f"EPFO-REF-{uuid.uuid4().hex[:6].upper()}",
        )


class BlacklistVerificationAdapter(BaseVerificationAdapter):
    """Mock verification adapter for Debarred / Blacklisted Supplier databases."""

    @property
    def source(self) -> VerificationSource:
        return VerificationSource.BLACKLIST_MOCK

    async def verify(
        self, bidder_data: dict[str, Any], field: str
    ) -> VerificationResultRead:
        bidder_name = bidder_data.get("bidder_name", "")
        pan = bidder_data.get("pan", "")
        simulated_mode = bidder_data.get("simulated_mode", "clean")
        now = datetime.now(timezone.utc)
        ver_id = str(uuid.uuid4())

        is_blacklisted = (simulated_mode == "blacklisted") or ("MALICIOUS" in bidder_name.upper())

        if is_blacklisted:
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_data.get("id", "UNKNOWN"),
                field=field,
                claimed_value=False,
                verified_value={"blacklisted": True, "reason": "Debarred by CPP Portal until 2027"},
                status=VerificationStatus.MISMATCH,
                source=self.source,
                checked_at=now,
                verification_reference=f"BLK-ENTRY-{uuid.uuid4().hex[:6].upper()}",
                error_message="Bidder is listed in Central Public Procurement Debarment Database",
            )

        return VerificationResultRead(
            id=ver_id,
            bidder_id=bidder_data.get("id", "UNKNOWN"),
            field=field,
            claimed_value=False,
            verified_value={"blacklisted": False},
            status=VerificationStatus.VERIFIED,
            source=self.source,
            checked_at=now,
            verification_reference="BLK-CLEAR",
        )
