import io
import re
import zipfile
from pathlib import Path
from typing import NamedTuple

from fastapi import HTTPException, status

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".docx"}
DANGEROUS_EXTENSIONS = {
    "exe", "bat", "cmd", "ps1", "sh", "js", "html", "htm", "php",
    "zip", "rar", "7z", "tar", "gz", "bz2", "jar", "vbs", "msi", "scr", "dll", "so", "sys"
}

MAGIC_SIGNATURES = {
    ".pdf": [b"%PDF-"],
    ".png": [b"\x89PNG\r\n\x1a\n"],
    ".jpg": [b"\xff\xd8\xff"],
    ".jpeg": [b"\xff\xd8\xff"],
    ".docx": [b"PK\x03\x04"],
}

MIME_TYPE_MAP = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


class ValidatedFileMetadata(NamedTuple):
    sanitized_filename: str
    extension: str
    content_type: str
    size_bytes: int
    sha256_hex: str


class DocumentValidationService:
    """Validates document payloads against file size, extension, double-extensions, and magic signatures."""

    @staticmethod
    def sanitize_filename(raw_filename: str) -> str:
        if not raw_filename:
            return "unnamed_document"
        
        # Remove null bytes
        cleaned = raw_filename.replace("\x00", "")
        # Get basename only
        cleaned = Path(cleaned).name
        # Remove backslashes and forward slashes if any remaining
        cleaned = str(cleaned).replace("/", "_").replace("\\", "_")
        # Remove any non-printable ASCII
        cleaned = re.sub(r"[^\w\.\-\_]", "_", cleaned)
        return cleaned if cleaned else "unnamed_document"

    @classmethod
    def validate_file(cls, filename: str, content: bytes) -> ValidatedFileMetadata:
        # 1. Zero-byte check
        size_bytes = len(content)
        if size_bytes == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "DOCUMENT_EMPTY",
                    "message": "Empty file uploaded (0 bytes).",
                    "details": {},
                },
            )

        # 2. Filename & extension safety
        sanitized = cls.sanitize_filename(filename)
        ext = Path(sanitized).suffix.lower()

        if not ext or ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail={
                    "code": "DOCUMENT_TYPE_UNSUPPORTED",
                    "message": f"Unsupported file extension '{ext}'. Supported formats: PDF, PNG, JPG, JPEG, DOCX.",
                    "details": {"allowed_extensions": sorted(list(ALLOWED_EXTENSIONS))},
                },
            )

        # Double-extension and dangerous component check
        name_parts = sanitized.lower().split(".")
        if len(name_parts) > 2:
            for part in name_parts[1:-1]:
                if part in DANGEROUS_EXTENSIONS:
                    raise HTTPException(
                        status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                        detail={
                            "code": "DOCUMENT_TYPE_UNSUPPORTED",
                            "message": f"Dangerous double extension pattern detected in filename '{sanitized}'.",
                            "details": {"suspicious_extension": part},
                        },
                    )

        # 3. Magic Header Signature Validation
        signatures = MAGIC_SIGNATURES.get(ext, [])
        header = content[:16]
        matches_sig = any(header.startswith(sig) for sig in signatures)

        if not matches_sig:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail={
                    "code": "DOCUMENT_SIGNATURE_INVALID",
                    "message": f"File magic signature does not match declared extension '{ext}'.",
                    "details": {"declared_extension": ext},
                },
            )

        # Detailed DOCX Zip Structure Check
        if ext == ".docx":
            try:
                with zipfile.ZipFile(io.BytesIO(content)) as zf:
                    namelist = zf.namelist()
                    if not any(name.startswith("word/") or name == "[Content_Types].xml" for name in namelist):
                        raise ValueError("Missing Office Word ZIP contents")
            except Exception as e:
                raise HTTPException(
                    status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    detail={
                        "code": "DOCUMENT_SIGNATURE_INVALID",
                        "message": "Invalid DOCX file structure: payload is not a valid Word processing document.",
                        "details": {},
                    },
                ) from e

        content_type = MIME_TYPE_MAP.get(ext, "application/octet-stream")
        import hashlib
        sha256_hex = hashlib.sha256(content).hexdigest()

        return ValidatedFileMetadata(
            sanitized_filename=sanitized,
            extension=ext,
            content_type=content_type,
            size_bytes=size_bytes,
            sha256_hex=sha256_hex,
        )
