from datetime import datetime, timezone
from typing import Any
import uuid
import httpx

from app.core.config import settings
from app.schemas.canonical import (
    AuthMode,
    VerificationMode,
    VerificationResultRead,
    VerificationSource,
    VerificationStatus,
)
from app.verification.providers.base import BaseVerificationProvider


class LiveHTTPClient:
    """Shared HTTP client infrastructure for executing live registry API calls with configured authentication."""

    @staticmethod
    async def request(
        url: str,
        method: str,
        auth_mode: AuthMode,
        api_key: str | None,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
        timeout_seconds: float = 10.0,
    ) -> tuple[int | None, dict[str, Any] | None, VerificationStatus | None, str | None]:
        """
        Executes HTTP request with exact configured authentication mechanism and status code handling.
        Returns tuple of (status_code, response_json, error_status, error_message).
        """
        headers: dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": "ARGUS-Procurement-Platform/1.0",
        }

        # Enforce strict auth mechanism separation - send ONLY configured auth header
        if api_key:
            if auth_mode == AuthMode.BEARER:
                headers["Authorization"] = f"Bearer {api_key}"
            elif auth_mode == AuthMode.X_API_KEY:
                headers["X-API-Key"] = api_key
            elif auth_mode == AuthMode.OAUTH_CLIENT_CREDENTIALS:
                headers["Authorization"] = f"Bearer {api_key}"
            # AuthMode.NONE sends no auth headers

        timeout = httpx.Timeout(timeout_seconds)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=params,
                    json=json_data,
                )

                if resp.status_code == 401:
                    return (
                        401,
                        None,
                        VerificationStatus.SERVICE_ERROR,
                        "Unauthorized: Live API credential rejected by authorized gateway.",
                    )

                if resp.status_code == 403:
                    return (
                        403,
                        None,
                        VerificationStatus.SERVICE_ERROR,
                        "Forbidden: Access denied to live registry endpoint.",
                    )

                if resp.status_code == 404:
                    return (
                        404,
                        None,
                        VerificationStatus.UNVERIFIED,
                        "Entity identifier not found in live official database.",
                    )

                if resp.status_code in (429, 500, 502, 503, 504):
                    return (
                        resp.status_code,
                        None,
                        VerificationStatus.SERVICE_ERROR,
                        f"Live official service error (HTTP {resp.status_code}).",
                    )

                resp.raise_for_status()
                try:
                    data = resp.json()
                    return resp.status_code, data, None, None
                except Exception as parse_err:
                    return (
                        resp.status_code,
                        None,
                        VerificationStatus.SERVICE_ERROR,
                        f"Failed to parse live JSON response: {parse_err}",
                    )

        except httpx.TimeoutException:
            return (
                None,
                None,
                VerificationStatus.TIMEOUT,
                f"Live portal request timed out after {timeout_seconds}s.",
            )

        except (httpx.RequestError, Exception) as exc:
            return (
                None,
                None,
                VerificationStatus.SERVICE_ERROR,
                f"Live provider transport failure: {exc}",
            )


class GSTLiveProvider(BaseVerificationProvider):
    """Authorized GST live registry verification provider."""

    def __init__(self, source: VerificationSource = VerificationSource.GST_AUTHORIZED_API, api_url: str | None = None, api_key: str | None = None):
        self._source = source
        self.api_url = api_url or settings.GST_API_URL or settings.GST_API_BASE_URL
        self.api_key = api_key or settings.GST_API_KEY
        self.auth_mode = settings.GST_AUTH_MODE

    @property
    def mode(self) -> VerificationMode:
        return VerificationMode.LIVE

    @property
    def source(self) -> VerificationSource:
        return self._source

    async def verify(self, bidder_data: dict[str, Any], field: str) -> VerificationResultRead:
        now = datetime.now(timezone.utc)
        ver_id = str(uuid.uuid4())
        bidder_id = bidder_data.get("id", "UNKNOWN_BIDDER")
        claimed_id = bidder_data.get("gstin") or bidder_data.get("identifier") or bidder_data.get("bidder_name")

        if not self.api_url or not self.api_key:
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_id,
                field=field,
                claimed_value=claimed_id,
                verified_value=None,
                status=VerificationStatus.UNAVAILABLE,
                source=self.source,
                mode=self.mode,
                checked_at=now,
                error_message="Live GST provider is not configured. Missing API URL or API key.",
            )

        if not claimed_id:
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_id,
                field=field,
                claimed_value=None,
                verified_value=None,
                status=VerificationStatus.UNVERIFIED,
                source=self.source,
                mode=self.mode,
                checked_at=now,
                error_message="Missing required identifier (GSTIN) for live verification.",
            )

        status_code, data, err_status, err_msg = await LiveHTTPClient.request(
            url=self.api_url,
            method="GET",
            auth_mode=self.auth_mode,
            api_key=self.api_key,
            params={"identifier": claimed_id, "field": field, "gstin": bidder_data.get("gstin")},
            timeout_seconds=settings.REQUEST_TIMEOUT_SECONDS,
        )

        if err_status is not None:
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_id,
                field=field,
                claimed_value=claimed_id,
                verified_value=None,
                status=err_status,
                source=self.source,
                mode=self.mode,
                checked_at=now,
                error_message=err_msg,
            )

        if not isinstance(data, dict):
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_id,
                field=field,
                claimed_value=claimed_id,
                verified_value=None,
                status=VerificationStatus.SERVICE_ERROR,
                source=self.source,
                mode=self.mode,
                checked_at=now,
                error_message="Invalid response payload format from live GST provider: expected JSON object.",
            )

        ref = data.get("reference") or data.get("transaction_id") or data.get("reference_id")

        if "match" in data and isinstance(data["match"], bool):
            is_match = data["match"]
        else:
            if "status" not in data and "verified_value" not in data and "legal_name" not in data:
                return VerificationResultRead(
                    id=ver_id,
                    bidder_id=bidder_id,
                    field=field,
                    claimed_value=claimed_id,
                    verified_value=None,
                    status=VerificationStatus.SERVICE_ERROR,
                    source=self.source,
                    mode=self.mode,
                    checked_at=now,
                    error_message="Invalid response schema from live GST provider: missing required fields.",
                )
            gst_status = data.get("status") or (data.get("verified_value", {}).get("status") if isinstance(data.get("verified_value"), dict) else None)
            is_match = (gst_status == "ACTIVE") if gst_status else False

        verified_val = data.get("verified_value")
        if verified_val is None:
            if "legal_name" in data or "gstin" in data or "status" in data:
                verified_val = {
                    "gstin": data.get("gstin", claimed_id),
                    "legal_name": data.get("legal_name"),
                    "status": data.get("status"),
                }
            else:
                return VerificationResultRead(
                    id=ver_id,
                    bidder_id=bidder_id,
                    field=field,
                    claimed_value=claimed_id,
                    verified_value=None,
                    status=VerificationStatus.SERVICE_ERROR,
                    source=self.source,
                    mode=self.mode,
                    checked_at=now,
                    error_message="Live GST response missing expected verified_value payload.",
                )

        status = VerificationStatus.VERIFIED if is_match else VerificationStatus.MISMATCH

        return VerificationResultRead(
            id=ver_id,
            bidder_id=bidder_id,
            field=field,
            claimed_value=claimed_id,
            verified_value=verified_val,
            status=status,
            source=self.source,
            mode=self.mode,
            checked_at=now,
            verification_reference=ref,
        )


class UdyamLiveProvider(BaseVerificationProvider):
    """Authorized MSME Udyam live registry verification provider."""

    def __init__(self, source: VerificationSource = VerificationSource.UDYAM_AUTHORIZED_API, api_url: str | None = None, api_key: str | None = None):
        self._source = source
        self.api_url = api_url or settings.UDYAM_API_URL or settings.UDYAM_API_BASE_URL
        self.api_key = api_key or settings.UDYAM_API_KEY
        self.auth_mode = settings.UDYAM_AUTH_MODE

    @property
    def mode(self) -> VerificationMode:
        return VerificationMode.LIVE

    @property
    def source(self) -> VerificationSource:
        return self._source

    async def verify(self, bidder_data: dict[str, Any], field: str) -> VerificationResultRead:
        now = datetime.now(timezone.utc)
        ver_id = str(uuid.uuid4())
        bidder_id = bidder_data.get("id", "UNKNOWN_BIDDER")
        claimed_id = bidder_data.get("udyam_number") or bidder_data.get("identifier") or bidder_data.get("bidder_name")

        if not self.api_url or not self.api_key:
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_id,
                field=field,
                claimed_value=claimed_id,
                verified_value=None,
                status=VerificationStatus.UNAVAILABLE,
                source=self.source,
                mode=self.mode,
                checked_at=now,
                error_message="Live Udyam provider is not configured. Missing API URL or API key.",
            )

        if not claimed_id:
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_id,
                field=field,
                claimed_value=None,
                verified_value=None,
                status=VerificationStatus.UNVERIFIED,
                source=self.source,
                mode=self.mode,
                checked_at=now,
                error_message="Missing required identifier (Udyam Registration Number) for live verification.",
            )

        status_code, data, err_status, err_msg = await LiveHTTPClient.request(
            url=self.api_url,
            method="GET",
            auth_mode=self.auth_mode,
            api_key=self.api_key,
            params={"identifier": claimed_id, "field": field},
            timeout_seconds=settings.REQUEST_TIMEOUT_SECONDS,
        )

        if err_status is not None:
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_id,
                field=field,
                claimed_value=claimed_id,
                verified_value=None,
                status=err_status,
                source=self.source,
                mode=self.mode,
                checked_at=now,
                error_message=err_msg,
            )

        if not isinstance(data, dict):
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_id,
                field=field,
                claimed_value=claimed_id,
                verified_value=None,
                status=VerificationStatus.SERVICE_ERROR,
                source=self.source,
                mode=self.mode,
                checked_at=now,
                error_message="Invalid response payload format from live Udyam provider: expected JSON object.",
            )

        ref = data.get("reference") or data.get("transaction_id") or data.get("reference_id")

        if "match" in data and isinstance(data["match"], bool):
            is_match = data["match"]
        else:
            if "valid" not in data and "verified_value" not in data and "enterprise_type" not in data:
                return VerificationResultRead(
                    id=ver_id,
                    bidder_id=bidder_id,
                    field=field,
                    claimed_value=claimed_id,
                    verified_value=None,
                    status=VerificationStatus.SERVICE_ERROR,
                    source=self.source,
                    mode=self.mode,
                    checked_at=now,
                    error_message="Invalid response schema from live Udyam provider: missing required fields.",
                )
            is_match = bool(data.get("valid", False))

        verified_val = data.get("verified_value")
        if verified_val is None:
            if "enterprise_type" in data or "valid" in data:
                verified_val = {
                    "udyam_number": data.get("udyam_number", claimed_id),
                    "valid": data.get("valid"),
                    "enterprise_type": data.get("enterprise_type"),
                }
            else:
                return VerificationResultRead(
                    id=ver_id,
                    bidder_id=bidder_id,
                    field=field,
                    claimed_value=claimed_id,
                    verified_value=None,
                    status=VerificationStatus.SERVICE_ERROR,
                    source=self.source,
                    mode=self.mode,
                    checked_at=now,
                    error_message="Live Udyam response missing expected verified_value payload.",
                )

        status = VerificationStatus.VERIFIED if is_match else VerificationStatus.MISMATCH

        return VerificationResultRead(
            id=ver_id,
            bidder_id=bidder_id,
            field=field,
            claimed_value=claimed_id,
            verified_value=verified_val,
            status=status,
            source=self.source,
            mode=self.mode,
            checked_at=now,
            verification_reference=ref,
        )


class MCALiveProvider(BaseVerificationProvider):
    """Authorized MCA corporate registry verification provider."""

    def __init__(self, source: VerificationSource = VerificationSource.MCA_AUTHORIZED_API, api_url: str | None = None, api_key: str | None = None):
        self._source = source
        self.api_url = api_url or settings.MCA_API_URL or settings.MCA_API_BASE_URL
        self.api_key = api_key or settings.MCA_API_KEY
        self.auth_mode = settings.MCA_AUTH_MODE

    @property
    def mode(self) -> VerificationMode:
        return VerificationMode.LIVE

    @property
    def source(self) -> VerificationSource:
        return self._source

    async def verify(self, bidder_data: dict[str, Any], field: str) -> VerificationResultRead:
        now = datetime.now(timezone.utc)
        ver_id = str(uuid.uuid4())
        bidder_id = bidder_data.get("id", "UNKNOWN_BIDDER")
        claimed_id = bidder_data.get("cin") or bidder_data.get("identifier") or bidder_data.get("bidder_name")

        if not self.api_url or not self.api_key:
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_id,
                field=field,
                claimed_value=claimed_id,
                verified_value=None,
                status=VerificationStatus.UNAVAILABLE,
                source=self.source,
                mode=self.mode,
                checked_at=now,
                error_message="Live MCA provider is not configured. Missing API URL or API key.",
            )

        if not claimed_id:
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_id,
                field=field,
                claimed_value=None,
                verified_value=None,
                status=VerificationStatus.UNVERIFIED,
                source=self.source,
                mode=self.mode,
                checked_at=now,
                error_message="Missing required identifier (CIN/Company Name) for live verification.",
            )

        status_code, data, err_status, err_msg = await LiveHTTPClient.request(
            url=self.api_url,
            method="GET",
            auth_mode=self.auth_mode,
            api_key=self.api_key,
            params={"identifier": claimed_id, "field": field},
            timeout_seconds=settings.REQUEST_TIMEOUT_SECONDS,
        )

        if err_status is not None:
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_id,
                field=field,
                claimed_value=claimed_id,
                verified_value=None,
                status=err_status,
                source=self.source,
                mode=self.mode,
                checked_at=now,
                error_message=err_msg,
            )

        if not isinstance(data, dict):
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_id,
                field=field,
                claimed_value=claimed_id,
                verified_value=None,
                status=VerificationStatus.SERVICE_ERROR,
                source=self.source,
                mode=self.mode,
                checked_at=now,
                error_message="Invalid response payload format from live MCA provider: expected JSON object.",
            )

        ref = data.get("reference") or data.get("transaction_id") or data.get("reference_id")

        if "match" in data and isinstance(data["match"], bool):
            is_match = data["match"]
        else:
            if "company_status" not in data and "verified_value" not in data and "company_name" not in data:
                return VerificationResultRead(
                    id=ver_id,
                    bidder_id=bidder_id,
                    field=field,
                    claimed_value=claimed_id,
                    verified_value=None,
                    status=VerificationStatus.SERVICE_ERROR,
                    source=self.source,
                    mode=self.mode,
                    checked_at=now,
                    error_message="Invalid response schema from live MCA provider: missing required fields.",
                )
            mca_status = data.get("company_status")
            is_match = (mca_status in ("ACTIVE", "ACTIVE_COMPANY")) if mca_status else False

        verified_val = data.get("verified_value")
        if verified_val is None:
            if "company_status" in data or "company_name" in data:
                verified_val = {
                    "cin": data.get("cin", claimed_id),
                    "company_name": data.get("company_name"),
                    "company_status": data.get("company_status"),
                }
            else:
                return VerificationResultRead(
                    id=ver_id,
                    bidder_id=bidder_id,
                    field=field,
                    claimed_value=claimed_id,
                    verified_value=None,
                    status=VerificationStatus.SERVICE_ERROR,
                    source=self.source,
                    mode=self.mode,
                    checked_at=now,
                    error_message="Live MCA response missing expected verified_value payload.",
                )

        status = VerificationStatus.VERIFIED if is_match else VerificationStatus.MISMATCH

        return VerificationResultRead(
            id=ver_id,
            bidder_id=bidder_id,
            field=field,
            claimed_value=claimed_id,
            verified_value=verified_val,
            status=status,
            source=self.source,
            mode=self.mode,
            checked_at=now,
            verification_reference=ref,
        )


class EPFOLiveProvider(BaseVerificationProvider):
    """Authorized EPFO labor compliance verification provider."""

    def __init__(self, source: VerificationSource = VerificationSource.EPFO_AUTHORIZED_CHANNEL, api_url: str | None = None, api_key: str | None = None):
        self._source = source
        self.api_url = api_url or settings.EPFO_API_URL or settings.EPFO_API_BASE_URL
        self.api_key = api_key or settings.EPFO_API_KEY
        self.auth_mode = settings.EPFO_AUTH_MODE

    @property
    def mode(self) -> VerificationMode:
        return VerificationMode.LIVE

    @property
    def source(self) -> VerificationSource:
        return self._source

    async def verify(self, bidder_data: dict[str, Any], field: str) -> VerificationResultRead:
        now = datetime.now(timezone.utc)
        ver_id = str(uuid.uuid4())
        bidder_id = bidder_data.get("id", "UNKNOWN_BIDDER")
        claimed_id = bidder_data.get("pan") or bidder_data.get("epfo_id") or bidder_data.get("identifier") or bidder_data.get("bidder_name")

        if not self.api_url or not self.api_key:
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_id,
                field=field,
                claimed_value=claimed_id,
                verified_value=None,
                status=VerificationStatus.UNAVAILABLE,
                source=self.source,
                mode=self.mode,
                checked_at=now,
                error_message="Live EPFO provider is not configured. Missing API URL or API key.",
            )

        if not claimed_id:
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_id,
                field=field,
                claimed_value=None,
                verified_value=None,
                status=VerificationStatus.UNVERIFIED,
                source=self.source,
                mode=self.mode,
                checked_at=now,
                error_message="Missing required identifier (PAN/EPFO Est Code) for live verification.",
            )

        status_code, data, err_status, err_msg = await LiveHTTPClient.request(
            url=self.api_url,
            method="GET",
            auth_mode=self.auth_mode,
            api_key=self.api_key,
            params={"identifier": claimed_id, "field": field},
            timeout_seconds=settings.REQUEST_TIMEOUT_SECONDS,
        )

        if err_status is not None:
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_id,
                field=field,
                claimed_value=claimed_id,
                verified_value=None,
                status=err_status,
                source=self.source,
                mode=self.mode,
                checked_at=now,
                error_message=err_msg,
            )

        if not isinstance(data, dict):
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_id,
                field=field,
                claimed_value=claimed_id,
                verified_value=None,
                status=VerificationStatus.SERVICE_ERROR,
                source=self.source,
                mode=self.mode,
                checked_at=now,
                error_message="Invalid response payload format from live EPFO provider: expected JSON object.",
            )

        ref = data.get("reference") or data.get("transaction_id") or data.get("reference_id")

        if "match" in data and isinstance(data["match"], bool):
            is_match = data["match"]
        else:
            if "compliance_status" not in data and "verified_value" not in data and "establishment_name" not in data:
                return VerificationResultRead(
                    id=ver_id,
                    bidder_id=bidder_id,
                    field=field,
                    claimed_value=claimed_id,
                    verified_value=None,
                    status=VerificationStatus.SERVICE_ERROR,
                    source=self.source,
                    mode=self.mode,
                    checked_at=now,
                    error_message="Invalid response schema from live EPFO provider: missing required fields.",
                )
            epfo_status = data.get("compliance_status")
            is_match = (epfo_status in ("REGULAR_PAYER", "COMPLIANT", "ACTIVE")) if epfo_status else False

        verified_val = data.get("verified_value")
        if verified_val is None:
            if "compliance_status" in data or "establishment_name" in data:
                verified_val = {
                    "pan": claimed_id,
                    "establishment_name": data.get("establishment_name"),
                    "compliance_status": data.get("compliance_status"),
                }
            else:
                return VerificationResultRead(
                    id=ver_id,
                    bidder_id=bidder_id,
                    field=field,
                    claimed_value=claimed_id,
                    verified_value=None,
                    status=VerificationStatus.SERVICE_ERROR,
                    source=self.source,
                    mode=self.mode,
                    checked_at=now,
                    error_message="Live EPFO response missing expected verified_value payload.",
                )

        status = VerificationStatus.VERIFIED if is_match else VerificationStatus.MISMATCH

        return VerificationResultRead(
            id=ver_id,
            bidder_id=bidder_id,
            field=field,
            claimed_value=claimed_id,
            verified_value=verified_val,
            status=status,
            source=self.source,
            mode=self.mode,
            checked_at=now,
            verification_reference=ref,
        )


class ESICLiveProvider(BaseVerificationProvider):
    """Authorized ESIC labor compliance verification provider."""

    def __init__(self, source: VerificationSource = VerificationSource.ESIC_AUTHORIZED_CHANNEL, api_url: str | None = None, api_key: str | None = None):
        self._source = source
        self.api_url = api_url or settings.ESIC_API_URL or settings.ESIC_API_BASE_URL
        self.api_key = api_key or settings.ESIC_API_KEY
        self.auth_mode = settings.ESIC_AUTH_MODE

    @property
    def mode(self) -> VerificationMode:
        return VerificationMode.LIVE

    @property
    def source(self) -> VerificationSource:
        return self._source

    async def verify(self, bidder_data: dict[str, Any], field: str) -> VerificationResultRead:
        now = datetime.now(timezone.utc)
        ver_id = str(uuid.uuid4())
        bidder_id = bidder_data.get("id", "UNKNOWN_BIDDER")
        claimed_id = bidder_data.get("pan") or bidder_data.get("esic_id") or bidder_data.get("identifier") or bidder_data.get("bidder_name")

        if not self.api_url or not self.api_key:
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_id,
                field=field,
                claimed_value=claimed_id,
                verified_value=None,
                status=VerificationStatus.UNAVAILABLE,
                source=self.source,
                mode=self.mode,
                checked_at=now,
                error_message="Live ESIC provider is not configured. Missing API URL or API key.",
            )

        if not claimed_id:
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_id,
                field=field,
                claimed_value=None,
                verified_value=None,
                status=VerificationStatus.UNVERIFIED,
                source=self.source,
                mode=self.mode,
                checked_at=now,
                error_message="Missing required identifier (PAN/ESIC Code) for live verification.",
            )

        status_code, data, err_status, err_msg = await LiveHTTPClient.request(
            url=self.api_url,
            method="GET",
            auth_mode=self.auth_mode,
            api_key=self.api_key,
            params={"identifier": claimed_id, "field": field},
            timeout_seconds=settings.REQUEST_TIMEOUT_SECONDS,
        )

        if err_status is not None:
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_id,
                field=field,
                claimed_value=claimed_id,
                verified_value=None,
                status=err_status,
                source=self.source,
                mode=self.mode,
                checked_at=now,
                error_message=err_msg,
            )

        if not isinstance(data, dict):
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_id,
                field=field,
                claimed_value=claimed_id,
                verified_value=None,
                status=VerificationStatus.SERVICE_ERROR,
                source=self.source,
                mode=self.mode,
                checked_at=now,
                error_message="Invalid response payload format from live ESIC provider: expected JSON object.",
            )

        ref = data.get("reference") or data.get("transaction_id") or data.get("reference_id")

        if "match" in data and isinstance(data["match"], bool):
            is_match = data["match"]
        else:
            if "contribution_status" not in data and "verified_value" not in data and "employer_name" not in data:
                return VerificationResultRead(
                    id=ver_id,
                    bidder_id=bidder_id,
                    field=field,
                    claimed_value=claimed_id,
                    verified_value=None,
                    status=VerificationStatus.SERVICE_ERROR,
                    source=self.source,
                    mode=self.mode,
                    checked_at=now,
                    error_message="Invalid response schema from live ESIC provider: missing required fields.",
                )
            esic_status = data.get("contribution_status")
            is_match = (esic_status in ("COMPLIANT", "ACTIVE", "REGULAR_PAYER")) if esic_status else False

        verified_val = data.get("verified_value")
        if verified_val is None:
            if "contribution_status" in data or "employer_name" in data:
                verified_val = {
                    "pan": claimed_id,
                    "employer_name": data.get("employer_name"),
                    "contribution_status": data.get("contribution_status"),
                }
            else:
                return VerificationResultRead(
                    id=ver_id,
                    bidder_id=bidder_id,
                    field=field,
                    claimed_value=claimed_id,
                    verified_value=None,
                    status=VerificationStatus.SERVICE_ERROR,
                    source=self.source,
                    mode=self.mode,
                    checked_at=now,
                    error_message="Live ESIC response missing expected verified_value payload.",
                )

        status = VerificationStatus.VERIFIED if is_match else VerificationStatus.MISMATCH

        return VerificationResultRead(
            id=ver_id,
            bidder_id=bidder_id,
            field=field,
            claimed_value=claimed_id,
            verified_value=verified_val,
            status=status,
            source=self.source,
            mode=self.mode,
            checked_at=now,
            verification_reference=ref,
        )


class BlacklistLiveProvider(BaseVerificationProvider):
    """Authorized Blacklist / Debarment live registry verification provider."""

    def __init__(self, source: VerificationSource = VerificationSource.BLACKLIST_AUTHORIZED_SOURCE, api_url: str | None = None, api_key: str | None = None):
        self._source = source
        self.api_url = api_url or settings.BLACKLIST_API_URL or settings.BLACKLIST_API_BASE_URL
        self.api_key = api_key or settings.BLACKLIST_API_KEY
        self.auth_mode = settings.BLACKLIST_AUTH_MODE

    @property
    def mode(self) -> VerificationMode:
        return VerificationMode.LIVE

    @property
    def source(self) -> VerificationSource:
        return self._source

    async def verify(self, bidder_data: dict[str, Any], field: str) -> VerificationResultRead:
        now = datetime.now(timezone.utc)
        ver_id = str(uuid.uuid4())
        bidder_id = bidder_data.get("id", "UNKNOWN_BIDDER")
        claimed_id = bidder_data.get("bidder_name") or bidder_data.get("identifier")

        if not self.api_url or not self.api_key:
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_id,
                field=field,
                claimed_value=claimed_id,
                verified_value=None,
                status=VerificationStatus.UNAVAILABLE,
                source=self.source,
                mode=self.mode,
                checked_at=now,
                error_message="Live Blacklist provider is not configured. Missing API URL or API key.",
            )

        if not claimed_id:
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_id,
                field=field,
                claimed_value=None,
                verified_value=None,
                status=VerificationStatus.UNVERIFIED,
                source=self.source,
                mode=self.mode,
                checked_at=now,
                error_message="Missing required identifier (Bidder Name) for live debarment verification.",
            )

        status_code, data, err_status, err_msg = await LiveHTTPClient.request(
            url=self.api_url,
            method="GET",
            auth_mode=self.auth_mode,
            api_key=self.api_key,
            params={"identifier": claimed_id, "field": field},
            timeout_seconds=settings.REQUEST_TIMEOUT_SECONDS,
        )

        if err_status is not None:
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_id,
                field=field,
                claimed_value=claimed_id,
                verified_value=None,
                status=err_status,
                source=self.source,
                mode=self.mode,
                checked_at=now,
                error_message=err_msg,
            )

        if not isinstance(data, dict):
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_id,
                field=field,
                claimed_value=claimed_id,
                verified_value=None,
                status=VerificationStatus.SERVICE_ERROR,
                source=self.source,
                mode=self.mode,
                checked_at=now,
                error_message="Invalid response payload format from live Blacklist provider: expected JSON object.",
            )

        ref = data.get("reference") or data.get("transaction_id") or data.get("reference_id")

        if "match" in data and isinstance(data["match"], bool):
            is_match = data["match"]
        else:
            if "blacklisted" not in data and "debarred" not in data and "verified_value" not in data:
                return VerificationResultRead(
                    id=ver_id,
                    bidder_id=bidder_id,
                    field=field,
                    claimed_value=claimed_id,
                    verified_value=None,
                    status=VerificationStatus.SERVICE_ERROR,
                    source=self.source,
                    mode=self.mode,
                    checked_at=now,
                    error_message="Invalid response schema from live Blacklist provider: missing required fields.",
                )
            blacklisted = bool(data.get("blacklisted", False) or data.get("debarred", False))
            is_match = not blacklisted

        verified_val = data.get("verified_value")
        if verified_val is None:
            if "blacklisted" in data or "debarred" in data:
                verified_val = {
                    "blacklisted": bool(data.get("blacklisted", False)),
                    "debarred": bool(data.get("debarred", False)),
                }
            else:
                return VerificationResultRead(
                    id=ver_id,
                    bidder_id=bidder_id,
                    field=field,
                    claimed_value=claimed_id,
                    verified_value=None,
                    status=VerificationStatus.SERVICE_ERROR,
                    source=self.source,
                    mode=self.mode,
                    checked_at=now,
                    error_message="Live Blacklist response missing expected verified_value payload.",
                )

        status = VerificationStatus.VERIFIED if is_match else VerificationStatus.MISMATCH

        return VerificationResultRead(
            id=ver_id,
            bidder_id=bidder_id,
            field=field,
            claimed_value=claimed_id,
            verified_value=verified_val,
            status=status,
            source=self.source,
            mode=self.mode,
            checked_at=now,
            verification_reference=ref,
        )


class GenericLiveProvider(BaseVerificationProvider):
    """Generic live provider wrapper delegating to specific domain live provider for backwards compatibility."""

    def __init__(self, domain: str, source: VerificationSource, api_base_url: str | None = None, api_key: str | None = None):
        self.domain = domain.lower()
        self._source = source
        self.api_base_url = api_base_url
        self.api_key = api_key

    @property
    def mode(self) -> VerificationMode:
        return VerificationMode.LIVE

    @property
    def source(self) -> VerificationSource:
        return self._source

    async def verify(self, bidder_data: dict[str, Any], field: str) -> VerificationResultRead:
        provider_cls_map = {
            "gst": GSTLiveProvider,
            "udyam": UdyamLiveProvider,
            "mca": MCALiveProvider,
            "epfo": EPFOLiveProvider,
            "esic": ESICLiveProvider,
            "blacklist": BlacklistLiveProvider,
        }
        provider_cls = provider_cls_map.get(self.domain, GSTLiveProvider)
        provider = provider_cls(source=self.source, api_url=self.api_base_url, api_key=self.api_key)
        return await provider.verify(bidder_data, field)
