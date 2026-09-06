from abc import ABC, abstractmethod
from typing import Any
from app.core.config import settings
from app.schemas.canonical import (
    VerificationMode,
    VerificationResultRead,
    VerificationSource,
)
from app.verification.providers.base import BaseVerificationProvider
from app.verification.providers.demo_providers import DemoProvider
from app.verification.providers.document_providers import DocumentVerificationProvider
from app.verification.providers.live_providers import GenericLiveProvider
from app.verification.providers.portal_cached_providers import PortalCachedProvider


class BaseVerificationAdapter(ABC):
    """Abstract base adapter delegating verification execution to selected provider."""

    @property
    @abstractmethod
    def domain(self) -> str:
        pass

    def resolve_mode(self, bidder_data: dict[str, Any] | None = None) -> VerificationMode:
        # Check if bidder data specifies a test mode override (case-insensitive)
        if bidder_data and "verification_mode" in bidder_data and bidder_data["verification_mode"]:
            val = bidder_data["verification_mode"]
            if isinstance(val, str):
                val = val.upper()
            return VerificationMode(val)

        if bidder_data and "simulated_mode" in bidder_data and bidder_data["simulated_mode"]:
            return VerificationMode.DEMO

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
    """GST verification adapter selecting provider based on configuration."""

    @property
    def domain(self) -> str:
        return "gst"

    def get_provider(self, mode: VerificationMode) -> BaseVerificationProvider:
        if mode == VerificationMode.LIVE:
            return GenericLiveProvider("gst", VerificationSource.GST_AUTHORIZED_API, settings.GST_API_BASE_URL, settings.GST_API_KEY)
        elif mode == VerificationMode.PORTAL_CACHED:
            return PortalCachedProvider("gst", VerificationSource.GST_PORTAL_VERIFIED_CACHE)
        else:
            return DemoProvider("gst", VerificationSource.GST_DEMO_DATA)


class UdyamVerificationAdapter(BaseVerificationAdapter):
    """Udyam MSME verification adapter selecting provider based on configuration."""

    @property
    def domain(self) -> str:
        return "udyam"

    def get_provider(self, mode: VerificationMode) -> BaseVerificationProvider:
        if mode == VerificationMode.LIVE:
            return GenericLiveProvider("udyam", VerificationSource.UDYAM_AUTHORIZED_API, settings.UDYAM_API_BASE_URL, settings.UDYAM_API_KEY)
        elif mode == VerificationMode.PORTAL_CACHED:
            return PortalCachedProvider("udyam", VerificationSource.UDYAM_PORTAL_VERIFIED_CACHE)
        else:
            return DemoProvider("udyam", VerificationSource.UDYAM_DEMO_DATA)


class MCAVerificationAdapter(BaseVerificationAdapter):
    """MCA corporate verification adapter selecting provider based on configuration."""

    @property
    def domain(self) -> str:
        return "mca"

    def get_provider(self, mode: VerificationMode) -> BaseVerificationProvider:
        if mode == VerificationMode.LIVE:
            return GenericLiveProvider("mca", VerificationSource.MCA_AUTHORIZED_API, settings.MCA_API_BASE_URL, settings.MCA_API_KEY)
        elif mode == VerificationMode.PORTAL_CACHED:
            return PortalCachedProvider("mca", VerificationSource.MCA_PUBLIC_MASTER_DATA_CACHE)
        else:
            return DemoProvider("mca", VerificationSource.MCA_DEMO_DATA)


class EPFOVerificationAdapter(BaseVerificationAdapter):
    """EPFO labor compliance verification adapter selecting provider based on configuration."""

    @property
    def domain(self) -> str:
        return "epfo"

    def get_provider(self, mode: VerificationMode) -> BaseVerificationProvider:
        if mode == VerificationMode.LIVE:
            return GenericLiveProvider("epfo", VerificationSource.EPFO_AUTHORIZED_CHANNEL, settings.EPFO_API_BASE_URL, settings.EPFO_API_KEY)
        elif mode == VerificationMode.DOCUMENT:
            return DocumentVerificationProvider("epfo", VerificationSource.EPFO_DOCUMENT_VERIFICATION)
        elif mode == VerificationMode.PORTAL_CACHED:
            return PortalCachedProvider("epfo", VerificationSource.EPFO_DOCUMENT_VERIFICATION)
        else:
            return DemoProvider("epfo", VerificationSource.EPFO_DEMO_DATA)


class ESICVerificationAdapter(BaseVerificationAdapter):
    """ESIC labor compliance verification adapter selecting provider based on configuration."""

    @property
    def domain(self) -> str:
        return "esic"

    def get_provider(self, mode: VerificationMode) -> BaseVerificationProvider:
        if mode == VerificationMode.LIVE:
            return GenericLiveProvider("esic", VerificationSource.ESIC_AUTHORIZED_CHANNEL, settings.ESIC_API_BASE_URL, settings.ESIC_API_KEY)
        elif mode == VerificationMode.DOCUMENT:
            return DocumentVerificationProvider("esic", VerificationSource.ESIC_DOCUMENT_VERIFICATION)
        elif mode == VerificationMode.PORTAL_CACHED:
            return PortalCachedProvider("esic", VerificationSource.ESIC_DOCUMENT_VERIFICATION)
        else:
            return DemoProvider("esic", VerificationSource.ESIC_DEMO_DATA)


class BlacklistVerificationAdapter(BaseVerificationAdapter):
    """Blacklist / Debarment verification adapter selecting provider based on configuration."""

    @property
    def domain(self) -> str:
        return "blacklist"

    def get_provider(self, mode: VerificationMode) -> BaseVerificationProvider:
        if mode == VerificationMode.LIVE:
            return GenericLiveProvider("blacklist", VerificationSource.BLACKLIST_AUTHORIZED_SOURCE, settings.BLACKLIST_API_BASE_URL, settings.BLACKLIST_API_KEY)
        elif mode == VerificationMode.PORTAL_CACHED:
            return PortalCachedProvider("blacklist", VerificationSource.BLACKLIST_PORTAL_VERIFIED_CACHE)
        else:
            return DemoProvider("blacklist", VerificationSource.BLACKLIST_DEMO_DATA)
