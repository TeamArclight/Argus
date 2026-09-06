from fastapi import APIRouter, Depends
from app.auth.dependencies import get_current_principal
from app.schemas.canonical import AuthenticatedPrincipal, RAGQueryRequest, RAGQueryResponse
from app.services.rag_adapter import RAGServiceAdapter

router = APIRouter(prefix="/rag", tags=["RAG Evidence Retrieval"])
rag_adapter = RAGServiceAdapter()


@router.post("/query", response_model=RAGQueryResponse)
async def query_rag_evidence(
    payload: RAGQueryRequest,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
    response = await rag_adapter.retrieve(payload)
    return response

