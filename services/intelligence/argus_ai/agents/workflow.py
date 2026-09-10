"""LangGraph coordination only. Compliance remains an injected backend tool."""
from __future__ import annotations

from typing import Any, Callable, TypedDict


class ArgusWorkflowState(TypedDict, total=False):
    tender: dict[str, Any]
    document: dict[str, Any]
    rag_query: dict[str, Any]
    requirements: list[dict[str, Any]]
    facts: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    risks: list[dict[str, Any]]
    compliance_evaluation: dict[str, Any]
    review_reasons: list[str]
    human_action: dict[str, Any]
    report: dict[str, Any]


def build_workflow(*, extract_tender: Callable[[dict[str, Any]], list[dict[str, Any]]],
                   extract_document: Callable[[dict[str, Any]], list[dict[str, Any]]],
                   retrieve: Callable[[dict[str, Any]], list[dict[str, Any]]],
                   detect_risk: Callable[[dict[str, Any]], list[dict[str, Any]]],
                   compliance_tool: Callable[[dict[str, Any]], dict[str, Any]],
                   report_tool: Callable[[dict[str, Any]], dict[str, Any]],
                   checkpointer=None):
    """Build a graph using explicit, least-privilege callbacks.

    `compliance_tool` must call the backend deterministic engine. It is never an
    LLM node and no graph node maps its output to a human qualification decision.
    """
    try:
        from langgraph.graph import END, START, StateGraph
        from langgraph.types import interrupt
    except ImportError as exc:
        raise RuntimeError("LangGraph orchestration requires the optional langgraph dependency") from exc

    def tender_node(state: ArgusWorkflowState): return {"requirements": extract_tender(state.get("tender", {}))}
    def document_node(state: ArgusWorkflowState): return {"facts": extract_document(state.get("document", {}))}
    def knowledge_node(state: ArgusWorkflowState): return {"evidence": retrieve(state.get("rag_query", {}))}
    def risk_node(state: ArgusWorkflowState): return {"risks": detect_risk(state)}
    def compliance_node(state: ArgusWorkflowState): return {"compliance_evaluation": compliance_tool(state)}
    def human_review_node(state: ArgusWorkflowState):
        reasons = state.get("review_reasons", []) or ["Human review requested by workflow policy."]
        return {"human_action": interrupt({"type": "HUMAN_REVIEW_REQUIRED", "reasons": reasons, "risks": state.get("risks", []), "evidence": state.get("evidence", [])})}
    def report_node(state: ArgusWorkflowState): return {"report": report_tool(state)}
    def route_review(state: ArgusWorkflowState):
        evaluation = state.get("compliance_evaluation", {})
        reasons = list(state.get("review_reasons", []))
        if any(risk.get("severity") in {"HIGH", "CRITICAL"} for risk in state.get("risks", [])): reasons.append("High-severity risk signal")
        if evaluation.get("status") in {"REVIEW_REQUIRED", "UNKNOWN"}: reasons.append("Compliance engine requires review")
        if reasons:
            state["review_reasons"] = reasons
            return "human_review"
        return "report"

    graph = StateGraph(ArgusWorkflowState)
    graph.add_node("tender_intelligence", tender_node); graph.add_node("document_intelligence", document_node)
    graph.add_node("knowledge", knowledge_node); graph.add_node("risk", risk_node)
    graph.add_node("compliance", compliance_node); graph.add_node("human_review", human_review_node); graph.add_node("report", report_node)
    graph.add_edge(START, "tender_intelligence"); graph.add_edge("tender_intelligence", "document_intelligence")
    graph.add_edge("document_intelligence", "knowledge"); graph.add_edge("knowledge", "risk"); graph.add_edge("risk", "compliance")
    graph.add_conditional_edges("compliance", route_review, {"human_review": "human_review", "report": "report"})
    graph.add_edge("human_review", "report"); graph.add_edge("report", END)
    if checkpointer is not None:
        return graph.compile(checkpointer=checkpointer)
    return graph.compile()


def memory_checkpointer():
    """In-process checkpointer for dev/demo; not for production."""
    from langgraph.checkpoint.memory import MemorySaver
    return MemorySaver()


def build_argus_workflow(*, gateway=None, rag=None, compliance_tool=None, report_tool=None, checkpointer=None):
    """Build a workflow wired to real ARGUS intelligence functions.
    
    compliance_tool and report_tool must be injected by the API layer.
    If not provided, they return REVIEW_REQUIRED / empty report.
    """
    from argus_ai.extraction.service import extract_tender as _raw_extract_tender
    from argus_ai.extraction.service import extract_document as _raw_extract_document
    from argus_ai.model_gateway.gateway import ModelGateway
    from argus_ai.risk.context import detect_fact_risks
    from argus_ai.rag.service import InMemoryRAG
    from argus_ai.storage import resolved_document

    gw = gateway or ModelGateway()
    rag_service = rag or InMemoryRAG()
    # Document URIs reaching these nodes come from the /evaluate-bid request body.
    # They must go through resolved_document() — the same size limit, scheme
    # restriction and storage-root allowlist every other endpoint uses. Passing
    # them straight to the parser bypassed all three (audit finding C-4).

    def _extract_tender(tender_dict):
        uri = tender_dict.get("document_uri")
        if not uri: return []
        with resolved_document(uri) as path:
            # extract_tender returns list[TenderRequirementDraft]
            return [req.model_dump(mode="json") for req in _raw_extract_tender(path, gw)]

    def _extract_document(document_dict):
        uri = document_dict.get("document_uri")
        if not uri: return []
        doc_id = document_dict.get("document_id", "unknown")
        bidder_id = document_dict.get("bidder_id", "unknown")
        with resolved_document(uri) as path:
            # extract_document returns list[ExtractedFactDraft]
            return [fact.model_dump(mode="json") for fact in _raw_extract_document(path, document_id=doc_id, bidder_id=bidder_id, gateway=gw)]

    def _retrieve(query_dict):
        query = query_dict.get("query")
        if not query: return []
        return [chunk.model_dump(mode="json") for chunk in rag_service.retrieve(query, top_k=5)]

    def _detect_risk(state):
        facts = state.get("facts", [])
        if not facts: return []
        # detect_fact_risks expects ExtractedFactDraft objects — reconstruct from dicts
        from argus_ai.contracts import ExtractedFactDraft
        drafts = []
        for fact_dict in facts:
            try:
                drafts.append(ExtractedFactDraft.model_validate(fact_dict))
            except Exception:
                continue
        if not drafts: return []
        return [risk.model_dump(mode="json") for risk in detect_fact_risks(drafts)]

    def _compliance_tool(state):
        if compliance_tool:
            return compliance_tool(state)
        return {"status": "REVIEW_REQUIRED", "source": "no_backend_compliance_engine"}

    def _report_tool(state):
        if report_tool:
            return report_tool(state)
        return {
            "requirement_count": len(state.get("requirements", [])),
            "fact_count": len(state.get("facts", [])),
            "evidence_count": len(state.get("evidence", [])),
            "risk_count": len(state.get("risks", [])),
            "compliance_status": state.get("compliance_evaluation", {}).get("status", "UNKNOWN"),
        }

    return build_workflow(
        extract_tender=_extract_tender,
        extract_document=_extract_document,
        retrieve=_retrieve,
        detect_risk=_detect_risk,
        compliance_tool=_compliance_tool,
        report_tool=_report_tool,
        checkpointer=checkpointer,
    )

