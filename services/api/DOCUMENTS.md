# ARGUS Document Ingestion Backend Architecture

## Overview

The ARGUS Document Ingestion system provides production-shaped, secure, auditable file ingestion for GeM tender notices and bidder submission documents. It enforces strict validation, path traversal containment, file integrity hashing, unique immutable storage key generation, atomic storage persistence, machine-readable error responses, and fine-grained Role-Based Access Control (RBAC).

---

## Storage Abstraction Layer & Unique Key Architecture

Document storage is decoupled from API logic via an abstract `StorageProvider` interface (`app/storage/base.py`).

### Immutable Key Generation
- **Internal Storage Keys**: File paths stored on disk use unique, collision-proof keys:
  - Tenders: `tenders/<tender_id>/<document_id>.<ext>`
  - Bidders: `bidders/<bidder_id>/<document_id>.<ext>`
- **Original Filename Preservation**: The user's original filename is stored as display metadata (`filename` column in database) and is never used as the physical storage key.
- **Same Filename Handling**: Re-uploading a file with the same original filename but different content bytes creates a distinct document record with a unique key, preserving both physical byte streams and their respective SHA-256 digests.

### `LocalStorageProvider` (`app/storage/local.py`)
- **Root Containment**: All file operations strictly enforce that resolved paths reside within `ARGUS_STORAGE_LOCAL_PATH` using `Path.is_relative_to()`. Any attempt at path traversal (e.g. `../../etc/passwd`) raises a `ValueError`.
- **Atomic File Operations**: File writes use unique temporary files (`.tmp_<uuid>_<filename>`) in the target directory and atomic `os.replace()` operations to prevent corrupted or partial file reads.
- **Overwrite Guard**: Checks for existing target keys and raises `FileExistsError` to prevent accidental overwrites.

---

## Single Owner Database Constraint

Database schema enforces that every `Document` belongs to **exactly one** parent entity (either a Tender or a Bidder):
- **DB Check Constraint**: `ck_documents_single_owner`:
  `(tender_id IS NOT NULL AND bidder_id IS NULL) OR (tender_id IS NULL AND bidder_id IS NOT NULL)`
- Both tender-only and bidder-only owner rows are supported; historical bidder-owned rows remain valid.

---

## Document Validation & Integrity (`app/services/document_validation_service.py`)

Every uploaded file undergoes multi-phase validation before storage:

1. **Filename Sanitation**:
   - Removes null bytes, path separators, and non-printable characters.
   - Extracts basename only to prevent directory traversal in user-supplied filenames.

2. **Zero-Byte Check**:
   - Rejects 0-byte empty files with `400 Bad Request` (`code: DOCUMENT_EMPTY`).

3. **Extension Whitelist & Supported Formats**:
   - Supported extensions in Phase 7: `.pdf`, `.png`, `.jpg`, `.jpeg`.
   - Complex archive/office format parsing (e.g. `.docx`) is deferred for Phase 7.
   - Rejects executable or dangerous extension patterns (`.exe`, `.sh`, `.bat`, `.py`, `.js`, `.docx`, etc.) with `415 Unsupported Media Type` (`code: DOCUMENT_TYPE_UNSUPPORTED`).
   - Detects and rejects dangerous double-extension attacks (e.g. `invoice.pdf.exe`).

4. **Declared MIME Consistency Validation**:
   - Compares user-declared `Content-Type` against expected MIME types for the extension.
   - If declared MIME is `application/octet-stream` (or absent), generic binary fallback is permitted provided extension and magic signature match.
   - Specific MIME mismatches (e.g. `.pdf` with declared MIME `image/png`) are rejected with `415 Unsupported Media Type` (`code: DOCUMENT_MIME_MISMATCH`).

5. **Magic Header Signature Verification**:
   - Inspects raw binary magic bytes (`%PDF-` for PDF, `\x89PNG` for PNG, `\xff\xd8\xff` for JPEG).
   - Rejects signature mismatches with `415 Unsupported Media Type` (`code: DOCUMENT_SIGNATURE_INVALID`).
   - *Disclaimer*: Magic header verification checks binary structure compliance but does **not** constitute deep malware analysis or antivirus scanning.

6. **SHA-256 Digest**:
   - Calculates deterministic SHA-256 checksum for payload deduplication and audit tracking.

---

## File Size Limits & Deduplication Policy

- **Upload Size Limit**: Upload file size is capped by `settings.ARGUS_MAX_UPLOAD_MB` (default: 20 MB). Streams in 64 KB chunks and aborts with `413 Request Entity Too Large` (`code: DOCUMENT_SIZE_EXCEEDED`).
- **Deduplication Policy**: Uploading identical file bytes for the same owner (`tender_id` or `bidder_id`) returns `409 Conflict` (`code: DOCUMENT_DUPLICATE`).

---

## API Endpoints & RBAC Authorization

| Method | Endpoint | Allowed Roles | Description |
|---|---|---|---|
| `POST` | `/api/v1/tenders/{id}/documents` | `ADMIN`, `PROCUREMENT_OFFICER` | Upload raw tender document |
| `GET` | `/api/v1/tenders/{id}/documents` | All authenticated roles | List tender documents |
| `POST` | `/api/v1/bidders/{id}/documents` | `ADMIN`, `PROCUREMENT_OFFICER` | Upload bidder document |
| `GET` | `/api/v1/bidders/{id}/documents` | All authenticated roles | List bidder documents |
| `GET` | `/api/v1/documents/{id}` | All authenticated roles | Get document metadata |
| `GET` | `/api/v1/documents/{id}/content` | All authenticated roles | Download stored file content |

### Evidence Retention & Deletion Policy
- Public file deletion endpoints (`DELETE /api/v1/documents/{id}`) are **deferred** to a dedicated future procurement evidence retention policy to prevent accidental data loss or breaking historical compliance run references.

---

## Safe Error Handling & Transaction Safety

- **Information Leakage Prevention**: Errors return generic messages and stable machine-readable error codes (`DOCUMENT_STORAGE_FAILED`, `DOCUMENT_NOT_FOUND`, `DOCUMENT_SIGNATURE_INVALID`, `DOCUMENT_MIME_MISMATCH`, `DOCUMENT_DUPLICATE`, `DOCUMENT_EMPTY`, `DOCUMENT_SIZE_EXCEEDED`). Internal exceptions, SQL errors, connection strings, and filesystem paths are logged silently to system logs and never leaked in HTTP responses.
- **Single-Transaction Persistence**: Metadata creation and `DOCUMENT_UPLOADED` audit event creation execute within a single database transaction. If database commit or audit logging fails, the transaction rolls back and ONLY the newly staged physical file (`storage_uri`) is deleted. Existing stored files are strictly preserved.
