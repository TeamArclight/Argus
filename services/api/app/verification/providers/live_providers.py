from datetime import datetime, timezone
import uuid
from typing import Any
import httpx
from app.core.config import settings
from app.schemas.canonical import (
    VerificationMode,
    VerificationResultRead,
    VerificationSource,
    VerificationStatus,
)
from app.verification.providers.base import BaseVerificationProvider


class GenericLiveProvider(BaseVerificationProvider):
    """Generic LIVE mode provider performing HTTP API calls to authorized government endpoints."""

    def __init__(self, domain: str, source: VerificationSource, api_base_url: str | None, api_key: str | None):
        self.domain = domain
        self._source = source
        self.api_base_url = api_base_url
        self.api_key = api_key

    @property
    def mode(self) -> VerificationMode:
        return VerificationMode.LIVE

    @property
    def source(self) -> VerificationSource:
        return self._source

    async def verify(
        self, bidder_data: dict[str, Any], field: str
    ) -> VerificationResultRead:
        now = datetime.now(timezone.utc)
        ver_id = str(uuid.uuid4())
        bidder_id = bidder_data.get("id", "UNKNOWN_BIDDER")

        # Check configuration presence
        if not self.api_base_url or not self.api_key:
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_id,
                field=field,
                claimed_value=bidder_data.get(self.domain) or bidder_data.get("bidder_name"),
                verified_value=None,
                status=VerificationStatus.UNAVAILABLE,
                source=self.source,
                mode=self.mode,
                checked_at=now,
                error_message=f"Live {self.domain.upper()} provider is not configured. Missing API base URL or credentials.",
            )

        # Build endpoint URL and headers
        url = f"{self.api_base_url.rstrip('/')}/verify/{self.domain}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "X-API-Key": self.api_key,
            "Accept": "application/json",
            "User-Agent": "ARGUS-Procurement-Platform/1.0",
        }

        params = {
            "field": field,
            "identifier": bidder_data.get(self.domain) or bidder_data.get("bidder_name"),
            "gstin": bidder_data.get("gstin"),
            "pan": bidder.get("pan") if (bidder := bidder_data) else None,
        }

        timeout = httpx.Timeout(settings.REQUEST_TIMEOUT_SECONDS)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(url, headers=headers, params=params)

                if resp.status_code == 401:
                    return VerificationResultRead(
                        id=ver_id,
                        bidder_id=bidder_id,
                        field=field,
                        claimed_value=params["identifier"],
                        verified_value=None,
                        status=VerificationStatus.SERVICE_ERROR,
                        source=self.source,
                        mode=self.mode,
                        checked_at=now,
                        error_message="Unauthorized: Live API credential rejected by authorized gateway.",
                    )

                if resp.status_code == 403:
                    return VerificationResultRead(
                        id=ver_id,
                        bidder_id=bidder_id,
                        field=field,
                        claimed_value=params["identifier"],
                        verified_value=None,
                        status=VerificationStatus.SERVICE_ERROR,
                        source=self.source,
                        mode=self.mode,
                        checked_at=now,
                        error_message="Forbidden: Access denied to live registry endpoint.",
                    )

                if resp.status_code == 404:
                    return VerificationResultRead(
                        id=ver_id,
                        bidder_id=bidder_id,
                        field=field,
                        claimed_value=params["identifier"],
                        verified_value=None,
                        status=VerificationStatus.UNVERIFIED,
                        source=self.source,
                        mode=self.mode,
                        checked_at=now,
                        error_message="Entity identifier not found in live official database.",
                    )

                if resp.status_code in (429, 500, 502, 503, 504):
                    return VerificationResultRead(
                        id=ver_id,
                        bidder_id=bidder_id,
                        field=field,
                        claimed_value=params["identifier"],
                        verified_value=None,
                        status=VerificationStatus.SERVICE_ERROR,
                        source=self.source,
                        mode=self.mode,
                        checked_at=now,
                        error_message=f"Live official service error (HTTP {resp.status_code}).",
                    )

                resp.raise_for_status()
                data = resp.json()

                verified_val = data.get("verified_value", data)
                is_match = data.get("match", True)
                ref = data.get("reference") or f"LIVE-{self.domain.upper()}-{uuid.uuid4().hex[:6].upper()}"

                status = VerificationStatus.VERIFIED if is_match else VerificationStatus.MISMATCH

                return VerificationResultRead(
                    id=ver_id,
                    bidder_id=bidder_id,
                    field=field,
                    claimed_value=params["identifier"],
                    verified_value=verified_val,
                    status=status,
                    source=self.source,
                    mode=self.mode,
                    checked_at=now,
                    verification_reference=ref,
                )

        except httpx.TimeoutException:
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_id,
                field=field,
                claimed_value=params["identifier"],
                verified_value=None,
                status=VerificationStatus.TIMEOUT,
                source=self.source,
                mode=self.mode,
                checked_at=now,
                error_message=f"Live {self.domain.upper()} portal request timed out after {settings.REQUEST_TIMEOUT_SECONDS}s.",
            )

        except (httpx.RequestError, ValueError) as exc:
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_id,
                field=field,
                claimed_value=params["identifier"],
                verified_value=None,
                status=VerificationStatus.SERVICE_ERROR,
                source=self.source,
                mode=self.mode,
                checked_at=now,
                error_message=f"Live provider transport/parse failure: {exc}",
            )
