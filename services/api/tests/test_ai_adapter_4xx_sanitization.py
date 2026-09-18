import httpx
import pytest
from app.services.ai_adapter import _sanitize_4xx_error, AIServiceAdapter


def test_sanitize_413_document_too_large():
    resp = httpx.Response(status_code=413, json={'detail': 'Document size exceeds maximum allowed size'})
    code, msg = _sanitize_4xx_error(413, resp)
    assert code == 'DOCUMENT_TOO_LARGE'
    assert 'exceeds' in msg.lower()


def test_sanitize_422_sha256_mismatch():
    resp = httpx.Response(status_code=422, json={'detail': 'Document SHA-256 digest mismatch'})
    code, msg = _sanitize_4xx_error(422, resp)
    assert code == 'DOCUMENT_SHA256_MISMATCH'
    assert 'sha-256' in msg.lower()


def test_sanitize_422_parse_failed():
    resp = httpx.Response(status_code=422, json={'detail': 'PDF parsing error: Stream has ended unexpectedly'})
    code, msg = _sanitize_4xx_error(422, resp)
    assert code == 'DOCUMENT_PARSE_FAILED'
    assert 'corrupt' in msg.lower() or 'truncated' in msg.lower()


def test_sanitize_422_invalid_payload():
    resp = httpx.Response(status_code=422, json={'detail': 'Invalid base64 document bytes: bad padding'})
    code, msg = _sanitize_4xx_error(422, resp)
    assert code == 'INVALID_DOCUMENT_PAYLOAD'
    assert 'invalid' in msg.lower()


def test_sanitize_400_rejected():
    resp = httpx.Response(status_code=400, json={'detail': 'Malformed request'})
    code, msg = _sanitize_4xx_error(400, resp)
    assert code == 'AI_SERVICE_REQUEST_REJECTED'
    assert 'rejected' in msg.lower()


def test_sanitize_never_exposes_internal_paths_or_keys():
    resp = httpx.Response(status_code=422, json={'detail': 'Error in /var/data/secret_keys/internal.pem: parse failed'})
    code, msg = _sanitize_4xx_error(422, resp)
    assert '/var' not in msg
    assert 'secret' not in msg
    assert '.pem' not in msg
