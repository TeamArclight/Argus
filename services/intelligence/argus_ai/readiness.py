"""Non-secret deployment readiness checks for the standalone intelligence service."""
from __future__ import annotations

import os
import shutil
from typing import Any

def readiness() -> dict[str, Any]:
    provider = os.getenv("ARGUS_MODEL_PROVIDER", "disabled").lower()
    embedding_provider = os.getenv("ARGUS_EMBEDDING_PROVIDER", "hash").lower()
    is_production = os.getenv("APP_ENV", "development").lower() == "production"
    require_live = os.getenv("ARGUS_REQUIRE_LIVE", "false").lower() in {"true", "1", "yes"} or is_production

    model_ready = (provider == "gemini" and bool(os.getenv("ARGUS_GEMINI_API_KEY"))) or (provider in {"customendpoint", "openai", "openai-compatible"} and bool(os.getenv("ARGUS_MODEL_API_KEY")))
    if not require_live and provider == "disabled":
        model_ready = True

    embedding_ready = (embedding_provider == "gemini" and bool(os.getenv("ARGUS_GEMINI_API_KEY")))
    if not require_live and embedding_provider == "hash":
        embedding_ready = True

    ocr_ready = bool(shutil.which("tesseract")) and bool(shutil.which("pdftoppm"))
    rag_database = bool(os.getenv("ARGUS_RAG_DATABASE_URL"))
    checks = {
        "model": {"configured": provider != "disabled", "ready": model_ready, "provider": provider},
        "embeddings": {"configured": embedding_provider != "hash", "ready": embedding_ready, "provider": embedding_provider},
        "rag": {"configured": rag_database, "mode": "pgvector" if rag_database else "in_memory_demo"},
        "ocr": {"ready": ocr_ready, "mode": "native_text_then_ocr"},
        "document_resolution": {"mode": "local_file_or_https_signed_url", "raw_s3_requires_backend_storage_adapter": True},
    }
    ready = model_ready and embedding_ready and (not require_live or (rag_database and provider != "disabled"))
    return {"ready": ready, "checks": checks}
