from argus_ai.evaluation.metrics import citation_precision, precision_recall, recall_at_k
from argus_ai.agents.workflow import build_workflow

def test_retrieval_and_citation_metrics():
    assert recall_at_k({"current-policy"}, ["old-policy", "current-policy"], 2) == 1
    assert citation_precision(["current-policy", "unknown"], {"current-policy"}) == .5
    assert precision_recall({"a"}, {"a", "b"}) == (1, .5)

def test_workflow_delegates_compliance_and_produces_report():
    workflow = build_workflow(
        extract_tender=lambda _: [{"field": "tax.gstin"}],
        extract_document=lambda _: [{"field": "tax.gstin", "value": "x"}],
        retrieve=lambda _: [{"id": "E1"}],
        detect_risk=lambda _: [],
        compliance_tool=lambda _: {"status": "NOT_APPLICABLE", "source": "backend"},
        report_tool=lambda state: {"evidence_count": len(state["evidence"]), "compliance_source": state["compliance_evaluation"]["source"]},
    )
    result = workflow.invoke({"tender": {}, "document": {}, "rag_query": {}})
    assert result["report"] == {"evidence_count": 1, "compliance_source": "backend"}
