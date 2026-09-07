"""Authorized document materialization; never invents cloud credentials for raw object URIs."""
from __future__ import annotations

import ipaddress
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Union
from urllib.parse import unquote, urlparse

import httpx

MAX_DOCUMENT_BYTES = int(os.getenv("ARGUS_MAX_DOCUMENT_BYTES", str(30 * 1024 * 1024)))

class DocumentResolutionError(ValueError): pass

def _public_host(hostname: str) -> None:
    """Reject literal/private endpoints before downloading signed URLs."""
    try:
        address = ipaddress.ip_address(hostname)
        if address.is_private or address.is_loopback or address.is_link_local: raise DocumentResolutionError("private download hosts are not allowed")
    except ValueError:
        # DNS resolution is intentionally delegated to the platform/network policy.
        if not hostname: raise DocumentResolutionError("download URL requires a hostname")

def _allowed_s3_bucket(bucket: str) -> None:
    allowed = {item.strip() for item in os.getenv("ARGUS_S3_ALLOWED_BUCKETS", "").split(",") if item.strip()}
    if not allowed or bucket not in allowed:
        raise DocumentResolutionError("S3 bucket is not allowlisted")

@contextmanager
def resolved_document(uri: Union[str, Path]) -> Iterator[Path]:
    """Yield a local path. HTTPS is only for caller-authorized signed download URLs."""
    value = str(uri); parsed = urlparse(value)
    if parsed.scheme == "s3":
        if not parsed.netloc or not parsed.path.strip("/"): raise DocumentResolutionError("S3 URI requires bucket and object key")
        with _resolved_s3(parsed.netloc, unquote(parsed.path.lstrip("/"))) as path:
            yield path
        return
    if parsed.scheme in ("", "file"):
        path = Path(unquote(parsed.path if parsed.scheme else value))
        if not path.is_file(): raise DocumentResolutionError("document not found")
        yield path
        return
    if parsed.scheme != "https": raise DocumentResolutionError("document_uri must be local/file:// or an authorized https signed-download URL")
    if parsed.username or parsed.password: raise DocumentResolutionError("credential-bearing URLs are not allowed")
    _public_host(parsed.hostname or "")
    suffix = Path(parsed.path).suffix or ".bin"; temporary_path = None
    try:
        with httpx.stream("GET", value, follow_redirects=False, timeout=20.0) as response:
            response.raise_for_status()
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > MAX_DOCUMENT_BYTES: raise DocumentResolutionError("document exceeds maximum allowed size")
            with tempfile.NamedTemporaryFile(prefix="argus-document-", suffix=suffix, delete=False) as handle:
                temporary_path = Path(handle.name); total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > MAX_DOCUMENT_BYTES: raise DocumentResolutionError("document exceeds maximum allowed size")
                    handle.write(chunk)
        yield temporary_path
    except httpx.HTTPError as exc:
        raise DocumentResolutionError("authorized document download failed") from exc
    finally:
        if temporary_path and temporary_path.exists(): temporary_path.unlink()

@contextmanager
def _resolved_s3(bucket: str, key: str) -> Iterator[Path]:
    _allowed_s3_bucket(bucket)
    try:
        import boto3
    except ImportError as exc:
        raise DocumentResolutionError("S3 document resolution requires boto3") from exc
    suffix = Path(key).suffix or ".bin"
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix="argus-document-", suffix=suffix, delete=False) as handle:
            temporary_path = Path(handle.name)
            boto3.client("s3").download_fileobj(bucket, key, handle)
        if temporary_path.stat().st_size > MAX_DOCUMENT_BYTES:
            raise DocumentResolutionError("document exceeds maximum allowed size")
        yield temporary_path
    except DocumentResolutionError:
        raise
    except Exception as exc:
        raise DocumentResolutionError("authorized S3 document download failed") from exc
    finally:
        if temporary_path and temporary_path.exists(): temporary_path.unlink()
