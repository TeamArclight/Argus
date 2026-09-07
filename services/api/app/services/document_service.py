from fastapi import HTTPException, UploadFile, status
from sqlalchemy.orm import Session
from app.audit.logger import AuditLogger
from app.core.config import settings
from app.models.domain import Bidder, Document, Tender
from app.schemas.canonical import AuthenticatedPrincipal, DocumentType
from app.services.document_validation_service import DocumentValidationService
from app.storage.factory import get_storage_provider


class DocumentService:
    """Core service managing document ingestion, storage, retrieval, and deletion."""

    @staticmethod
    async def upload_tender_document(
        db: Session,
        tender_id: str,
        file: UploadFile,
        document_type: DocumentType,
        principal: AuthenticatedPrincipal,
    ) -> Document:
        tender = db.query(Tender).filter(Tender.id == tender_id).first()
        if not tender:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tender with ID {tender_id} not found.",
            )

        max_bytes = settings.ARGUS_MAX_UPLOAD_MB * 1024 * 1024
        contents = bytearray()
        while chunk := await file.read(65536):
            contents.extend(chunk)
            if len(contents) > max_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail={
                        "code": "DOCUMENT_SIZE_EXCEEDED",
                        "message": f"Document file size exceeds maximum limit of {settings.ARGUS_MAX_UPLOAD_MB} MB.",
                        "details": {"max_size_mb": settings.ARGUS_MAX_UPLOAD_MB},
                    },
                )
        file_bytes = bytes(contents)

        val_meta = DocumentValidationService.validate_file(
            filename=file.filename or "unnamed_document",
            content=file_bytes,
        )
        sanitized_filename = val_meta.sanitized_filename
        sha256_hash = val_meta.sha256_hex

        existing_doc = (
            db.query(Document)
            .filter(Document.tender_id == tender_id, Document.sha256 == sha256_hash)
            .first()
        )
        if existing_doc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "DOCUMENT_DUPLICATE",
                    "message": f"A document with identical content (SHA-256: {sha256_hash}) already exists for this tender.",
                    "details": {"existing_document_id": existing_doc.id, "sha256": sha256_hash},
                },
            )

        provider = get_storage_provider()
        target_key = f"tenders/{tender_id}/{sanitized_filename}"
        storage_uri = provider.store_file(file_bytes, target_key=target_key)

        doc = Document(
            tender_id=tender_id,
            bidder_id=None,
            document_type=document_type,
            filename=sanitized_filename,
            storage_uri=storage_uri,
            content_type=val_meta.content_type,
            size_bytes=val_meta.size_bytes,
            sha256=sha256_hash,
            metadata_json={"original_filename": file.filename},
        )
        tender.raw_document_uri = storage_uri

        try:
            db.add(doc)
            db.commit()
            db.refresh(doc)
        except Exception as exc:
            db.rollback()
            provider.delete_file(storage_uri)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "DOCUMENT_STORAGE_ERROR",
                    "message": f"Failed to persist document metadata: {str(exc)}",
                    "details": {},
                },
            )

        AuditLogger.log(
            db,
            action="DOCUMENT_UPLOADED",
            entity_type="DOCUMENT",
            entity_id=doc.id,
            actor_id=principal.user_id,
            actor_role=principal.role.value,
            payload={
                "tender_id": doc.tender_id,
                "document_type": str(doc.document_type),
                "filename": doc.filename,
                "sha256": doc.sha256,
                "size_bytes": doc.size_bytes,
                "storage_uri": doc.storage_uri,
            },
        )
        db.refresh(doc)
        return doc

    @staticmethod
    async def upload_bidder_document(
        db: Session,
        bidder_id: str,
        file: UploadFile,
        document_type: DocumentType,
        principal: AuthenticatedPrincipal,
    ) -> Document:
        bidder = db.query(Bidder).filter(Bidder.id == bidder_id).first()
        if not bidder:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Bidder with ID {bidder_id} not found.",
            )

        max_bytes = settings.ARGUS_MAX_UPLOAD_MB * 1024 * 1024
        contents = bytearray()
        while chunk := await file.read(65536):
            contents.extend(chunk)
            if len(contents) > max_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail={
                        "code": "DOCUMENT_SIZE_EXCEEDED",
                        "message": f"Document file size exceeds maximum limit of {settings.ARGUS_MAX_UPLOAD_MB} MB.",
                        "details": {"max_size_mb": settings.ARGUS_MAX_UPLOAD_MB},
                    },
                )
        file_bytes = bytes(contents)

        val_meta = DocumentValidationService.validate_file(
            filename=file.filename or "unnamed_document",
            content=file_bytes,
        )
        sanitized_filename = val_meta.sanitized_filename
        sha256_hash = val_meta.sha256_hex

        existing_doc = (
            db.query(Document)
            .filter(Document.bidder_id == bidder_id, Document.sha256 == sha256_hash)
            .first()
        )
        if existing_doc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "DOCUMENT_DUPLICATE",
                    "message": f"A document with identical content (SHA-256: {sha256_hash}) already exists for this bidder.",
                    "details": {"existing_document_id": existing_doc.id, "sha256": sha256_hash},
                },
            )

        provider = get_storage_provider()
        target_key = f"bidders/{bidder_id}/{sanitized_filename}"
        storage_uri = provider.store_file(file_bytes, target_key=target_key)

        doc = Document(
            tender_id=None,
            bidder_id=bidder_id,
            document_type=document_type,
            filename=sanitized_filename,
            storage_uri=storage_uri,
            content_type=val_meta.content_type,
            size_bytes=val_meta.size_bytes,
            sha256=sha256_hash,
            metadata_json={"original_filename": file.filename},
        )

        try:
            db.add(doc)
            db.commit()
            db.refresh(doc)
        except Exception as exc:
            db.rollback()
            provider.delete_file(storage_uri)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "DOCUMENT_STORAGE_ERROR",
                    "message": f"Failed to persist document metadata: {str(exc)}",
                    "details": {},
                },
            )

        AuditLogger.log(
            db,
            action="DOCUMENT_UPLOADED",
            entity_type="DOCUMENT",
            entity_id=doc.id,
            actor_id=principal.user_id,
            actor_role=principal.role.value,
            payload={
                "bidder_id": doc.bidder_id,
                "document_type": str(doc.document_type),
                "filename": doc.filename,
                "sha256": doc.sha256,
                "size_bytes": doc.size_bytes,
                "storage_uri": doc.storage_uri,
            },
        )
        db.refresh(doc)
        return doc

    @staticmethod
    def get_document(db: Session, document_id: str) -> Document:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document with ID {document_id} not found.",
            )
        return doc

    @staticmethod
    def get_document_content(db: Session, document_id: str) -> tuple[bytes, str, str]:
        doc = DocumentService.get_document(db, document_id)
        provider = get_storage_provider()
        try:
            file_bytes = provider.read_file(doc.storage_uri)
        except FileNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document file content for ID {document_id} not found on storage.",
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to read document file content: {str(exc)}",
            )
        return file_bytes, doc.filename, doc.content_type or "application/octet-stream"

    @staticmethod
    def delete_document(
        db: Session, document_id: str, principal: AuthenticatedPrincipal
    ) -> str:
        doc = DocumentService.get_document(db, document_id)
        provider = get_storage_provider()

        # Attempt to delete file from storage provider
        try:
            provider.delete_file(doc.storage_uri)
        except Exception:
            # Continue DB cleanup even if file storage delete raises non-critical error
            pass

        tender_id = doc.tender_id
        bidder_id = doc.bidder_id
        filename = doc.filename

        db.delete(doc)
        db.commit()

        AuditLogger.log(
            db,
            action="DOCUMENT_DELETED",
            entity_type="DOCUMENT",
            entity_id=document_id,
            actor_id=principal.user_id,
            actor_role=principal.role.value,
            payload={
                "tender_id": tender_id,
                "bidder_id": bidder_id,
                "filename": filename,
            },
        )
        return document_id
