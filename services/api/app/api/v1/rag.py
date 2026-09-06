from fastapi import APIRouter
from app.schemas.canonical import RAGQueryRequest, RAGQueryResponse
from app.services.rag_adapter import RAGServiceAdapter

router = APIRouter(prefix="/rag", tags=["RAG Evidence Retrieval"])
rag_adapter = RAGServiceAdapter()


@router.post("/query", response_model=RAGQueryResponse)
async def query_rag_evidence(payload: RAGQueryRequest):
    response = await rag_adapter.retrieve(payload)
    return response
