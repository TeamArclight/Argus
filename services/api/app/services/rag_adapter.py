from datetime import datetime, timezone
from typing import Any
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

        # Helper to retrieve from backend canonical demo fixtures
        def _match_demo_chunks() -> list[EvidenceRead]:
            from app.core.demo_fixtures import DEMO_RAG_CHUNKS
            q_lower = request.query.lower()
            matches: list[EvidenceRead] = []
            keywords = {
                "turnover": ["turnover", "annual turnover", "revenue"],
                "experience": ["experience", "operating experience", "track record", "similar contract"],
                "msme": ["msme", "mse", "udyam", "relaxation", "exemption"],
                "emd": ["emd", "earnest money", "bid security"],
                "warranty": ["warranty", "guarantee", "amc"],
                "jv": ["joint venture", "jv", "consortium"],
            }
            matched_keys = [k for k, terms in keywords.items() if any(t in q_lower for t in terms)]
            if not matched_keys:
                return []
            for chunk in DEMO_RAG_CHUNKS:
                if request.tender_id and chunk.get("tender_id") and chunk["tender_id"] != request.tender_id and "DEMO" not in request.tender_id.upper():
                    continue
                c_text = (chunk["snippet"] + " " + chunk.get("clause", "")).lower()
                for mk in matched_keys:
                    if any(term in c_text for term in keywords[mk]):
                        matches.append(EvidenceRead.model_validate(chunk))
                        break
            return matches

        if not url or not url.strip():
            is_demo_query = bool(
                request.tender_id
                and any(
                    k in request.tender_id.lower()
                    for k in ("demo", "gem_2026", "tender_gem")
                )
            )
            if is_demo_query:
                demo_matches = _match_demo_chunks()
                if demo_matches:
                    return RAGQueryResponse(
                        query=request.query,
                        results=demo_matches,
                        retrieved_at=now,
                    )
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
                elif resp.status_code in (408, 429):
                    return RAGQueryResponse(
                        query=request.query,
                        results=[],
                        retrieved_at=now,
                        error_code="RAG_SERVICE_UNAVAILABLE",
                        error_message=f"RAG service rate limited or timed out (HTTP {resp.status_code}).",
                    )
                elif 400 <= resp.status_code < 500:
                    return RAGQueryResponse(
                        query=request.query,
                        results=[],
                        retrieved_at=now,
                        error_code="RAG_SERVICE_REQUEST_REJECTED",
                        error_message=f"RAG service rejected the request (HTTP {resp.status_code}).",
                    )
                elif resp.status_code >= 500:
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

    async def ingest_document(
        self,
        document_id: str,
        title: str,
        document_uri: str,
        document_type: str,
        *,
        tender_id: str | None = None,
        source_uri: str | None = None,
        clause: str | None = None,
        security_level: str = "INTERNAL",
        file_bytes: bytes | None = None,
        text: str | None = None,
    ) -> dict[str, Any]:
        """Ingests a parsed document into the intelligence service RAG index."""
        url = settings.ARGUS_INTELLIGENCE_RAG_INGEST_URL
        if not url or not url.strip():
            if settings.ARGUS_INTELLIGENCE_BASE_URL:
                url = f"{settings.ARGUS_INTELLIGENCE_BASE_URL.rstrip('/')}/rag-ingest"
            elif settings.ARGUS_INTELLIGENCE_RAG_URL:
                base = settings.ARGUS_INTELLIGENCE_RAG_URL.rsplit("/", 1)[0]
                url = f"{base}/rag-ingest"
            else:
                return {
                    "success": False,
                    "error_code": "RAG_SERVICE_UNAVAILABLE",
                    "message": "ARGUS RAG ingest URL is not configured.",
                    "chunks_indexed": 0,
                }

        headers: dict[str, str] = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "ARGUS-Procurement-Platform/1.0",
        }
        if settings.ARGUS_INTELLIGENCE_API_KEY:
            headers["Authorization"] = f"Bearer {settings.ARGUS_INTELLIGENCE_API_KEY}"

        if file_bytes is None and document_uri:
            try:
                from app.storage.factory import get_storage_provider
                storage = get_storage_provider()
                try:
                    file_bytes = storage.read_file(document_uri)
                except Exception:
                    if storage.file_exists(document_uri):
                        file_bytes = storage.read_file(document_uri)
            except Exception:
                pass

        payload: dict[str, Any] = {
            "document_id": document_id,
            "title": title,
            "document_uri": document_uri,
            "document_type": document_type,
            "source_uri": source_uri or document_uri,
            "tender_id": tender_id,
            "clause": clause,
            "security_level": security_level,
        }
        if file_bytes:
            import base64
            payload["file_bytes_base64"] = base64.b64encode(file_bytes).decode("ascii")
        if text:
            payload["text"] = text

        timeout = httpx.Timeout(settings.ARGUS_INTELLIGENCE_READ_TIMEOUT_SECONDS)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "success": True,
                        "document_id": document_id,
                        "chunks_indexed": data.get("chunks_indexed", 0),
                    }
                return {
                    "success": False,
                    "error_code": f"HTTP_{resp.status_code}",
                    "message": resp.text[:200],
                    "chunks_indexed": 0,
                }
        except Exception as exc:
            return {
                "success": False,
                "error_code": "RAG_INGEST_FAILED",
                "message": str(exc),
                "chunks_indexed": 0,
            }

