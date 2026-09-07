import base64
import uuid
from typing import Any
import httpx
from pydantic import ValidationError

from app.core.config import settings
from app.schemas.canonical import (
    AIServiceResult,
    ExtractedFactCreate,
    TenderRequirementCreate,
)


class AIServiceAdapter:
    """Interface for ARGUS Intelligence Service (document parsing & LLM requirement extraction).

    The backend validates all extracted payloads against canonical Pydantic models.
    Does NOT invoke LLMs directly inside backend; interfaces via structured HTTP service calls.
    Follows contract-first integration with secure document byte transfer and bounded retries.
    """

    async def extract_tender(
        self,
        tender_id: str,
        document_id: str | None = None,
        document_uri: str | None = None,
        document_sha256: str | None = None,
        file_bytes: bytes | None = None,
        filename: str | None = None,
        content_type: str | None = None,
        request_id: str | None = None,
        **kwargs: Any,
    ) -> AIServiceResult:
        """Extract tender requirements via configured ARGUS Intelligence gateway."""
        doc_id = document_id or f"doc_tender_{tender_id}"
        doc_uri = document_uri or ""

        url = settings.ARGUS_INTELLIGENCE_EXTRACT_TENDER_URL

        if not url or not url.strip():
            return AIServiceResult(
                success=False,
                data=None,
                error_code="AI_SERVICE_UNAVAILABLE",
                retryable=True,
                message="ARGUS Intelligence tender extraction URL is unconfigured or unavailable.",
            )

        req_id = request_id or str(uuid.uuid4())
        file_b64 = base64.b64encode(file_bytes).decode("utf-8") if file_bytes else None

        payload: dict[str, Any] = {
            "contract_version": "1.0",
            "request_id": req_id,
            "tender_id": tender_id,
            "document_id": doc_id,
            "document_uri": doc_uri,
            "document_sha256": document_sha256,
            "filename": filename,
            "content_type": content_type,
            "file_bytes_base64": file_b64,
        }

        headers: dict[str, str] = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "ARGUS-Procurement-Platform/1.0",
            "X-Request-ID": req_id,
        }
        if settings.ARGUS_INTELLIGENCE_API_KEY:
            headers["Authorization"] = f"Bearer {settings.ARGUS_INTELLIGENCE_API_KEY}"

        timeout = httpx.Timeout(settings.REQUEST_TIMEOUT_SECONDS)
        max_attempts = 3

        for attempt in range(1, max_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(url, headers=headers, json=payload)

                    if resp.status_code == 400:
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="AI_SERVICE_REQUEST_REJECTED",
                            retryable=False,
                            message="Intelligence service rejected the request (HTTP 400).",
                        )
                    elif resp.status_code in (401, 403):
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="AI_SERVICE_AUTH_ERROR",
                            retryable=False,
                            message=f"Intelligence service authentication error (HTTP {resp.status_code}).",
                        )
                    elif resp.status_code == 404:
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="AI_SERVICE_ENDPOINT_NOT_FOUND",
                            retryable=False,
                            message="Intelligence service endpoint not found (HTTP 404).",
                        )
                    elif resp.status_code in (408, 429):
                        if attempt < max_attempts:
                            continue
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="AI_SERVICE_UNAVAILABLE",
                            retryable=True,
                            message=f"Intelligence service rate limited or timed out (HTTP {resp.status_code}).",
                        )
                    elif 400 <= resp.status_code < 500:
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="AI_SERVICE_REQUEST_REJECTED",
                            retryable=False,
                            message=f"Intelligence service rejected request (HTTP {resp.status_code}).",
                        )
                    elif resp.status_code >= 500:
                        if attempt < max_attempts:
                            continue
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="AI_SERVICE_UNAVAILABLE",
                            retryable=True,
                            message=f"Intelligence service HTTP error (status {resp.status_code}).",
                        )

                    try:
                        resp_data = resp.json()
                    except Exception as parse_err:
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="SCHEMA_VALIDATION_FAILED",
                            retryable=False,
                            message=f"Failed to parse intelligence JSON response: {parse_err}",
                        )

                    if not isinstance(resp_data, dict):
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="SCHEMA_VALIDATION_FAILED",
                            retryable=False,
                            message="Intelligence service response must be a JSON object.",
                        )

                    if resp_data.get("status") in ("FAILED", "ERROR"):
                        err_msg = resp_data.get("error") or resp_data.get("message") or "Extraction failed on service."
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="AI_SERVICE_REQUEST_REJECTED",
                            retryable=False,
                            message=f"Intelligence processing failure: {err_msg}",
                        )

                    raw_items = resp_data.get("requirements") if "requirements" in resp_data else resp_data.get("data")
                    if not isinstance(raw_items, list):
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="SCHEMA_VALIDATION_FAILED",
                            retryable=False,
                            message="Intelligence service response missing required 'requirements' list.",
                        )

                    validated_requirements = []
                    for item in raw_items:
                        if not isinstance(item, dict):
                            return AIServiceResult(
                                success=False,
                                data=None,
                                error_code="SCHEMA_VALIDATION_FAILED",
                                retryable=False,
                                message="Requirement item must be a JSON object.",
                            )
                        try:
                            # Scrub model-supplied authority and approval fields
                            for authority_field in ("is_approved", "approved_by", "approved_at", "approval_status", "approved"):
                                item.pop(authority_field, None)
                                if isinstance(item.get("metadata_json"), dict):
                                    item["metadata_json"].pop(authority_field, None)

                            item_meta = item.get("metadata_json") or {}
                            item_meta.setdefault("request_id", req_id)
                            item_meta.setdefault("document_id", doc_id)
                            if document_sha256:
                                item_meta.setdefault("document_sha256", document_sha256)
                            if "provider_model" in resp_data:
                                item_meta.setdefault("provider_model", resp_data["provider_model"])

                            item["metadata_json"] = item_meta
                            item["document_id"] = doc_id
                            item["is_approved"] = False

                            req_obj = TenderRequirementCreate.model_validate(item)
                            validated_requirements.append(req_obj.model_dump())
                        except ValidationError as val_err:
                            return AIServiceResult(
                                success=False,
                                data=None,
                                error_code="SCHEMA_VALIDATION_FAILED",
                                retryable=False,
                                message=f"Requirement payload failed schema validation: {val_err}",
                            )

                    return AIServiceResult(
                        success=True,
                        data=validated_requirements,
                        error_code=None,
                        retryable=False,
                        message=f"Successfully extracted {len(validated_requirements)} requirement candidates.",
                    )

            except httpx.TimeoutException:
                if attempt < max_attempts:
                    continue
                return AIServiceResult(
                    success=False,
                    data=None,
                    error_code="AI_SERVICE_UNAVAILABLE",
                    retryable=True,
                    message=f"Intelligence service request timed out after {settings.REQUEST_TIMEOUT_SECONDS}s.",
                )
            except httpx.RequestError as req_err:
                if attempt < max_attempts:
                    continue
                return AIServiceResult(
                    success=False,
                    data=None,
                    error_code="AI_SERVICE_UNAVAILABLE",
                    retryable=True,
                    message=f"Intelligence service transport failure: {req_err}",
                )
            except Exception as exc:
                return AIServiceResult(
                    success=False,
                    data=None,
                    error_code="AI_SERVICE_UNAVAILABLE",
                    retryable=False,
                    message=f"Unexpected internal error: {exc}",
                )

        return AIServiceResult(
            success=False,
            data=None,
            error_code="AI_SERVICE_UNAVAILABLE",
            retryable=True,
            message="Intelligence service unavailable after maximum retries.",
        )

    async def extract_document(
        self,
        document_id: str,
        document_uri: str | None = None,
        bidder_id: str | None = None,
        document_type: str | None = None,
        document_sha256: str | None = None,
        file_bytes: bytes | None = None,
        filename: str | None = None,
        content_type: str | None = None,
        request_id: str | None = None,
        **kwargs: Any,
    ) -> AIServiceResult:
        """Extract bidder facts from bidder document via configured ARGUS Intelligence gateway."""
        b_id = bidder_id or ""
        doc_uri = document_uri or ""

        url = settings.ARGUS_INTELLIGENCE_EXTRACT_DOCUMENT_URL

        if not url or not url.strip():
            return AIServiceResult(
                success=False,
                data=None,
                error_code="AI_SERVICE_UNAVAILABLE",
                retryable=True,
                message="ARGUS Intelligence document extraction URL is unconfigured or unavailable.",
            )

        req_id = request_id or str(uuid.uuid4())
        file_b64 = base64.b64encode(file_bytes).decode("utf-8") if file_bytes else None

        payload: dict[str, Any] = {
            "contract_version": "1.0",
            "request_id": req_id,
            "document_id": document_id,
            "document_uri": doc_uri,
            "bidder_id": b_id,
            "document_type": document_type,
            "document_sha256": document_sha256,
            "filename": filename,
            "content_type": content_type,
            "file_bytes_base64": file_b64,
        }

        headers: dict[str, str] = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "ARGUS-Procurement-Platform/1.0",
            "X-Request-ID": req_id,
        }
        if settings.ARGUS_INTELLIGENCE_API_KEY:
            headers["Authorization"] = f"Bearer {settings.ARGUS_INTELLIGENCE_API_KEY}"

        timeout = httpx.Timeout(settings.REQUEST_TIMEOUT_SECONDS)
        max_attempts = 3

        for attempt in range(1, max_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(url, headers=headers, json=payload)

                    if resp.status_code == 400:
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="AI_SERVICE_REQUEST_REJECTED",
                            retryable=False,
                            message="Intelligence service rejected request (HTTP 400).",
                        )
                    elif resp.status_code in (401, 403):
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="AI_SERVICE_AUTH_ERROR",
                            retryable=False,
                            message=f"Intelligence service auth error (HTTP {resp.status_code}).",
                        )
                    elif resp.status_code == 404:
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="AI_SERVICE_ENDPOINT_NOT_FOUND",
                            retryable=False,
                            message="Intelligence service endpoint not found (HTTP 404).",
                        )
                    elif resp.status_code in (408, 429):
                        if attempt < max_attempts:
                            continue
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="AI_SERVICE_UNAVAILABLE",
                            retryable=True,
                            message=f"Intelligence service rate limited or timed out (HTTP {resp.status_code}).",
                        )
                    elif 400 <= resp.status_code < 500:
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="AI_SERVICE_REQUEST_REJECTED",
                            retryable=False,
                            message=f"Intelligence service rejected request (HTTP {resp.status_code}).",
                        )
                    elif resp.status_code >= 500:
                        if attempt < max_attempts:
                            continue
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="AI_SERVICE_UNAVAILABLE",
                            retryable=True,
                            message=f"Intelligence service HTTP error (status {resp.status_code}).",
                        )

                    try:
                        resp_data = resp.json()
                    except Exception as parse_err:
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="SCHEMA_VALIDATION_FAILED",
                            retryable=False,
                            message=f"Failed to parse intelligence JSON response: {parse_err}",
                        )

                    if not isinstance(resp_data, dict):
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="SCHEMA_VALIDATION_FAILED",
                            retryable=False,
                            message="Intelligence service response must be a JSON object.",
                        )

                    if resp_data.get("status") in ("FAILED", "ERROR"):
                        err_msg = resp_data.get("error") or resp_data.get("message") or "Extraction failed on service."
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="AI_SERVICE_REQUEST_REJECTED",
                            retryable=False,
                            message=f"Intelligence processing failure: {err_msg}",
                        )

                    raw_facts = resp_data.get("facts") if "facts" in resp_data else resp_data.get("data")
                    if not isinstance(raw_facts, list):
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="SCHEMA_VALIDATION_FAILED",
                            retryable=False,
                            message="Intelligence service response missing required 'facts' list.",
                        )

                    validated_facts = []
                    for item in raw_facts:
                        if not isinstance(item, dict):
                            return AIServiceResult(
                                success=False,
                                data=None,
                                error_code="SCHEMA_VALIDATION_FAILED",
                                retryable=False,
                                message="Extracted fact item must be a JSON object.",
                            )
                        try:
                            for authority_field in ("is_approved", "approved_by", "approved_at", "approval_status", "approved"):
                                item.pop(authority_field, None)
                                if isinstance(item.get("metadata_json"), dict):
                                    item["metadata_json"].pop(authority_field, None)

                            item_meta = item.get("metadata_json") or {}
                            item_meta.setdefault("request_id", req_id)
                            item_meta.setdefault("document_id", document_id)
                            if document_sha256:
                                item_meta.setdefault("document_sha256", document_sha256)
                            if "provider_model" in resp_data:
                                item_meta.setdefault("provider_model", resp_data["provider_model"])

                            item["metadata_json"] = item_meta

                            fact_obj = ExtractedFactCreate.model_validate(item)
                            validated_facts.append(fact_obj.model_dump())
                        except ValidationError as val_err:
                            return AIServiceResult(
                                success=False,
                                data=None,
                                error_code="SCHEMA_VALIDATION_FAILED",
                                retryable=False,
                                message=f"Extracted fact payload failed schema validation: {val_err}",
                            )

                    return AIServiceResult(
                        success=True,
                        data=validated_facts,
                        error_code=None,
                        retryable=False,
                        message=f"Successfully extracted {len(validated_facts)} facts from bidder document.",
                    )

            except httpx.TimeoutException:
                if attempt < max_attempts:
                    continue
                return AIServiceResult(
                    success=False,
                    data=None,
                    error_code="AI_SERVICE_UNAVAILABLE",
                    retryable=True,
                    message=f"Intelligence service request timed out after {settings.REQUEST_TIMEOUT_SECONDS}s.",
                )
            except httpx.RequestError as req_err:
                if attempt < max_attempts:
                    continue
                return AIServiceResult(
                    success=False,
                    data=None,
                    error_code="AI_SERVICE_UNAVAILABLE",
                    retryable=True,
                    message=f"Intelligence service transport failure: {req_err}",
                )
            except Exception as exc:
                return AIServiceResult(
                    success=False,
                    data=None,
                    error_code="AI_SERVICE_UNAVAILABLE",
                    retryable=False,
                    message=f"Unexpected internal error: {exc}",
                )

        return AIServiceResult(
            success=False,
            data=None,
            error_code="AI_SERVICE_UNAVAILABLE",
            retryable=True,
            message="Intelligence service unavailable after maximum retries.",
        )
