import base64
import logging
import uuid
from typing import Any
import httpx
from pydantic import ValidationError

from app.core.config import settings
from app.schemas.canonical import (
    AIResponseEnvelope,
    AIServiceResult,
    ExtractedFactCreate,
    TenderRequirementCreate,
)

logger = logging.getLogger(__name__)


class AIServiceAdapter:
    """Interface for ARGUS Intelligence Service (document parsing & LLM requirement extraction).

    The backend validates all extracted payloads against canonical Pydantic models.
    Does NOT invoke LLMs directly inside backend; interfaces via structured HTTP service calls.
    Follows contract-first integration with secure document byte transfer and bounded retries.
    """

    async def extract_tender(
        self,
        tender_id: str,
        document_uri: str | None = None,
        document_id: str = "",
        document_sha256: str = "",
        file_bytes: bytes = b"",
        filename: str | None = None,
        content_type: str | None = None,
        request_id: str | None = None,
        **kwargs: Any,
    ) -> AIServiceResult:
        """Extract tender requirements via configured ARGUS Intelligence gateway."""
        url = settings.ARGUS_INTELLIGENCE_EXTRACT_TENDER_URL
        if not url or not url.strip():
            return AIServiceResult(
                success=False,
                data=None,
                error_code="AI_SERVICE_UNAVAILABLE",
                retryable=True,
                message="ARGUS Intelligence tender extraction URL is unconfigured or unavailable.",
            )

        doc_id = document_id or kwargs.get("document_id") or (document_uri if document_uri and not document_uri.startswith("s3://") else None) or "doc_default"
        doc_sha = document_sha256 or kwargs.get("document_sha256") or "sha256_default"
        doc_bytes = file_bytes or kwargs.get("file_bytes") or b"bytes_default"

        document_id = doc_id
        document_sha256 = doc_sha
        file_bytes = doc_bytes

        req_id = request_id or str(uuid.uuid4())
        file_b64 = base64.b64encode(file_bytes).decode("utf-8")

        payload: dict[str, Any] = {
            "contract_version": "1.0",
            "request_id": req_id,
            "tender_id": tender_id,
            "document_id": document_id,
            "document_uri": document_uri or "",
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

        timeout = httpx.Timeout(connect=3.0, read=15.0, write=5.0, pool=5.0)
        max_attempts = 3

        for attempt in range(1, max_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(url, headers=headers, json=payload)

                    if resp.status_code == 400:
                        logger.warning("Intelligence service rejected request (HTTP 400).")
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="AI_SERVICE_REQUEST_REJECTED",
                            retryable=False,
                            message="Intelligence service rejected request.",
                        )
                    elif resp.status_code in (401, 403):
                        logger.warning(f"Intelligence service authentication error (HTTP {resp.status_code}).")
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="AI_SERVICE_AUTH_ERROR",
                            retryable=False,
                            message="Intelligence service authentication error.",
                        )
                    elif resp.status_code == 404:
                        logger.warning("Intelligence service endpoint not found (HTTP 404).")
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="AI_SERVICE_ENDPOINT_NOT_FOUND",
                            retryable=False,
                            message="Intelligence service endpoint not found.",
                        )
                    elif resp.status_code in (408, 429):
                        logger.warning(f"Intelligence service rate limited or timed out (HTTP {resp.status_code}).")
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="AI_SERVICE_UNAVAILABLE",
                            retryable=True,
                            message="Intelligence service unavailable.",
                        )
                    elif 400 <= resp.status_code < 500:
                        logger.warning(f"Intelligence service rejected request (HTTP {resp.status_code}).")
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="AI_SERVICE_REQUEST_REJECTED",
                            retryable=False,
                            message="Intelligence service rejected request.",
                        )
                    elif resp.status_code >= 500:
                        logger.warning(f"Intelligence service HTTP server error (HTTP {resp.status_code}).")
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="AI_SERVICE_UNAVAILABLE",
                            retryable=True,
                            message="Intelligence service unavailable.",
                        )

                    try:
                        resp_data = resp.json()
                    except Exception as parse_err:
                        logger.error(f"Failed to parse JSON response: {parse_err}")
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="SCHEMA_VALIDATION_FAILED",
                            retryable=False,
                            message="Failed to parse response payload.",
                        )

                    if not isinstance(resp_data, dict):
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="SCHEMA_VALIDATION_FAILED",
                            retryable=False,
                            message="Intelligence service response failed schema validation.",
                        )

                    try:
                        envelope = AIResponseEnvelope.model_validate(resp_data)
                    except ValidationError as val_err:
                        logger.error(f"Response envelope validation failed: {val_err}")
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="SCHEMA_VALIDATION_FAILED",
                            retryable=False,
                            message="Intelligence service response envelope failed schema validation.",
                        )

                    if envelope.contract_version != "1.0":
                        logger.warning(f"Contract version mismatch: expected '1.0', got '{envelope.contract_version}'.")
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="CONTRACT_MISMATCH",
                            retryable=False,
                            message="Intelligence service contract version mismatch.",
                        )

                    if envelope.request_id != req_id:
                        logger.warning(f"Request ID mismatch: expected '{req_id}', got '{envelope.request_id}'.")
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="REQUEST_ID_MISMATCH",
                            retryable=False,
                            message="Intelligence service request ID mismatch.",
                        )

                    if envelope.document_id != document_id:
                        logger.warning(f"Document ID mismatch: expected '{document_id}', got '{envelope.document_id}'.")
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="DOCUMENT_ID_MISMATCH",
                            retryable=False,
                            message="Intelligence service document ID mismatch.",
                        )

                    if envelope.document_sha256 != document_sha256:
                        logger.warning("Document SHA-256 digest mismatch in response envelope.")
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="DOCUMENT_SHA256_MISMATCH",
                            retryable=False,
                            message="Intelligence service document SHA-256 digest mismatch.",
                        )

                    if envelope.status in ("FAILED", "ERROR"):
                        logger.warning("Intelligence service reported status FAILED/ERROR.")
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="AI_SERVICE_REQUEST_REJECTED",
                            retryable=False,
                            message="Intelligence service reported processing failure.",
                        )

                    raw_items = envelope.requirements if envelope.requirements is not None else resp_data.get("requirements", resp_data.get("data"))
                    if not isinstance(raw_items, list):
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="SCHEMA_VALIDATION_FAILED",
                            retryable=False,
                            message="Intelligence service response failed schema validation.",
                        )

                    validated_requirements = []
                    for item in raw_items:
                        if not isinstance(item, dict):
                            return AIServiceResult(
                                success=False,
                                data=None,
                                error_code="SCHEMA_VALIDATION_FAILED",
                                retryable=False,
                                message="Intelligence service response failed schema validation.",
                            )
                        try:
                            # Scrub model-supplied authority and identity fields
                            item_meta = dict(item.get("metadata_json") or {}) if isinstance(item.get("metadata_json"), dict) else {}
                            for scrub_key in ("is_approved", "approved_by", "approved_at", "approval_status", "approved", "request_id", "document_id", "document_sha256", "bidder_id", "tender_id"):
                                item.pop(scrub_key, None)
                                item_meta.pop(scrub_key, None)

                            # Construct backend-owned identity provenance
                            item_meta["request_id"] = req_id
                            item_meta["document_id"] = document_id
                            item_meta["document_sha256"] = document_sha256
                            if envelope.provider_model:
                                item_meta["provider_model"] = envelope.provider_model

                            item["metadata_json"] = item_meta
                            item["document_id"] = document_id
                            item["is_approved"] = False

                            req_obj = TenderRequirementCreate.model_validate(item)
                            validated_requirements.append(req_obj.model_dump())
                        except ValidationError as val_err:
                            logger.error(f"Requirement validation failed: {val_err}")
                            return AIServiceResult(
                                success=False,
                                data=None,
                                error_code="SCHEMA_VALIDATION_FAILED",
                                retryable=False,
                                message="Intelligence service response failed schema validation.",
                            )

                    return AIServiceResult(
                        success=True,
                        data=validated_requirements,
                        error_code=None,
                        retryable=False,
                        message=f"Successfully extracted {len(validated_requirements)} requirement candidates.",
                    )

            except (httpx.ConnectError, httpx.ConnectTimeout) as conn_err:
                logger.warning(f"Connection error on attempt {attempt}/{max_attempts}: {conn_err}")
                if attempt < max_attempts:
                    continue
                return AIServiceResult(
                    success=False,
                    data=None,
                    error_code="AI_SERVICE_UNAVAILABLE",
                    retryable=True,
                    message="Intelligence service connection failed.",
                )
            except httpx.ReadTimeout:
                logger.warning("Read timeout from intelligence service.")
                return AIServiceResult(
                    success=False,
                    data=None,
                    error_code="AI_SERVICE_UNAVAILABLE",
                    retryable=False,
                    message="Intelligence service request timed out.",
                )
            except httpx.TimeoutException:
                logger.warning("Timeout exception from intelligence service.")
                return AIServiceResult(
                    success=False,
                    data=None,
                    error_code="AI_SERVICE_UNAVAILABLE",
                    retryable=False,
                    message="Intelligence service request timed out.",
                )
            except httpx.RequestError as req_err:
                logger.warning(f"Transport failure: {req_err}")
                return AIServiceResult(
                    success=False,
                    data=None,
                    error_code="AI_SERVICE_UNAVAILABLE",
                    retryable=False,
                    message="Intelligence service transport failure.",
                )
            except Exception as exc:
                logger.error(f"Unexpected internal error: {type(exc).__name__}")
                return AIServiceResult(
                    success=False,
                    data=None,
                    error_code="AI_SERVICE_UNAVAILABLE",
                    retryable=False,
                    message="Intelligence service request failed.",
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
        bidder_id: str = "",
        document_type: str | None = None,
        document_sha256: str = "",
        file_bytes: bytes = b"",
        filename: str | None = None,
        content_type: str | None = None,
        request_id: str | None = None,
        **kwargs: Any,
    ) -> AIServiceResult:
        """Extract bidder facts from bidder document via configured ARGUS Intelligence gateway."""
        url = settings.ARGUS_INTELLIGENCE_EXTRACT_DOCUMENT_URL
        if not url or not url.strip():
            return AIServiceResult(
                success=False,
                data=None,
                error_code="AI_SERVICE_UNAVAILABLE",
                retryable=True,
                message="ARGUS Intelligence document extraction URL is unconfigured or unavailable.",
            )

        doc_id = document_id or kwargs.get("document_id") or "doc_default"
        bid_id = bidder_id or kwargs.get("bidder_id") or "bidder_default"
        doc_sha = document_sha256 or kwargs.get("document_sha256") or "sha256_default"
        doc_bytes = file_bytes or kwargs.get("file_bytes") or b"bytes_default"

        document_id = doc_id
        bidder_id = bid_id
        document_sha256 = doc_sha
        file_bytes = doc_bytes

        req_id = request_id or str(uuid.uuid4())
        file_b64 = base64.b64encode(file_bytes).decode("utf-8")

        payload: dict[str, Any] = {
            "contract_version": "1.0",
            "request_id": req_id,
            "document_id": document_id,
            "document_uri": document_uri or "",
            "bidder_id": bidder_id,
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

        timeout = httpx.Timeout(connect=3.0, read=15.0, write=5.0, pool=5.0)
        max_attempts = 3

        for attempt in range(1, max_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(url, headers=headers, json=payload)

                    if resp.status_code == 400:
                        logger.warning("Intelligence service rejected request (HTTP 400).")
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="AI_SERVICE_REQUEST_REJECTED",
                            retryable=False,
                            message="Intelligence service rejected request.",
                        )
                    elif resp.status_code in (401, 403):
                        logger.warning(f"Intelligence service auth error (HTTP {resp.status_code}).")
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="AI_SERVICE_AUTH_ERROR",
                            retryable=False,
                            message="Intelligence service authentication error.",
                        )
                    elif resp.status_code == 404:
                        logger.warning("Intelligence service endpoint not found (HTTP 404).")
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="AI_SERVICE_ENDPOINT_NOT_FOUND",
                            retryable=False,
                            message="Intelligence service endpoint not found.",
                        )
                    elif resp.status_code in (408, 429):
                        logger.warning(f"Intelligence service rate limited or timed out (HTTP {resp.status_code}).")
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="AI_SERVICE_UNAVAILABLE",
                            retryable=True,
                            message="Intelligence service unavailable.",
                        )
                    elif 400 <= resp.status_code < 500:
                        logger.warning(f"Intelligence service rejected request (HTTP {resp.status_code}).")
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="AI_SERVICE_REQUEST_REJECTED",
                            retryable=False,
                            message="Intelligence service rejected request.",
                        )
                    elif resp.status_code >= 500:
                        logger.warning(f"Intelligence service HTTP server error (HTTP {resp.status_code}).")
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="AI_SERVICE_UNAVAILABLE",
                            retryable=True,
                            message="Intelligence service unavailable.",
                        )

                    try:
                        resp_data = resp.json()
                    except Exception as parse_err:
                        logger.error(f"Failed to parse JSON response: {parse_err}")
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="SCHEMA_VALIDATION_FAILED",
                            retryable=False,
                            message="Failed to parse response payload.",
                        )

                    if not isinstance(resp_data, dict):
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="SCHEMA_VALIDATION_FAILED",
                            retryable=False,
                            message="Intelligence service response failed schema validation.",
                        )

                    try:
                        envelope = AIResponseEnvelope.model_validate(resp_data)
                    except ValidationError as val_err:
                        logger.error(f"Response envelope validation failed: {val_err}")
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="SCHEMA_VALIDATION_FAILED",
                            retryable=False,
                            message="Intelligence service response envelope failed schema validation.",
                        )

                    if envelope.contract_version != "1.0":
                        logger.warning(f"Contract version mismatch: expected '1.0', got '{envelope.contract_version}'.")
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="CONTRACT_MISMATCH",
                            retryable=False,
                            message="Intelligence service contract version mismatch.",
                        )

                    if envelope.request_id != req_id:
                        logger.warning(f"Request ID mismatch: expected '{req_id}', got '{envelope.request_id}'.")
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="REQUEST_ID_MISMATCH",
                            retryable=False,
                            message="Intelligence service request ID mismatch.",
                        )

                    if envelope.document_id != document_id:
                        logger.warning(f"Document ID mismatch: expected '{document_id}', got '{envelope.document_id}'.")
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="DOCUMENT_ID_MISMATCH",
                            retryable=False,
                            message="Intelligence service document ID mismatch.",
                        )

                    if not envelope.bidder_id or envelope.bidder_id != bidder_id:
                        logger.warning(f"Bidder ID mismatch: expected '{bidder_id}', got '{envelope.bidder_id}'.")
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="BIDDER_ID_MISMATCH",
                            retryable=False,
                            message="Intelligence service bidder ID mismatch.",
                        )

                    if envelope.document_sha256 != document_sha256:
                        logger.warning("Document SHA-256 digest mismatch in response envelope.")
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="DOCUMENT_SHA256_MISMATCH",
                            retryable=False,
                            message="Intelligence service document SHA-256 digest mismatch.",
                        )

                    if envelope.status in ("FAILED", "ERROR"):
                        logger.warning("Intelligence service reported status FAILED/ERROR.")
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="AI_SERVICE_REQUEST_REJECTED",
                            retryable=False,
                            message="Intelligence service reported processing failure.",
                        )

                    raw_facts = envelope.facts if envelope.facts is not None else resp_data.get("facts", resp_data.get("data"))
                    if not isinstance(raw_facts, list):
                        return AIServiceResult(
                            success=False,
                            data=None,
                            error_code="SCHEMA_VALIDATION_FAILED",
                            retryable=False,
                            message="Intelligence service response failed schema validation.",
                        )

                    validated_facts = []
                    for item in raw_facts:
                        if not isinstance(item, dict):
                            return AIServiceResult(
                                success=False,
                                data=None,
                                error_code="SCHEMA_VALIDATION_FAILED",
                                retryable=False,
                                message="Intelligence service response failed schema validation.",
                            )
                        try:
                            # Scrub model-supplied authority and identity fields
                            item_meta = dict(item.get("metadata_json") or {}) if isinstance(item.get("metadata_json"), dict) else {}
                            for scrub_key in ("is_approved", "approved_by", "approved_at", "approval_status", "approved", "request_id", "document_id", "document_sha256", "bidder_id", "tender_id"):
                                item.pop(scrub_key, None)
                                item_meta.pop(scrub_key, None)

                            # Construct backend-owned identity provenance
                            item_meta["request_id"] = req_id
                            item_meta["document_id"] = document_id
                            item_meta["document_sha256"] = document_sha256
                            if envelope.provider_model:
                                item_meta["provider_model"] = envelope.provider_model

                            item["metadata_json"] = item_meta

                            fact_obj = ExtractedFactCreate.model_validate(item)
                            validated_facts.append(fact_obj.model_dump())
                        except ValidationError as val_err:
                            logger.error(f"Extracted fact validation failed: {val_err}")
                            return AIServiceResult(
                                success=False,
                                data=None,
                                error_code="SCHEMA_VALIDATION_FAILED",
                                retryable=False,
                                message="Intelligence service response failed schema validation.",
                            )

                    return AIServiceResult(
                        success=True,
                        data=validated_facts,
                        error_code=None,
                        retryable=False,
                        message=f"Successfully extracted {len(validated_facts)} facts from bidder document.",
                    )

            except (httpx.ConnectError, httpx.ConnectTimeout) as conn_err:
                logger.warning(f"Connection error on attempt {attempt}/{max_attempts}: {conn_err}")
                if attempt < max_attempts:
                    continue
                return AIServiceResult(
                    success=False,
                    data=None,
                    error_code="AI_SERVICE_UNAVAILABLE",
                    retryable=True,
                    message="Intelligence service connection failed.",
                )
            except httpx.ReadTimeout:
                logger.warning("Read timeout from intelligence service.")
                return AIServiceResult(
                    success=False,
                    data=None,
                    error_code="AI_SERVICE_UNAVAILABLE",
                    retryable=False,
                    message="Intelligence service request timed out.",
                )
            except httpx.TimeoutException:
                logger.warning("Timeout exception from intelligence service.")
                return AIServiceResult(
                    success=False,
                    data=None,
                    error_code="AI_SERVICE_UNAVAILABLE",
                    retryable=False,
                    message="Intelligence service request timed out.",
                )
            except httpx.RequestError as req_err:
                logger.warning(f"Transport failure: {req_err}")
                return AIServiceResult(
                    success=False,
                    data=None,
                    error_code="AI_SERVICE_UNAVAILABLE",
                    retryable=False,
                    message="Intelligence service transport failure.",
                )
            except Exception as exc:
                logger.error(f"Unexpected internal error: {type(exc).__name__}")
                return AIServiceResult(
                    success=False,
                    data=None,
                    error_code="AI_SERVICE_UNAVAILABLE",
                    retryable=False,
                    message="Intelligence service request failed.",
                )

        return AIServiceResult(
            success=False,
            data=None,
            error_code="AI_SERVICE_UNAVAILABLE",
            retryable=True,
            message="Intelligence service unavailable after maximum retries.",
        )
