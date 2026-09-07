# ARGUS Document Ingestion Backend Architecture

## Overview

The ARGUS Document Ingestion system provides production-shaped, secure, auditable file ingestion for GeM tender notices and bidder submission documents. It enforces strict validation, path traversal containment, file integrity hashing, atomic storage persistence, machine-readable error responses, and fine-grained Role-Based Access Control (RBAC).

---

## Storage Abstraction Layer

Document storage is decoupled from API logic via an abstract `StorageProvider` interface (`app/storage/base.py`).

### `LocalStorageProvider` (`app/storage/local.py`)
- **Root Containment**: All file operations strictly enforce that resolved paths reside within `ARGUS_STORAGE_LOCAL_PATH` using `Path.is_relative_to()`. Any attempt at path traversal (e.g. `../../etc/passwd`) raises a `ValueError`.
- **Atomic File Operations**: File writes use temporary files (`.tmp_*`) in the target directory and atomic `os.replace()` operations to prevent corrupted or partial file reads.
- **Key Resolution**: Sanitizes input storage keys, removing windows drive letters and leading slashes.

### Storage Factory (`app/storage/factory.py`)
Provides `get_storage_provider()`, returning the active implementation based on `settings.ARGUS_STORAGE_BACKEND`.

---

## Document Validation & Integrity (`app/services/document_validation_service.py`)

Every uploaded file undergoes multi-phase validation before storage:

1. **Filename Sanitation**:
   - Removes null bytes, path separators, and non-printable characters.
   - Extracts basename only to prevent directory traversal in user-supplied filenames.

2. **Zero-Byte Check**:
   - Rejects 0-byte empty files with `400 Bad Request` (`code: DOCUMENT_EMPTY`).

3. **Extension Whitelist & Executable Checks**:
   - Allowed extensions: `.pdf`, `.png`, `.jpg`, `.jpeg`, `.docx`.
   - Rejects executable or dangerous extension patterns (`.exe`, `.sh`, `.bat`, `.py`, `.js`, etc.) with `415 Unsupported Media Type` (`code: DOCUMENT_TYPE_UNSUPPORTED`).
   - Detects and rejects dangerous double-extension attacks (e.g. `invoice.pdf.exe`).

4. **Magic Signature Header Verification**:
   - Inspects raw binary magic bytes (`%PDF-` for PDF, `\x89PNG` for PNG, `\xff\xd8\xff` for JPEG, `PK\x03\x04` for DOCX).
   - For `.docx` files, validates inner Office Open XML zip file structure (`word/` directory or `[Content_Types].xml`).
   - Rejects mismatches with `415 Unsupported Media Type` (`code: DOCUMENT_SIGNATURE_INVALID`).

5. **SHA-256 Digest**:
   - Calculates deterministic SHA-256 checksum for payload deduplication and audit tracking.

---

## File Size Limits & Streaming Checks

- Upload file size is capped by `settings.ARGUS_MAX_UPLOAD_MB` (default: 20 MB).
- File reading streams in 64 KB chunks. If incoming payload size exceeds max limit, ingestion aborts immediately with `413 Request Entity Too Large` (`code: DOCUMENT_SIZE_EXCEEDED`).

---

## Duplicate Conflict Handling

- Hashes uploaded file content (SHA-256).
- Checks DB for pre-existing `Document` linked to the same owner (`tender_id` or `bidder_id`) with matching `sha256`.
- Rejects duplicates with `409 Conflict` (`code: DOCUMENT_DUPLICATE`).

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
| `DELETE` | `/api/v1/documents/{id}` | `ADMIN`, `PROCUREMENT_OFFICER` | Delete document & storage file |

---

## Audit Logging

Auditable actions emit structured `AuditEvent` records with caller attribution (`actor_id`, `actor_role`):
- `DOCUMENT_UPLOADED`: Records `tender_id`/`bidder_id`, `document_type`, `filename`, `sha256`, `size_bytes`, `storage_uri`.
- `DOCUMENT_DELETED`: Records target `document_id`, owner IDs, and `filename`.

---

## Failure Recovery & Rollback Safety

- If metadata database persistence fails after storing the physical file, the transaction rolls back and stored file content is immediately deleted via `provider.delete_file()`, preventing orphan files on storage.
