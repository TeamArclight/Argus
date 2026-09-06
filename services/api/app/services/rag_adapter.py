from datetime import datetime, timezone
import httpx
from pydantic import ValidationError

from app.core.config import settings
from app.schemas.canonical import EvidenceRead, RAGQueryRequest, RAGQueryResponse


class RAGServiceAdapter:
    """Backend-facing RAG retrieval adapter calling intelligence RAG gateway.

    Provides structured evidence retrieval interface for policy, tender clauses, and exemptions.
    Does NOT implement vector db / embeddings directly in API service.
    """

    async def retrieve(self, request: RAGQueryRequest) -> RAGQueryResponse:
        now = datetime.now(timezone.utc)

        url = settings.ARGUS_INTELLIGENCE_RAG_URL

        if not url or not url.strip():
            return RAGQueryResponse(
                query=request.query,
                results=[],
                retrieved_at=now,
                error_code="RAG_SERVICE_UNAVAILABLE",
                error_message="ARGUS RAG intelligence service URL is unconfigured or unavailable.",
            )

        headers: dict[str, str] = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "ARGUS-Procurement-Platform/1.0",
        }
        if settings.ARGUS_INTELLIGENCE_API_KEY:
            headers["Authorization"] = f"Bearer {settings.ARGUS_INTELLIGENCE_API_KEY}"

        timeout = httpx.Timeout(settings.REQUEST_TIMEOUT_SECONDS)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    url,
                    headers=headers,
                    json=request.model_dump(mode="json"),
                )

                if resp.status_code == 400:
                    return RAGQueryResponse(
                        query=request.query,
                        results=[],
                        retrieved_at=now,
                        error_code="RAG_SERVICE_REQUEST_REJECTED",
                        error_message="RAG service rejected the request (HTTP 400).",
                    )
                elif resp.status_code in (401, 403):
                    return RAGQueryResponse(
                        query=request.query,
                        results=[],
                        retrieved_at=now,
                        error_code="RAG_SERVICE_AUTH_ERROR",
                        error_message=f"RAG service authentication error (HTTP {resp.status_code}).",
                    )
                elif resp.status_code == 404:
                    return RAGQueryResponse(
                        query=request.query,
                        results=[],
                        retrieved_at=now,
                        error_code="RAG_SERVICE_ENDPOINT_NOT_FOUND",
                        error_message="RAG service endpoint not found (HTTP 404).",
                    )
                elif resp.status_code in (408, 429, 500, 502, 503, 504) or resp.status_code >= 400:
                    return RAGQueryResponse(
                        query=request.query,
                        results=[],
                        retrieved_at=now,
                        error_code="RAG_SERVICE_UNAVAILABLE",
                        error_message=f"RAG service HTTP error (status {resp.status_code}).",
                    )

                try:
                    resp_data = resp.json()
                except Exception as parse_err:
                    return RAGQueryResponse(
                        query=request.query,
                        results=[],
                        retrieved_at=now,
                        error_code="SCHEMA_VALIDATION_FAILED",
                        error_message=f"Failed to parse RAG response JSON: {parse_err}",
                    )

                if not isinstance(resp_data, dict):
                    return RAGQueryResponse(
                        query=request.query,
                        results=[],
                        retrieved_at=now,
                        error_code="SCHEMA_VALIDATION_FAILED",
                        error_message="RAG service response must be a JSON object.",
                    )

                raw_results = resp_data.get("results")
                if not isinstance(raw_results, list):
                    return RAGQueryResponse(
                        query=request.query,
                        results=[],
                        retrieved_at=now,
                        error_code="SCHEMA_VALIDATION_FAILED",
                        error_message="RAG service response missing 'results' list.",
                    )

                validated_evidence = []
                for item in raw_results:
                    try:
                        evidence_obj = EvidenceRead.model_validate(item)
                        validated_evidence.append(evidence_obj)
                    except ValidationError as val_err:
                        return RAGQueryResponse(
                            query=request.query,
                            results=[],
                            retrieved_at=now,
                            error_code="SCHEMA_VALIDATION_FAILED",
                            error_message=f"Evidence item failed schema validation: {val_err}",
                        )

                return RAGQueryResponse(
                    query=request.query,
                    results=validated_evidence,
                    retrieved_at=now,
                )

        except httpx.TimeoutException:
            return RAGQueryResponse(
                query=request.query,
                results=[],
                retrieved_at=now,
                error_code="RAG_SERVICE_UNAVAILABLE",
                error_message=f"RAG service request timed out after {settings.REQUEST_TIMEOUT_SECONDS}s.",
            )
        except httpx.RequestError as req_err:
            return RAGQueryResponse(
                query=request.query,
                results=[],
                retrieved_at=now,
                error_code="RAG_SERVICE_UNAVAILABLE",
                error_message=f"RAG service transport failure: {req_err}",
            )
        except Exception as exc:
            return RAGQueryResponse(
                query=request.query,
                results=[],
                retrieved_at=now,
                error_code="RAG_SERVICE_UNAVAILABLE",
                error_message=f"Unexpected internal error: {exc}",
            )
