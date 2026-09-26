/**
 * Re-export and alias domain models for UI components.
 * Strict TypeScript: zero 'any'.
 */
export * from '@/types/api';

import type {
  TenderRequirementRead,
  TenderRequirementCreate,
  IntegrationsHealthResponse,
  JobStage,
  JobStatus,
  EvidenceRead,
} from '@/types/api';

export type RequirementRead = TenderRequirementRead;
export type RequirementCreate = TenderRequirementCreate;
export type HealthRead = { status: string };
export type IntegrationHealthRead = IntegrationsHealthResponse;

export interface RawAuditEvent {
  id?: string;
  entity_type?: string;
  entity_id?: string;
  action?: string;
  actor_id?: string;
  actor_role?: string;
  actor_user_id?: string;
  actor_name?: string;
  actor_email?: string;
  payload_json?: Record<string, unknown>;
  payload?: Record<string, unknown>;
  job_id?: string;
  stage?: JobStage | string;
  status?: JobStatus | string;
  progress?: number;
  message?: string;
  timestamp?: string;
  created_at?: string;
  tender_id?: string | null;
  bidder_id?: string | null;
  run_id?: string | null;
  requirement_id?: string | null;
  clause_reference?: string | null;
  target_url?: string | null;
  mode?: 'AUTHENTIC' | 'DEMO';
  source?: 'BACKEND / DATABASE' | 'DEMO_STORE / SYNTHETIC';
  event_hash?: string | null;
  blockchain_status?: string | null;
  blockchain_network?: string | null;
  blockchain_tx_hash?: string | null;
  blockchain_block_number?: number | null;
  anchored_at?: string | null;
  blockchain_error?: string | null;
  audit_hash_version?: string | null;
}

export type AuditEvent = AuditEventRead;

export type AuditEventCategory =
  | 'PIPELINE'
  | 'PROVIDER_HEALTH'
  | 'AUTH'
  | 'DOCUMENT'
  | 'TENDER'
  | 'BIDDER'
  | 'COMPLIANCE'
  | 'HUMAN_DECISION'
  | 'SYSTEM'
  | 'OTHER';

export interface AuditEventRead {
  id?: string;
  job_id?: string | null;
  stage?: JobStage | string | null;
  status?: JobStatus | string | null;
  progress?: number | null;
  message?: string | null;
  timestamp?: string | null;
  event_category?: AuditEventCategory;
  pipeline_stage?: string | null;
  action?: string | null;
  entity_type?: string | null;
  entity_id?: string | null;
  actor?: string | null;
  actor_user_id?: string | null;
  actor_name?: string | null;
  actor_email?: string | null;
  actor_role?: string | null;
  mode?: 'AUTHENTIC' | 'DEMO';
  source?: 'BACKEND / DATABASE' | 'DEMO_STORE / SYNTHETIC';
  target_url?: string | null;
  tender_id?: string | null;
  bidder_id?: string | null;
  run_id?: string | null;
  requirement_id?: string | null;
  clause_reference?: string | null;
  payload_json?: Record<string, unknown> | null;
  event_hash?: string | null;
  blockchain_status?: string | null;
  blockchain_network?: string | null;
  blockchain_tx_hash?: string | null;
  blockchain_block_number?: number | null;
  anchored_at?: string | null;
  blockchain_error?: string | null;
  audit_hash_version?: string | null;
}

export type BlockchainAnchorStatus = 'NOT_ANCHORED' | 'PENDING' | 'SUBMITTED' | 'CONFIRMED' | 'FAILED';

export interface AuditEventBlockchainRead {
  event_id: string;
  event_hash?: string | null;
  blockchain_status: string;
  network?: string | null;
  chain_id?: number | null;
  transaction_hash?: string | null;
  block_number?: number | null;
  anchored_at?: string | null;
  explorer_url?: string | null;
  audit_hash_version?: string | null;
  blockchain_error?: string | null;
}

export interface AuditEventIntegrityVerifyResponse {
  event_id: string;
  integrity: 'VERIFIED' | 'TAMPERED' | 'NOT_ANCHORED' | 'VERIFICATION_ERROR' | 'NOT_FOUND';
  computed_hash?: string | null;
  onchain_hash?: string | null;
  transaction_hash?: string | null;
  block_number?: number | null;
  anchored_at?: string | null;
  verified_at: string;
  details: string;
}

export interface RAGExplainRequest {
  query: string;
  tender_id?: string | null;
  clause?: string | null;
  field?: string | null;
  top_k?: number;
}

export interface RAGExplainResponse {
  query: string;
  explanation: string;
  direct_answer?: string | null;
  related_context?: string | null;
  result_class?: 'DIRECT_EVIDENCE' | 'RELATED_CONTEXT' | 'INSUFFICIENT_RETRIEVAL_EVIDENCE';
  citations: EvidenceRead[];
  related_citations?: EvidenceRead[];
  is_advisory: boolean;
  advisory_disclaimer: string;
  retrieved_at: string;
  error_code?: string | null;
  error_message?: string | null;
}

export type FindingSeverity = 'HIGH' | 'MEDIUM' | 'LOW' | 'INFO';

export type FindingCategory =
  | 'CROSS_DOCUMENT_CONFLICT'
  | 'MISSING_EVIDENCE'
  | 'TENDER_BIDDER_MISMATCH'
  | 'STATUTORY_MISMATCH'
  | 'ANOMALY_SIGNAL'
  | 'RAG_CONTEXT'
  | 'DATE_CONFLICT'
  | 'VALUE_CONFLICT'
  | 'DOCUMENT_INCONSISTENCY'
  | 'UNRESOLVED_QUESTION';

export interface DeepAuditFinding {
  finding_id: string;
  category: FindingCategory | string;
  severity: FindingSeverity | string;
  title: string;
  description: string;
  affected_field?: string;
  affected_fields?: string[];
  tender_requirement?: string;
  bidder_value?: string | null;
  evidence?: string | null;
  evidence_provenance?: Array<{
    document_name: string;
    page?: number | null;
    field?: string;
    raw_value: string;
  }>;
  source_document?: string | null;
  page?: number | null;
  rule_or_detection_method?: string;
  detection_method?: string;
  recommended_action?: string;
  status?: string;
}

export interface CrossDocumentConflictItem {
  conflict_id?: string;
  field?: string;
  field_name?: string;
  tender_requirement?: string;
  documents_involved?: Array<{
    document_name: string;
    document_type: string;
    value: string;
    page?: number | null;
  }>;
  document_a_id?: string;
  document_a_name?: string;
  document_a_page?: number | null;
  document_a_value?: string;
  document_b_id?: string;
  document_b_name?: string;
  document_b_page?: number | null;
  document_b_value?: string;
  difference_description?: string;
  severity?: FindingSeverity | string;
  status?: string; // 'CONFLICT_DETECTED'
  compliance_engine_choice?: string | null;
  officer_review_reason?: string;
}

export interface MissingEvidenceItem {
  item_id: string;
  requirement_title: string;
  requirement_description: string;
  status: 'MISSING' | 'AMBIGUOUS' | 'EXPIRED' | 'WEAK' | 'INCOMPLETE' | string;
  bidder_evidence_status: string;
  recommended_action: string;
}

export interface StatutoryInvestigationItem {
  identifier_type: 'GSTIN' | 'PAN' | 'CIN' | 'UDYAM' | 'EPFO' | 'ESIC' | 'BLACKLIST' | string;
  identifier_value: string;
  provider_mode: 'LIVE' | 'CONFIGURED_UNVERIFIED' | 'DEMO_SYNTHETIC' | string;
  verification_result: 'VERIFIED' | 'MISMATCH' | 'UNVERIFIED' | 'CLEARED' | 'FLAGGED' | string;
  document_derived_value?: string | null;
  external_derived_value?: string | null;
  conflict_status: 'NO_CONFLICT' | 'CONFLICT_DETECTED' | 'UNVERIFIED' | string;
  details: string;
}

export interface RiskAnomalyItem {
  signal_id: string;
  rule_name: string;
  engine_label: string; // 'DETERMINISTIC ANOMALY & RISK RULE ENGINE'
  input_values: string[];
  why_triggered: string;
  severity: FindingSeverity;
  supporting_evidence: string;
}

export interface RAGInvestigationItem {
  query: string;
  direct_tender_evidence?: string | null;
  related_policy_context?: string | null;
  citation_document?: string | null;
  citation_page?: number | null;
  advisory_result: string;
  is_advisory: boolean;
}

export interface UnresolvedQuestionItem {
  question_id: string;
  question: string;
  background: string;
  reason_cannot_auto_resolve: string;
  officer_prompt: string;
}

export interface RecommendedActionItem {
  action_id: string;
  action_type: 'MANUAL_VERIFY' | 'REQUEST_CLARIFICATION' | 'REQUEST_DOCUMENT' | 'REVIEW_CONFLICT' | 'CONFIRM_EXEMPTION' | 'INSPECT_PAGE' | string;
  title: string;
  description: string;
  target_document?: string | null;
  target_page?: number | null;
  is_recommendation_only: boolean;
}

export interface EvidenceChainItem {
  chain_id: string;
  tender_requirement: {
    id: string;
    clause: string;
    text: string;
  };
  bidder_evidence: {
    document_id: string;
    document_name: string;
    page?: number | null;
    excerpt: string;
  };
  extracted_fact: {
    canonical_field: string;
    extracted_value: string;
    confidence?: number | null;
  };
  rule_investigation: {
    detection_method: string;
    engine: string;
    evaluation: string;
  };
  deep_audit_finding: {
    finding_id: string;
    title: string;
    severity: string;
  };
}

export interface WorkflowStageTraceItem {
  stage_key: string;
  label: string;
  status: 'COMPLETED' | 'RUNNING' | 'PENDING' | 'INTERRUPTED' | string;
  short_description: string;
  findings_produced: number;
  evidence_used: number;
  duration_ms?: number | null;
}

export interface DeepAuditConflict {
  clause_reference: string;
  conflict_type: string;
  description: string;
  severity: 'CRITICAL' | 'WARNING' | 'ADVISORY' | string;
}

export interface DeepAuditPrecedent {
  clause_reference: string;
  precedent_id: string;
  source: string;
  similarity_score?: number;
  ruling_summary: string;
}

export interface DeepAuditSynthesis {
  run_id?: string;
  started_at?: string;
  completed_at?: string | null;
  status?: string;
  tender_id?: string;
  bidder_id?: string;
  tender_title?: string | null;
  bidder_name?: string | null;
  is_advisory: boolean;
  advisory_disclaimer?: string;
  summary: string;
  summary_text?: string;

  // Counts & metrics
  total_findings_count?: number;
  high_priority_count?: number;
  review_required_count?: number;
  informational_count?: number;
  unresolved_questions_count?: number;
  conflicts_count?: number;
  missing_evidence_count?: number;

  // Structured investigation collections
  workflow_trace?: WorkflowStageTraceItem[];
  findings?: DeepAuditFinding[];
  cross_document_conflicts?: CrossDocumentConflictItem[];
  missing_evidence?: MissingEvidenceItem[];
  statutory_investigations?: StatutoryInvestigationItem[];
  risk_anomalies?: RiskAnomalyItem[];
  rag_investigations?: RAGInvestigationItem[];
  unresolved_questions?: UnresolvedQuestionItem[];
  recommended_actions?: RecommendedActionItem[];
  evidence_chains?: EvidenceChainItem[];

  // Legacy backward-compatibility fields
  conflicts_detected?: DeepAuditConflict[];
  policy_precedents?: DeepAuditPrecedent[];
  evidence_synthesis?: string;
  recommended_human_inquiries?: string[];
  disclaimer?: string;
  langgraph_trace?: string[];
  langgraph_interrupted?: boolean;
  langgraph_reasons?: string[];
}

export interface DeepAuditStatusResponse {
  status: 'NOT_STARTED' | 'QUEUED' | 'RUNNING' | 'COMPLETED' | 'FAILED' | string;
  stage?: string | null;
  progress?: number;
  job_id?: string | null;
  completed_at?: string | null;
  synthesis?: DeepAuditSynthesis | null;
}


