from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session
from app.auth.dependencies import get_current_principal, require_roles
from app.db.session import get_db
from app.schemas.canonical import AuthenticatedPrincipal, DocumentRead, UserRole
from app.services.document_service import DocumentService

router = APIRouter(prefix="/documents", tags=["Documents"])


@router.get("/{document_id}", response_model=DocumentRead)
def get_document(
    document_id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """Retrieve metadata for a specific document by ID."""
    doc = DocumentService.get_document(db, document_id)
    return doc


@router.get("/{document_id}/content")
def get_document_content(
    document_id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_db),
):
    """Download or view stored file content for a document."""
    file_bytes, filename, media_type = DocumentService.get_document_content(db, document_id)
    return Response(
        content=file_bytes,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )



