from abc import ABC, abstractmethod
from typing import Any
from app.schemas.canonical import (
    VerificationMode,
    VerificationResultRead,
    VerificationSource,
)


class BaseVerificationProvider(ABC):
    """Interface protocol for domain-specific verification providers."""

    @property
    @abstractmethod
    def mode(self) -> VerificationMode:
        """Returns the mode of this provider (LIVE, PORTAL_CACHED, DEMO, DOCUMENT)."""
        pass

    @property
    @abstractmethod
    def source(self) -> VerificationSource:
        """Returns the truthful provenance source enum for this provider."""
        pass

    @abstractmethod
    async def verify(
        self, bidder_data: dict[str, Any], field: str
    ) -> VerificationResultRead:
        """Executes verification and returns structured result with truthful mode and source."""
        pass
