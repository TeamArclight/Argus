/**
 * Centralized Typed API Client for ARGUS Backend.
 * Strictly adheres to FastAPI OpenAPI contract with zero 'any'.
 */
import type {
  AuthenticatedPrincipal,
  BidderCreate,
  BidderRead,
  ComplianceMatrixRead,
  ComplianceMatrixRow,
  ComplianceRunDetailRead,
  ComplianceRunSummaryRead,
  DocumentRead,
  DocumentType,
  EvidenceRead,
  HumanDecisionCreate,
  HumanDecisionRead,
  IntegrationsHealthResponse,
  JobRead,
  JobStage,
  JobStatus,
  ProviderHealthRead,
  RAGQueryRequest,
  RAGQueryResponse,
  ReportRead,
  TenderCreate,
  TenderRead,
  TenderRequirementCreate,
  TenderRequirementRead,
  VerificationResultRead,
} from '@/types/api';
import type { AuditEventRead, RawAuditEvent } from '@/services/types';
import { demoStore } from './demo-store';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000';

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly raw: unknown;
  readonly isNetworkError: boolean;

  constructor(status: number, detail: string, raw?: unknown, isNetworkError: boolean = false) {
    super(`API Error ${status}: ${detail}`);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.raw = raw;
    this.isNetworkError = isNetworkError;
  }
}

let activeToken: string | null = null;

export function setAuthToken(token: string | null): void {
  activeToken = token;
  if (typeof window !== 'undefined') {
    if (token) {
      sessionStorage.setItem('argus_auth_token', token);
      // Clean up legacy localStorage if present
      localStorage.removeItem('argus_auth_token');
    } else {
      sessionStorage.removeItem('argus_auth_token');
      localStorage.removeItem('argus_auth_token');
    }
  }
}

export function getAuthToken(): string | null {
  if (activeToken) return activeToken;
  if (typeof window !== 'undefined') {
    return sessionStorage.getItem('argus_auth_token') || localStorage.getItem('argus_auth_token');
  }
  return null;
}

export function getApiBaseUrl(): string {
  return API_BASE_URL;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getAuthToken();
  const headers = new Headers(options.headers || {});
  
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  if (!headers.has('Content-Type') && !(options.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json');
  }

  const url = `${API_BASE_URL}${path.startsWith('/') ? path : `/${path}`}`;
  
  let response: Response;
  try {
    response = await fetch(url, { ...options, headers });
  } catch (err: unknown) {
    const errorMsg = err instanceof Error ? err.message : String(err);
    throw new ApiError(
      0,
      `Backend Unavailable: Unable to connect to ${API_BASE_URL} (${errorMsg}). Please verify that the FastAPI backend server is running.`,
      err,
      true
    );
  }

  if (!response.ok) {
    let detail = response.statusText;
    let raw: unknown = null;
    try {
      raw = await response.json();
      if (raw && typeof raw === 'object') {
        const payload = raw as { detail?: unknown; message?: unknown; error?: { message?: string } };
        if (payload.error && typeof payload.error.message === 'string') {
          detail = payload.error.message;
        } else if (typeof payload.detail === 'string') {
          detail = payload.detail;
        } else if (Array.isArray(payload.detail)) {
          detail = payload.detail.map((e: { msg?: string }) => e.msg || JSON.stringify(e)).join('; ');
        } else if (typeof payload.message === 'string') {
          detail = payload.message;
        }
      }
    } catch {
      // Non-JSON response
    }

    if (response.status === 401 && (!detail || detail === 'Unauthorized')) {
      detail = 'Session Required / Token Expired: Valid Bearer token is required to access this endpoint.';
    } else if (response.status === 403 && (!detail || detail === 'Forbidden')) {
      detail = 'Access Denied / Role Not Authorized: Your authenticated role lacks required permissions.';
    }

    throw new ApiError(response.status, detail, raw, false);
  }

  if (response.status === 204) {
    return {} as T;
  }

  return response.json() as Promise<T>;
}

export const apiClient = {
  // Authentication & Session
  async getMe(): Promise<AuthenticatedPrincipal> {
    return request<AuthenticatedPrincipal>('/api/v1/auth/me');
  },

  // Tenders
  async getTenders(): Promise<TenderRead[]> {
    return request<TenderRead[]>('/api/v1/tenders');
  },

  async getTender(tenderId: string): Promise<TenderRead> {
    return request<TenderRead>(`/api/v1/tenders/${encodeURIComponent(tenderId)}`);
  },

  async createTender(data: TenderCreate): Promise<TenderRead> {
    return request<TenderRead>('/api/v1/tenders', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  async processTender(tenderId: string): Promise<JobRead> {
    return request<JobRead>(`/api/v1/tenders/${encodeURIComponent(tenderId)}/process`, {
      method: 'POST',
    });
  },

  async extractRequirements(tenderId: string): Promise<JobRead> {
    return this.processTender(tenderId);
  },

  async getTenderRequirements(tenderId: string): Promise<TenderRequirementRead[]> {
    return request<TenderRequirementRead[]>(`/api/v1/tenders/${encodeURIComponent(tenderId)}/requirements`);
  },

  async getRequirements(tenderId: string): Promise<TenderRequirementRead[]> {
    return this.getTenderRequirements(tenderId);
  },

  async createTenderRequirement(tenderId: string, data: TenderRequirementCreate): Promise<TenderRequirementRead> {
    return request<TenderRequirementRead>(`/api/v1/tenders/${encodeURIComponent(tenderId)}/requirements`, {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  async addRequirement(tenderId: string, data: TenderRequirementCreate): Promise<TenderRequirementRead> {
    return this.createTenderRequirement(tenderId, data);
  },

  async approveTenderRequirement(tenderId: string, requirementId: string): Promise<TenderRequirementRead> {
    return request<TenderRequirementRead>(
      `/api/v1/tenders/${encodeURIComponent(tenderId)}/requirements/${encodeURIComponent(requirementId)}/approve`,
      { method: 'POST' }
    );
  },

  async getTenderDocuments(tenderId: string): Promise<DocumentRead[]> {
    return request<DocumentRead[]>(`/api/v1/tenders/${encodeURIComponent(tenderId)}/documents`);
  },

  async uploadTenderDocument(tenderId: string, file: File, documentType: DocumentType = 'TENDER'): Promise<DocumentRead> {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('document_type', documentType);
    return request<DocumentRead>(`/api/v1/tenders/${encodeURIComponent(tenderId)}/documents`, {
      method: 'POST',
      body: formData,
    });
  },

  async uploadTenderDoc(tenderId: string, file: File, documentType: DocumentType = 'TENDER'): Promise<DocumentRead> {
    return this.uploadTenderDocument(tenderId, file, documentType);
  },

  // Bidders
  async getTenderBidders(tenderId: string): Promise<BidderRead[]> {
    return request<BidderRead[]>(`/api/v1/tenders/${encodeURIComponent(tenderId)}/bidders`);
  },

  async getBidders(tenderId: string): Promise<BidderRead[]> {
    return this.getTenderBidders(tenderId);
  },

  async createBidder(tenderId: string, data: BidderCreate): Promise<BidderRead> {
    return request<BidderRead>(`/api/v1/tenders/${encodeURIComponent(tenderId)}/bidders`, {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  async getBidder(bidderId: string): Promise<BidderRead> {
    return request<BidderRead>(`/api/v1/bidders/${encodeURIComponent(bidderId)}`);
  },

  async getBidderDocuments(bidderId: string): Promise<DocumentRead[]> {
    return request<DocumentRead[]>(`/api/v1/bidders/${encodeURIComponent(bidderId)}/documents`);
  },

  async uploadBidderDocument(bidderId: string, file: File, documentType: DocumentType = 'FINANCIAL_STATEMENT'): Promise<DocumentRead> {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('document_type', documentType);
    return request<DocumentRead>(`/api/v1/bidders/${encodeURIComponent(bidderId)}/documents`, {
      method: 'POST',
      body: formData,
    });
  },

  async uploadBidderDoc(bidderId: string, file: File, documentType: DocumentType = 'FINANCIAL_STATEMENT'): Promise<DocumentRead> {
    return this.uploadBidderDocument(bidderId, file, documentType);
  },

  async verifyBidder(bidderId: string): Promise<JobRead> {
    return request<JobRead>(`/api/v1/bidders/${encodeURIComponent(bidderId)}/verify`, {
      method: 'POST',
    });
  },

  async runVerification(bidderId: string): Promise<JobRead> {
    return this.verifyBidder(bidderId);
  },

  async getVerificationResults(bidderId: string): Promise<VerificationResultRead[]> {
    try {
      const report = await this.getReport(bidderId);
      return report.verification_results ?? [];
    } catch {
      return [];
    }
  },

  async processBidderDocuments(bidderId: string): Promise<JobRead> {
    return request<JobRead>(`/api/v1/bidders/${encodeURIComponent(bidderId)}/process-documents`, {
      method: 'POST',
    });
  },

  async evaluateCompliance(bidderId: string): Promise<JobRead> {
    return this.processBidderDocuments(bidderId);
  },

  async runCompliance(bidderId: string): Promise<JobRead> {
    return this.processBidderDocuments(bidderId);
  },

  async getComplianceRuns(bidderId: string): Promise<ComplianceRunSummaryRead[]> {
    return request<ComplianceRunSummaryRead[]>(`/api/v1/bidders/${encodeURIComponent(bidderId)}/runs`);
  },

  async getComplianceRunDetail(bidderId: string, runId: string): Promise<ComplianceRunDetailRead> {
    return request<ComplianceRunDetailRead>(
      `/api/v1/bidders/${encodeURIComponent(bidderId)}/runs/${encodeURIComponent(runId)}`
    );
  },

  async getComplianceMatrix(bidderId: string): Promise<ComplianceMatrixRow[]> {
    const res = await request<ComplianceMatrixRead>(`/api/v1/bidders/${encodeURIComponent(bidderId)}/matrix`);
    return res.rows || [];
  },

  async submitHumanDecision(bidderId: string, data: HumanDecisionCreate): Promise<HumanDecisionRead> {
    return request<HumanDecisionRead>(`/api/v1/bidders/${encodeURIComponent(bidderId)}/decision`, {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  async getReport(bidderId: string): Promise<ReportRead> {
    return request<ReportRead>(`/api/v1/bidders/${encodeURIComponent(bidderId)}/report`);
  },

  // Documents
  async getDocument(documentId: string): Promise<DocumentRead> {
    return request<DocumentRead>(`/api/v1/documents/${encodeURIComponent(documentId)}`);
  },

  getDocumentContentUrl(documentId: string): string {
    return `${API_BASE_URL}/api/v1/documents/${encodeURIComponent(documentId)}/content`;
  },

  // Evidence
  async getEvaluationEvidence(evaluationId: string): Promise<EvidenceRead> {
    return request<EvidenceRead>(`/api/v1/evaluations/${encodeURIComponent(evaluationId)}/evidence`);
  },

  // Jobs & SSE
  async getJob(jobId: string): Promise<JobRead> {
    const demoJob = demoStore.getJob(jobId);
    if (demoJob) return demoJob;
    return request<JobRead>(`/api/v1/jobs/${encodeURIComponent(jobId)}`);
  },

  getJobEventSourceUrl(jobId: string, afterSeq?: number): string {
    const query = afterSeq !== undefined ? `?after_seq=${afterSeq}` : '';
    return `${API_BASE_URL}/api/v1/jobs/${encodeURIComponent(jobId)}/events${query}`;
  },

  // RAG & Audit
  async queryRAG(data: RAGQueryRequest): Promise<RAGQueryResponse> {
    return request<RAGQueryResponse>('/api/v1/rag/query', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  async getAuditLogs(params: { limit?: number; offset?: number } = {}): Promise<AuditEventRead[]> {
    const query = params.limit ? `?limit=${params.limit}` : '';
    const rawEvents = await request<RawAuditEvent[]>(`/api/v1/audit/events${query}`);
    if (Array.isArray(rawEvents)) {
      return rawEvents.map(normalizeAuditEvent);
    }
    return [];
  },

  async getAuditEvents(params: { limit?: number; offset?: number } = {}): Promise<AuditEventRead[]> {
    return this.getAuditLogs(params);
  },

  // System Health
  async getProviders(): Promise<ProviderHealthRead[]> {
    return request<ProviderHealthRead[]>('/api/v1/providers');
  },

  async getHealth(): Promise<{ status: string }> {
    return request<{ status: string }>('/health');
  },

  async getSystemHealth(): Promise<{ status: string }> {
    return this.getHealth();
  },

  async getHealthIntegrations(): Promise<IntegrationsHealthResponse> {
    return request<IntegrationsHealthResponse>('/health/integrations');
  },

  async getIntegrationHealth(): Promise<IntegrationsHealthResponse> {
    return this.getHealthIntegrations();
  },
};

const VALID_JOB_STAGES: JobStage[] = [
  'UPLOAD', 'PARSING', 'OCR', 'EXTRACTION', 'VERIFICATION', 'COMPLIANCE', 'RISK_ANALYSIS', 'REPORTING'
];

const VALID_JOB_STATUSES: JobStatus[] = [
  'QUEUED', 'RUNNING', 'COMPLETED', 'FAILED', 'REVIEW_REQUIRED'
];

/**
 * Normalizes raw backend AuditEvent records into canonical AuditEventRead objects.
 * Single adapter boundary mapping for backend shape -> UI event model.
 */
export function normalizeAuditEvent(raw: RawAuditEvent | Record<string, unknown>): AuditEventRead {
  if (!raw || typeof raw !== 'object') {
    return {
      id: undefined,
      job_id: null,
      stage: null,
      status: null,
      progress: null,
      message: null,
      timestamp: null,
    };
  }

  const rawRecord = raw as RawAuditEvent;
  const payload = (rawRecord.payload_json || rawRecord.payload || {}) as Record<string, unknown>;

  // 1. JOB ID FALLBACK:
  // Do NOT use entity_id as generic fallback. Only map entity_id -> job_id when entity_type === "JOB".
  // Otherwise leave job_id as null unless a real job_id / jobId / run_id exists in payload.
  let jobId: string | null = null;
  if (typeof rawRecord.job_id === 'string' && rawRecord.job_id.trim() !== '') {
    jobId = rawRecord.job_id;
  } else if (typeof payload.job_id === 'string' && payload.job_id.trim() !== '') {
    jobId = payload.job_id;
  } else if (typeof payload.jobId === 'string' && payload.jobId.trim() !== '') {
    jobId = payload.jobId;
  } else if (typeof payload.run_id === 'string' && payload.run_id.trim() !== '') {
    jobId = payload.run_id;
  } else if (rawRecord.entity_type === 'JOB' && typeof rawRecord.entity_id === 'string' && rawRecord.entity_id.trim() !== '') {
    jobId = rawRecord.entity_id;
  }

  // 2. STAGE / STATUS DERIVATION:
  // Map stage/status from payload or action when unambiguous.
  let stageCandidate: string | null = (rawRecord.stage && rawRecord.stage !== '—')
    ? String(rawRecord.stage)
    : (payload.stage || payload.event_type ? String(payload.stage || payload.event_type) : null);

  let statusCandidate: string | null = (rawRecord.status && rawRecord.status !== '—')
    ? String(rawRecord.status)
    : (payload.status || payload.event_status ? String(payload.status || payload.event_status) : null);

  const action: string = rawRecord.action || '';

  if (!stageCandidate && action) {
    if (action.includes('EXTRACTION')) {
      stageCandidate = 'EXTRACTION';
    } else if (action.includes('VERIFICATION')) {
      stageCandidate = 'VERIFICATION';
    } else if (action.includes('COMPLIANCE')) {
      stageCandidate = 'COMPLIANCE';
    } else if (action === 'DOCUMENT_UPLOADED' || action === 'TENDER_CREATED' || action === 'BIDDER_CREATED' || action === 'TENDER_PROCESSING_STARTED') {
      stageCandidate = 'UPLOAD';
    } else if (action === 'HUMAN_DECISION_RECORDED') {
      stageCandidate = 'REPORTING';
    } else if (action.includes('OCR') || action.includes('PARSING')) {
      stageCandidate = 'OCR';
    }
  }

  if (!statusCandidate && action) {
    if (action.includes('FAILED') || action.includes('ERROR') || action.includes('ORPHANED')) {
      statusCandidate = 'FAILED';
    } else if (action.includes('COMPLETED') || action.includes('APPROVED') || action === 'TENDER_CREATED' || action === 'BIDDER_CREATED' || action === 'DOCUMENT_UPLOADED') {
      statusCandidate = 'COMPLETED';
    } else if (action.includes('STARTED') || action.includes('REQUESTED')) {
      statusCandidate = 'RUNNING';
    }
  }

  // Safely validate enum types without forcing invalid "—" strings
  const stage: JobStage | null = (stageCandidate && VALID_JOB_STAGES.includes(stageCandidate as JobStage))
    ? (stageCandidate as JobStage)
    : null;

  let status: JobStatus | null = null;
  if (statusCandidate) {
    if (statusCandidate === 'SUCCESS') {
      status = 'COMPLETED';
    } else if (VALID_JOB_STATUSES.includes(statusCandidate as JobStatus)) {
      status = statusCandidate as JobStatus;
    }
  }

  // 3. MESSAGE NORMALIZATION:
  let message: string | null = (rawRecord.message || payload.message || payload.detail || payload.error_message)
    ? String(rawRecord.message || payload.message || payload.detail || payload.error_message)
    : null;

  if (!message) {
    if (payload.error_code) {
      message = String(payload.error_code);
    } else if (action) {
      switch (action) {
        case 'DOCUMENT_EXTRACTION_FAILED':
        case 'TENDER_EXTRACTION_FAILED':
          message = 'Document extraction failed.';
          break;
        case 'DOCUMENT_EXTRACTION_COMPLETED':
        case 'TENDER_EXTRACTION_COMPLETED':
          message = 'Document extraction completed.';
          break;
        case 'TENDER_PROCESSING_STARTED':
          message = 'Tender processing started.';
          break;
        case 'VERIFICATION_STARTED':
          message = 'Registry verification workflow started.';
          break;
        case 'VERIFICATION_FAILED':
          message = 'Verification workflow failed.';
          break;
        case 'VERIFICATION_RUN_ORPHANED':
          message = 'Stale verification run orphaned.';
          break;
        case 'COMPLIANCE_EVALUATION_COMPLETED':
        case 'COMPLIANCE_RUN_COMPLETED':
          message = payload.overall_status
            ? `Compliance evaluation completed (${String(payload.overall_status)}).`
            : 'Deterministic compliance evaluation completed.';
          break;
        case 'HUMAN_DECISION_RECORDED':
          message = 'Officer human decision recorded.';
          break;
        case 'DOCUMENT_UPLOADED':
          message = 'Document uploaded.';
          break;
        case 'TENDER_CREATED':
          message = 'Tender record created.';
          break;
        case 'BIDDER_CREATED':
          message = 'Bidder record created.';
          break;
        case 'TENDER_REQUIREMENT_CREATED':
          message = 'Tender requirement created.';
          break;
        case 'TENDER_REQUIREMENT_APPROVED':
          message = 'Tender requirement approved.';
          break;
        case 'PROVIDER_HEALTH_CHECKED':
          message = 'Provider health check passed.';
          break;
        default:
          message = action;
          break;
      }
    }
  }

  // 4. PROGRESS NORMALIZATION (Constraint 4):
  // Normalize only when the value is a real finite number.
  let progress: number | null = null;
  if (typeof rawRecord.progress === 'number' && Number.isFinite(rawRecord.progress)) {
    progress = rawRecord.progress;
  } else if (typeof payload.progress === 'number' && Number.isFinite(payload.progress)) {
    progress = payload.progress;
  } else if (typeof payload.progress_pct === 'number' && Number.isFinite(payload.progress_pct)) {
    progress = payload.progress_pct;
  }

  // 5. TIMESTAMP HANDLING (Constraint 1 - No fabricated timestamps):
  // An unknown audit timestamp must remain null.
  let timestampStr: string | null = null;
  const rawTs = rawRecord.timestamp || rawRecord.created_at;
  if (typeof rawTs === 'string' && rawTs.trim() !== '') {
    timestampStr = rawTs.trim().replace(/^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})/, '$1T$2');
  }

  // 6. EVENT ID HANDLING (Constraint 2 - No random generated IDs):
  const id = typeof rawRecord.id === 'string' && rawRecord.id.trim() !== '' ? rawRecord.id : undefined;

  return {
    id,
    job_id: jobId,
    stage,
    status,
    progress,
    message,
    timestamp: timestampStr,
  };
}

export const api = apiClient;

