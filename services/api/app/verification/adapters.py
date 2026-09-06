from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any
import uuid
from app.core.config import settings
from app.schemas.canonical import (
    VerificationMode,
    VerificationResultRead,
    VerificationSource,
    VerificationStatus,
)
from app.verification.providers.base import BaseVerificationProvider
from app.verification.providers.demo_providers import DemoProvider
from app.verification.providers.document_providers import DocumentVerificationProvider
from app.verification.providers.live_providers import (
    BlacklistLiveProvider,
    EPFOLiveProvider,
    ESICLiveProvider,
    GSTLiveProvider,
    MCALiveProvider,
    UdyamLiveProvider,
)
from app.verification.providers.portal_cached_providers import PortalCachedProvider


class UnsupportedModeProvider(BaseVerificationProvider):
    """Provider returning structured error when an unsupported mode is selected for a domain."""

    def __init__(self, domain: str, attempted_mode: Any):
        self.domain = domain
        self.attempted_mode = attempted_mode

    @property
    def mode(self) -> VerificationMode:
        return self.attempted_mode if isinstance(self.attempted_mode, VerificationMode) else VerificationMode.LIVE

    @property
    def source(self) -> VerificationSource:
        return getattr(VerificationSource, f"{self.domain.upper()}_DEMO_DATA", VerificationSource.GST_DEMO_DATA)

    async def verify(self, bidder_data: dict[str, Any], field: str) -> VerificationResultRead:
        now = datetime.now(timezone.utc)
        return VerificationResultRead(
            id=str(uuid.uuid4()),
            bidder_id=bidder_data.get("id", "UNKNOWN_BIDDER"),
            field=field,
            claimed_value=bidder_data.get("identifier"),
            verified_value=None,
            status=VerificationStatus.UNAVAILABLE,
            source=self.source,
            mode=self.mode,
            checked_at=now,
            error_message=f"Unsupported verification mode '{self.attempted_mode}' for domain '{self.domain}'.",
        )


class BaseVerificationAdapter(ABC):
    """Abstract base adapter delegating verification execution to selected provider."""

    @property
    @abstractmethod
    def domain(self) -> str:
        pass

    def resolve_mode(self, bidder_data: dict[str, Any] | None = None) -> VerificationMode:
        # Check if bidder data specifies an explicit verification_mode test override
        if bidder_data and "verification_mode" in bidder_data and bidder_data["verification_mode"]:
            val = bidder_data["verification_mode"]
            if isinstance(val, str):
                val = val.upper()
            return VerificationMode(val)

        return settings.get_mode_for_domain(self.domain)

    @abstractmethod
    def get_provider(self, mode: VerificationMode) -> BaseVerificationProvider:
        pass

    async def verify(
        self, bidder_data: dict[str, Any], field: str
    ) -> VerificationResultRead:
        active_mode = self.resolve_mode(bidder_data)
        provider = self.get_provider(active_mode)
        return await provider.verify(bidder_data, field)


class GSTVerificationAdapter(BaseVerificationAdapter):
    """GST verification adapter supporting LIVE, PORTAL_CACHED, and DEMO modes."""

    @property
    def domain(self) -> str:
        return "gst"

    def get_provider(self, mode: VerificationMode) -> BaseVerificationProvider:
        if mode == VerificationMode.LIVE:
            return GSTLiveProvider(VerificationSource.GST_AUTHORIZED_API)
        elif mode == VerificationMode.PORTAL_CACHED:
            return PortalCachedProvider("gst", VerificationSource.GST_PORTAL_VERIFIED_CACHE)
        elif mode == VerificationMode.DEMO:
            return DemoProvider("gst", VerificationSource.GST_DEMO_DATA)
        else:
            return UnsupportedModeProvider("gst", mode)


class UdyamVerificationAdapter(BaseVerificationAdapter):
    """Udyam MSME verification adapter supporting LIVE, PORTAL_CACHED, and DEMO modes."""

    @property
    def domain(self) -> str:
        return "udyam"

    def get_provider(self, mode: VerificationMode) -> BaseVerificationProvider:
        if mode == VerificationMode.LIVE:
            return UdyamLiveProvider(VerificationSource.UDYAM_AUTHORIZED_API)
        elif mode == VerificationMode.PORTAL_CACHED:
            return PortalCachedProvider("udyam", VerificationSource.UDYAM_PORTAL_VERIFIED_CACHE)
        elif mode == VerificationMode.DEMO:
            return DemoProvider("udyam", VerificationSource.UDYAM_DEMO_DATA)
        else:
            return UnsupportedModeProvider("udyam", mode)


class MCAVerificationAdapter(BaseVerificationAdapter):
    """MCA corporate verification adapter supporting LIVE, PORTAL_CACHED, and DEMO modes."""

    @property
    def domain(self) -> str:
        return "mca"

    def get_provider(self, mode: VerificationMode) -> BaseVerificationProvider:
        if mode == VerificationMode.LIVE:
            return MCALiveProvider(VerificationSource.MCA_AUTHORIZED_API)
        elif mode == VerificationMode.PORTAL_CACHED:
            return PortalCachedProvider("mca", VerificationSource.MCA_PUBLIC_MASTER_DATA_CACHE)
        elif mode == VerificationMode.DEMO:
            return DemoProvider("mca", VerificationSource.MCA_DEMO_DATA)
        else:
            return UnsupportedModeProvider("mca", mode)


class EPFOVerificationAdapter(BaseVerificationAdapter):
    """EPFO labor compliance verification adapter supporting LIVE, PORTAL_CACHED, DOCUMENT, and DEMO modes."""

    @property
    def domain(self) -> str:
        return "epfo"

    def get_provider(self, mode: VerificationMode) -> BaseVerificationProvider:
        if mode == VerificationMode.LIVE:
            return EPFOLiveProvider(VerificationSource.EPFO_AUTHORIZED_CHANNEL)
        elif mode == VerificationMode.PORTAL_CACHED:
            return PortalCachedProvider("epfo", VerificationSource.EPFO_DOCUMENT_VERIFICATION)
        elif mode == VerificationMode.DOCUMENT:
            return DocumentVerificationProvider("epfo", VerificationSource.EPFO_DOCUMENT_VERIFICATION)
        elif mode == VerificationMode.DEMO:
            return DemoProvider("epfo", VerificationSource.EPFO_DEMO_DATA)
        else:
            return UnsupportedModeProvider("epfo", mode)


class ESICVerificationAdapter(BaseVerificationAdapter):
    """ESIC labor compliance verification adapter supporting LIVE, PORTAL_CACHED, DOCUMENT, and DEMO modes."""

    @property
    def domain(self) -> str:
        return "esic"

    def get_provider(self, mode: VerificationMode) -> BaseVerificationProvider:
        if mode == VerificationMode.LIVE:
            return ESICLiveProvider(VerificationSource.ESIC_AUTHORIZED_CHANNEL)
        elif mode == VerificationMode.PORTAL_CACHED:
            return PortalCachedProvider("esic", VerificationSource.ESIC_DOCUMENT_VERIFICATION)
        elif mode == VerificationMode.DOCUMENT:
            return DocumentVerificationProvider("esic", VerificationSource.ESIC_DOCUMENT_VERIFICATION)
        elif mode == VerificationMode.DEMO:
            return DemoProvider("esic", VerificationSource.ESIC_DEMO_DATA)
        else:
            return UnsupportedModeProvider("esic", mode)


class BlacklistVerificationAdapter(BaseVerificationAdapter):
    """Blacklist / Debarment verification adapter supporting LIVE, PORTAL_CACHED, and DEMO modes."""

    @property
    def domain(self) -> str:
        return "blacklist"

    def get_provider(self, mode: VerificationMode) -> BaseVerificationProvider:
        if mode == VerificationMode.LIVE:
            return BlacklistLiveProvider(VerificationSource.BLACKLIST_AUTHORIZED_SOURCE)
        elif mode == VerificationMode.PORTAL_CACHED:
            return PortalCachedProvider("blacklist", VerificationSource.BLACKLIST_PORTAL_VERIFIED_CACHE)
        elif mode == VerificationMode.DEMO:
            return DemoProvider("blacklist", VerificationSource.BLACKLIST_DEMO_DATA)
        else:
            return UnsupportedModeProvider("blacklist", mode)
