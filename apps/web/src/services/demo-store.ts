/**
 * Centralized, Persistent Synthetic Demo Store for ARGUS.
 * Handles localStorage persistence, per-tender data isolation, synthetic pipeline execution,
 * and deterministic demo data reset without touching production backend architecture.
 */
import type {
  TenderRead,
  TenderCreate,
  TenderRequirementRead,
  BidderRead,
  BidderCreate,
  ComplianceMatrixRead,
  ReportRead,
  VerificationResultRead,
  HumanDecisionStatus,
  JobRead,
  JobEventRead,
  JobStage,
  JobStatus,
} from '@/types/api';
import type { AuditEventRead } from '@/services/types';

export interface DemoAttachedFile {
  filename: string;
  size_bytes: number;
  content_type: string;
  uploaded_at: string;
}

export interface DemoTender extends TenderRead {
  attached_file?: DemoAttachedFile | null;
}

export interface DemoState {
  tenders: DemoTender[];
  requirements: Record<string, TenderRequirementRead[]>; // tender_id -> requirements
  bidders: Record<string, BidderRead[]>; // tender_id -> bidders
  complianceMatrices: Record<string, ComplianceMatrixRead>; // bidder_id -> matrix
  verifications: Record<string, VerificationResultRead[]>; // bidder_id -> verifications
  humanDecisions: Record<string, HumanDecisionStatus>; // bidder_id -> decision
  auditEvents: AuditEventRead[];
  jobs: Record<string, JobRead>; // job_id -> job
}

const DEMO_STORAGE_KEY = 'argus_demo_store_v1';

// ---------------------------------------------------------------------------
// CANONICAL DEFAULT DEMO SCENARIOS
// ---------------------------------------------------------------------------

const DEFAULT_DEMO_STATE: DemoState = {
  tenders: [
    {
      id: 'tender_gem_2026_01',
      tender_number: 'GEM/2026/B/4521089',
      title: 'Comprehensive Highway Surveillance & IT Infrastructure Modernization',
      category: 'GOODS_AND_SERVICES',
      authority: 'National Highways Authority of India',
      budget: 50000000,
      deadline: '2026-10-15T18:00:00Z',
      status: 'COMPLETED',
      created_at: '2026-08-01T10:00:00Z',
      updated_at: '2026-08-01T10:00:00Z',
      attached_file: {
        filename: 'highway_surveillance_rfp_2026.pdf',
        size_bytes: 1245000,
        content_type: 'application/pdf',
        uploaded_at: '2026-08-01T10:01:00Z',
      },
    },
    {
      id: 'tender_gem_2026_02',
      tender_number: 'GEM/2026/B/4521090',
      title: 'National Digital Identity Verification & Cloud Backup Cluster',
      category: 'IT_INFRASTRUCTURE',
      authority: 'Ministry of Electronics & Information Technology',
      budget: 120000000,
      deadline: '2026-11-01T17:00:00Z',
      status: 'COMPLETED',
      created_at: '2026-08-10T11:30:00Z',
      updated_at: '2026-08-10T11:30:00Z',
      attached_file: {
        filename: 'meity_cloud_cluster_rfp.pdf',
        size_bytes: 2410000,
        content_type: 'application/pdf',
        uploaded_at: '2026-08-10T11:31:00Z',
      },
    },
    {
      id: 'tender_gem_2026_03',
      tender_number: 'GEM/2026/B/4521091',
      title: 'Smart Solar Grid Micro-Inverter Deployment (Phase IV)',
      category: 'RENEWABLE_ENERGY',
      authority: 'Solar Energy Corporation of India',
      budget: 85000000,
      deadline: '2026-12-01T12:00:00Z',
      status: 'REVIEW_REQUIRED',
      created_at: '2026-08-20T09:15:00Z',
      updated_at: '2026-08-20T09:15:00Z',
      attached_file: {
        filename: 'seci_solar_grid_rfp.pdf',
        size_bytes: 3150000,
        content_type: 'application/pdf',
        uploaded_at: '2026-08-20T09:16:00Z',
      },
    },
  ],
  requirements: {
    tender_gem_2026_01: [
      {
        id: 'req_01',
        tender_id: 'tender_gem_2026_01',
        clause: 'Clause 4.1.1',
        requirement_type: 'GST',
        field: 'tax.gstin',
        operator: 'EQ',
        expected_value: 'VALID_ACTIVE',
        unit: null,
        mandatory: true,
        source_page: 4,
        source_text: 'Bidder must possess a valid, active GSTIN registration in India.',
        confidence: 0.98,
        requires_verification: true,
        is_approved: true,
        metadata_json: {},
        created_at: '2026-08-01T10:05:00Z',
      },
      {
        id: 'req_02',
        tender_id: 'tender_gem_2026_01',
        clause: 'Clause 4.2.3',
        requirement_type: 'TURNOVER',
        field: 'financial.average_annual_turnover',
        operator: 'GTE',
        expected_value: 50000000,
        unit: 'INR',
        mandatory: true,
        source_page: 5,
        source_text: 'Minimum average annual turnover shall be INR 5,00,00,000 for the last 3 financial years.',
        confidence: 0.95,
        requires_verification: true,
        is_approved: true,
        metadata_json: {},
        created_at: '2026-08-01T10:05:00Z',
      },
      {
        id: 'req_03',
        tender_id: 'tender_gem_2026_01',
        clause: 'Clause 4.3.1',
        requirement_type: 'EXPERIENCE',
        field: 'experience.years',
        operator: 'GTE',
        expected_value: 3,
        unit: 'YEARS',
        mandatory: true,
        source_page: 7,
        source_text: 'Bidder must have at least 3 years of continuous operating experience in IT infrastructure.',
        confidence: 0.92,
        requires_verification: true,
        is_approved: true,
        metadata_json: {},
        created_at: '2026-08-01T10:05:00Z',
      },
    ],
    tender_gem_2026_02: [
      {
        id: 'req_b01',
        tender_id: 'tender_gem_2026_02',
        clause: 'Clause 3.1.2',
        requirement_type: 'GST',
        field: 'tax.gstin',
        operator: 'EQ',
        expected_value: 'VALID_ACTIVE',
        unit: null,
        mandatory: true,
        source_page: 3,
        source_text: 'Bidder must be registered under GST Act.',
        confidence: 0.99,
        requires_verification: true,
        is_approved: true,
        metadata_json: {},
        created_at: '2026-08-10T11:35:00Z',
      },
      {
        id: 'req_b02',
        tender_id: 'tender_gem_2026_02',
        clause: 'Clause 3.2.1',
        requirement_type: 'TURNOVER',
        field: 'financial.average_annual_turnover',
        operator: 'GTE',
        expected_value: 120000000,
        unit: 'INR',
        mandatory: true,
        source_page: 6,
        source_text: 'Minimum annual turnover must exceed INR 12,00,00,000.',
        confidence: 0.96,
        requires_verification: true,
        is_approved: true,
        metadata_json: {},
        created_at: '2026-08-10T11:35:00Z',
      },
      {
        id: 'req_b03',
        tender_id: 'tender_gem_2026_02',
        clause: 'Clause 3.3.4',
        requirement_type: 'EXPERIENCE',
        field: 'experience.years',
        operator: 'GTE',
        expected_value: 5,
        unit: 'YEARS',
        mandatory: true,
        source_page: 9,
        source_text: 'At least 5 years operating cloud data centers in India.',
        confidence: 0.94,
        requires_verification: true,
        is_approved: true,
        metadata_json: {},
        created_at: '2026-08-10T11:35:00Z',
      },
    ],
    tender_gem_2026_03: [
      {
        id: 'req_c01',
        tender_id: 'tender_gem_2026_03',
        clause: 'Clause 2.1',
        requirement_type: 'GST',
        field: 'tax.gstin',
        operator: 'EQ',
        expected_value: 'VALID_ACTIVE',
        unit: null,
        mandatory: true,
        source_page: 2,
        source_text: 'Valid GSTIN required.',
        confidence: 0.97,
        requires_verification: true,
        is_approved: true,
        metadata_json: {},
        created_at: '2026-08-20T09:20:00Z',
      },
      {
        id: 'req_c02',
        tender_id: 'tender_gem_2026_03',
        clause: 'Clause 2.4',
        requirement_type: 'TURNOVER',
        field: 'financial.average_annual_turnover',
        operator: 'GTE',
        expected_value: 85000000,
        unit: 'INR',
        mandatory: true,
        source_page: 4,
        source_text: 'Annual turnover of INR 8.5 Crores or higher.',
        confidence: 0.93,
        requires_verification: true,
        is_approved: true,
        metadata_json: {},
        created_at: '2026-08-20T09:20:00Z',
      },
      {
        id: 'req_c03',
        tender_id: 'tender_gem_2026_03',
        clause: 'Clause 5.2',
        requirement_type: 'CUSTOM',
        field: 'credentials.oem_authorization',
        operator: 'EXISTS',
        expected_value: 'VALID_OEM_LETTER',
        unit: null,
        mandatory: true,
        source_page: 11,
        source_text: 'Original Equipment Manufacturer (OEM) authorization certificate required.',
        confidence: 0.82,
        requires_verification: true,
        is_approved: false,
        metadata_json: {},
        created_at: '2026-08-20T09:20:00Z',
      },
    ],
  },
  bidders: {
    tender_gem_2026_01: [
      {
        id: 'bidder_alpha_01',
        tender_id: 'tender_gem_2026_01',
        bidder_name: 'Alpha Infotech Private Limited',
        gstin: '07AABCA1234H1Z9',
        pan: 'AABCA1234H',
        udyam_number: 'UDYAM-DL-01-0012345',
        cin: 'U72200DL2018PTC123456',
        status: 'QUALIFIED',
        metadata_json: {},
        created_at: '2026-08-15T14:20:00Z',
        documents: [
          {
            id: 'doc_alpha_gst',
            tender_id: 'tender_gem_2026_01',
            bidder_id: 'bidder_alpha_01',
            filename: 'gst_certificate.pdf',
            storage_uri: 'data/uploads/gst_alpha.pdf',
            sha256: 'a1b2c3d4e5f678901234567890abcdef1234567890abcdef1234567890abcdef',
            document_type: 'GST_CERT',
            content_type: 'application/pdf',
            size_bytes: 452100,
            created_at: '2026-08-15T14:21:00Z',
          },
          {
            id: 'doc_alpha_turnover',
            tender_id: 'tender_gem_2026_01',
            bidder_id: 'bidder_alpha_01',
            filename: 'ca_turnover_certificate.pdf',
            storage_uri: 'data/uploads/turnover_alpha.pdf',
            sha256: 'b2c3d4e5f6a178901234567890abcdef1234567890abcdef1234567890abcdef',
            document_type: 'FINANCIAL_STATEMENT',
            content_type: 'application/pdf',
            size_bytes: 812400,
            created_at: '2026-08-15T14:22:00Z',
          },
        ],
      },
    ],
    tender_gem_2026_02: [
      {
        id: 'bidder_crest_02',
        tender_id: 'tender_gem_2026_02',
        bidder_name: 'Crest Enterprises',
        gstin: '33AABCC9999P1Z1',
        pan: 'AABCC9999P',
        udyam_number: null,
        cin: null,
        status: 'DISQUALIFIED',
        metadata_json: {},
        created_at: '2026-08-17T11:45:00Z',
        documents: [],
      },
    ],
    tender_gem_2026_03: [
      {
        id: 'bidder_bharat_03',
        tender_id: 'tender_gem_2026_03',
        bidder_name: 'Bharat Tech Solutions LLP',
        gstin: '27AAGCB5678K1Z3',
        pan: 'AAGCB5678K',
        udyam_number: 'UDYAM-MH-02-0054321',
        cin: null,
        status: 'PENDING',
        metadata_json: {},
        created_at: '2026-08-16T10:10:00Z',
        documents: [],
      },
    ],
  },
  complianceMatrices: {
    bidder_alpha_01: {
      bidder_id: 'bidder_alpha_01',
      tender_id: 'tender_gem_2026_01',
      overall_status: 'PASS',
      run_id: 'run_eval_2026_001',
      historical_limitations_notice: 'Evaluated under deterministic synthetic rules with immutable provenance.',
      rows: [
        {
          requirement_id: 'req_01',
          clause: 'Clause 4.1.1',
          requirement_type: 'GST',
          field: 'tax.gstin',
          operator: 'EQ',
          expected_value: 'VALID_ACTIVE',
          observed_value: 'VALID_ACTIVE',
          status: 'PASS',
          reason_code: 'EXACT_MATCH',
          evidence_ids: ['ev_gst_01'],
          review_required: false,
        },
        {
          requirement_id: 'req_02',
          clause: 'Clause 4.2.3',
          requirement_type: 'TURNOVER',
          field: 'financial.average_annual_turnover',
          operator: 'GTE',
          expected_value: 50000000,
          observed_value: 65000000,
          status: 'PASS',
          reason_code: 'NUMERIC_GTE',
          evidence_ids: ['ev_to_01'],
          review_required: false,
        },
        {
          requirement_id: 'req_03',
          clause: 'Clause 4.3.1',
          requirement_type: 'EXPERIENCE',
          field: 'experience.years',
          operator: 'GTE',
          expected_value: 3,
          observed_value: 5,
          status: 'PASS',
          reason_code: 'NUMERIC_GTE',
          evidence_ids: ['ev_exp_01'],
          review_required: false,
        },
      ],
    },
    bidder_crest_02: {
      bidder_id: 'bidder_crest_02',
      tender_id: 'tender_gem_2026_02',
      overall_status: 'FAIL',
      run_id: 'run_eval_2026_002',
      historical_limitations_notice: 'Mandatory financial turnover threshold not satisfied.',
      rows: [
        {
          requirement_id: 'req_b01',
          clause: 'Clause 3.1.2',
          requirement_type: 'GST',
          field: 'tax.gstin',
          operator: 'EQ',
          expected_value: 'VALID_ACTIVE',
          observed_value: 'VALID_ACTIVE',
          status: 'PASS',
          reason_code: 'EXACT_MATCH',
          evidence_ids: ['ev_gst_b01'],
          review_required: false,
        },
        {
          requirement_id: 'req_b02',
          clause: 'Clause 3.2.1',
          requirement_type: 'TURNOVER',
          field: 'financial.average_annual_turnover',
          operator: 'GTE',
          expected_value: 120000000,
          observed_value: 80000000,
          status: 'FAIL',
          reason_code: 'NUMERIC_LT_THRESHOLD',
          evidence_ids: ['ev_to_b02'],
          review_required: false,
        },
        {
          requirement_id: 'req_b03',
          clause: 'Clause 3.3.4',
          requirement_type: 'EXPERIENCE',
          field: 'experience.years',
          operator: 'GTE',
          expected_value: 5,
          observed_value: 6,
          status: 'PASS',
          reason_code: 'NUMERIC_GTE',
          evidence_ids: ['ev_exp_b03'],
          review_required: false,
        },
      ],
    },
    bidder_bharat_03: {
      bidder_id: 'bidder_bharat_03',
      tender_id: 'tender_gem_2026_03',
      overall_status: 'REVIEW_REQUIRED',
      run_id: 'run_eval_2026_003',
      historical_limitations_notice: 'Manual procurement officer validation required for OEM accreditation.',
      rows: [
        {
          requirement_id: 'req_c01',
          clause: 'Clause 2.1',
          requirement_type: 'GST',
          field: 'tax.gstin',
          operator: 'EQ',
          expected_value: 'VALID_ACTIVE',
          observed_value: 'VALID_ACTIVE',
          status: 'PASS',
          reason_code: 'EXACT_MATCH',
          evidence_ids: ['ev_gst_c01'],
          review_required: false,
        },
        {
          requirement_id: 'req_c02',
          clause: 'Clause 2.4',
          requirement_type: 'TURNOVER',
          field: 'financial.average_annual_turnover',
          operator: 'GTE',
          expected_value: 85000000,
          observed_value: 90000000,
          status: 'PASS',
          reason_code: 'NUMERIC_GTE',
          evidence_ids: ['ev_to_c02'],
          review_required: false,
        },
        {
          requirement_id: 'req_c03',
          clause: 'Clause 5.2',
          requirement_type: 'CUSTOM',
          field: 'credentials.oem_authorization',
          operator: 'EXISTS',
          expected_value: 'VALID_OEM_LETTER',
          observed_value: 'UNVERIFIED_LETTER_ATTACHED',
          status: 'REVIEW_REQUIRED',
          reason_code: 'MANUAL_DOCUMENT_VERIFICATION_REQUIRED',
          evidence_ids: ['ev_oem_c03'],
          review_required: true,
        },
      ],
    },
  },
  verifications: {
    bidder_alpha_01: [
      {
        id: 'vr_01',
        bidder_id: 'bidder_alpha_01',
        field: 'gstin',
        claimed_value: '07AABCA1234H1Z9',
        verified_value: 'Active (Tax Regular)',
        status: 'VERIFIED',
        source: 'GST_DEMO_DATA',
        mode: 'DEMO',
        checked_at: '2026-08-20T11:58:00Z',
        verification_reference: 'DEMO-GSTN-8492019',
      },
      {
        id: 'vr_02',
        bidder_id: 'bidder_alpha_01',
        field: 'udyam_number',
        claimed_value: 'UDYAM-DL-01-0012345',
        verified_value: 'Small Enterprise (Manufacturing)',
        status: 'VERIFIED',
        source: 'UDYAM_DEMO_DATA',
        mode: 'DEMO',
        checked_at: '2026-08-20T11:58:10Z',
        verification_reference: 'DEMO-UDYAM-55219',
      },
    ],
    bidder_crest_02: [
      {
        id: 'vr_b01',
        bidder_id: 'bidder_crest_02',
        field: 'gstin',
        claimed_value: '33AABCC9999P1Z1',
        verified_value: 'Active (Tax Regular)',
        status: 'VERIFIED',
        source: 'GST_DEMO_DATA',
        mode: 'DEMO',
        checked_at: '2026-08-20T11:58:00Z',
        verification_reference: 'DEMO-GSTN-999120',
      },
    ],
    bidder_bharat_03: [
      {
        id: 'vr_c01',
        bidder_id: 'bidder_bharat_03',
        field: 'gstin',
        claimed_value: '27AAGCB5678K1Z3',
        verified_value: 'Active (Tax Regular)',
        status: 'VERIFIED',
        source: 'GST_DEMO_DATA',
        mode: 'DEMO',
        checked_at: '2026-08-20T11:58:00Z',
        verification_reference: 'DEMO-GSTN-567812',
      },
    ],
  },
  humanDecisions: {
    bidder_alpha_01: 'QUALIFIED',
    bidder_crest_02: 'DISQUALIFIED',
    bidder_bharat_03: 'PENDING',
  },
  auditEvents: [
    {
      id: 'evt_01',
      job_id: 'job_extract_01',
      stage: 'EXTRACTION',
      status: 'COMPLETED',
      progress: 100,
      message: 'Tender requirements extracted and candidate rules prepared.',
      timestamp: '2026-08-01T10:06:00Z',
    },
    {
      id: 'evt_02',
      job_id: 'job_verify_alpha',
      stage: 'VERIFICATION',
      status: 'COMPLETED',
      progress: 100,
      message: 'Registry verification and multi-document fact validation completed.',
      timestamp: '2026-08-20T11:58:00Z',
    },
    {
      id: 'evt_03',
      job_id: 'job_compliance_alpha',
      stage: 'COMPLIANCE',
      status: 'COMPLETED',
      progress: 100,
      message: 'Deterministic compliance evaluation passed (3/3 rules satisfied).',
      timestamp: '2026-08-20T12:00:00Z',
    },
  ],
  jobs: {},
};

// ---------------------------------------------------------------------------
// STORE HELPERS
// ---------------------------------------------------------------------------

function getDefaultState(): DemoState {
  return JSON.parse(JSON.stringify(DEFAULT_DEMO_STATE));
}

function loadFromStorage(): DemoState {
  if (typeof window === 'undefined') return getDefaultState();
  try {
    const raw = localStorage.getItem(DEMO_STORAGE_KEY);
    if (!raw) return getDefaultState();
    const parsed = JSON.parse(raw) as Partial<DemoState>;
    if (!parsed || typeof parsed !== 'object' || !Array.isArray(parsed.tenders)) {
      return getDefaultState();
    }
    return {
      tenders: Array.isArray(parsed.tenders) ? parsed.tenders : DEFAULT_DEMO_STATE.tenders,
      requirements: parsed.requirements && typeof parsed.requirements === 'object' ? parsed.requirements : DEFAULT_DEMO_STATE.requirements,
      bidders: parsed.bidders && typeof parsed.bidders === 'object' ? parsed.bidders : DEFAULT_DEMO_STATE.bidders,
      complianceMatrices: parsed.complianceMatrices && typeof parsed.complianceMatrices === 'object' ? parsed.complianceMatrices : DEFAULT_DEMO_STATE.complianceMatrices,
      verifications: parsed.verifications && typeof parsed.verifications === 'object' ? parsed.verifications : DEFAULT_DEMO_STATE.verifications,
      humanDecisions: parsed.humanDecisions && typeof parsed.humanDecisions === 'object' ? parsed.humanDecisions : DEFAULT_DEMO_STATE.humanDecisions,
      auditEvents: Array.isArray(parsed.auditEvents) ? parsed.auditEvents : DEFAULT_DEMO_STATE.auditEvents,
      jobs: parsed.jobs && typeof parsed.jobs === 'object' ? parsed.jobs : {},
    };
  } catch {
    return getDefaultState();
  }
}

function saveToStorage(state: DemoState): void {
  if (typeof window === 'undefined') return;
  try {
    localStorage.setItem(DEMO_STORAGE_KEY, JSON.stringify(state));
  } catch (err) {
    console.error('Failed to save demo store to localStorage:', err);
  }
}

export const demoStore = {
  getDemoState(): DemoState {
    return loadFromStorage();
  },

  saveDemoState(state: DemoState): void {
    saveToStorage(state);
  },

  resetDemoState(): void {
    if (typeof window !== 'undefined') {
      localStorage.removeItem(DEMO_STORAGE_KEY);
    }
  },

  // -------------------------------------------------------------------------
  // TENDERS
  // -------------------------------------------------------------------------
  getTenders(): DemoTender[] {
    const state = loadFromStorage();
    return state.tenders;
  },

  getTender(id: string): DemoTender | null {
    const state = loadFromStorage();
    return state.tenders.find((t) => t.id === id || t.tender_number === id) || null;
  },

  createTender(data: TenderCreate, rfpFile?: File): DemoTender {
    const state = loadFromStorage();
    const tenderId = `tender_demo_${Date.now()}`;
    const now = new Date().toISOString();

    let attachedFile: DemoAttachedFile | null = null;
    if (rfpFile) {
      attachedFile = {
        filename: rfpFile.name,
        size_bytes: rfpFile.size,
        content_type: rfpFile.type || 'application/pdf',
        uploaded_at: now,
      };
    }

    const newTender: DemoTender = {
      id: tenderId,
      tender_number: data.tender_number,
      title: data.title,
      category: data.category || 'GOODS_AND_SERVICES',
      authority: data.authority || 'Procurement Authority',
      budget: data.budget || null,
      deadline: data.deadline || null,
      status: rfpFile ? 'RUNNING' : 'QUEUED',
      created_at: now,
      updated_at: now,
      attached_file: attachedFile,
    };

    state.tenders = [newTender, ...state.tenders];
    state.requirements[tenderId] = [];
    state.bidders[tenderId] = [];

    // Log audit events
    const createEvt: AuditEventRead = {
      id: `evt_${Date.now()}_1`,
      job_id: `job_${tenderId}`,
      stage: 'UPLOAD',
      status: 'COMPLETED',
      progress: 100,
      message: `Procurement tender "${data.title}" registered in demo workspace.`,
      timestamp: now,
    };

    state.auditEvents = [createEvt, ...state.auditEvents];
    saveToStorage(state);
    return newTender;
  },

  updateTenderStatus(tenderId: string, status: DemoTender['status']): void {
    const state = loadFromStorage();
    const index = state.tenders.findIndex((t) => t.id === tenderId || t.tender_number === tenderId);
    if (index !== -1) {
      state.tenders[index].status = status;
      state.tenders[index].updated_at = new Date().toISOString();
      saveToStorage(state);
    }
  },

  // -------------------------------------------------------------------------
  // REQUIREMENTS & AI EXTRACTION SIMULATION
  // -------------------------------------------------------------------------
  getRequirements(tenderId: string): TenderRequirementRead[] {
    const state = loadFromStorage();
    const tender = this.getTender(tenderId);
    const key = tender ? tender.id : tenderId;
    return state.requirements[key] || state.requirements[tenderId] || [];
  },

  setRequirements(tenderId: string, reqs: TenderRequirementRead[]): void {
    const state = loadFromStorage();
    const tender = this.getTender(tenderId);
    const key = tender ? tender.id : tenderId;
    state.requirements[key] = reqs;
    saveToStorage(state);
  },

  generateSyntheticRequirements(tenderId: string, budget?: number | null): TenderRequirementRead[] {
    const now = new Date().toISOString();
    const formattedBudget = budget ? `₹ ${(budget * 0.5).toLocaleString('en-IN')}` : 'INR 1,00,00,000';
    const turnoverVal = budget ? Math.round(budget * 0.5) : 10000000;

    return [
      {
        id: `req_${tenderId}_gst`,
        tender_id: tenderId,
        clause: 'Clause 1.1 (GST)',
        requirement_type: 'GST',
        field: 'tax.gstin',
        operator: 'EQ',
        expected_value: 'VALID_ACTIVE',
        unit: null,
        mandatory: true,
        source_page: 1,
        source_text: 'Bidder must possess a valid and active GSTIN registration in India. (Synthetic Demo Extraction)',
        confidence: 0.98,
        requires_verification: true,
        is_approved: true,
        metadata_json: {},
        created_at: now,
      },
      {
        id: `req_${tenderId}_turnover`,
        tender_id: tenderId,
        clause: 'Clause 2.3 (Turnover)',
        requirement_type: 'TURNOVER',
        field: 'financial.average_annual_turnover',
        operator: 'GTE',
        expected_value: turnoverVal,
        unit: 'INR',
        mandatory: true,
        source_page: 3,
        source_text: `Minimum average annual turnover of at least ${formattedBudget} over the past 3 financial years. (Synthetic Demo Extraction)`,
        confidence: 0.95,
        requires_verification: true,
        is_approved: true,
        metadata_json: {},
        created_at: now,
      },
      {
        id: `req_${tenderId}_experience`,
        tender_id: tenderId,
        clause: 'Clause 3.2 (Experience)',
        requirement_type: 'EXPERIENCE',
        field: 'experience.years',
        operator: 'GTE',
        expected_value: 3,
        unit: 'YEARS',
        mandatory: true,
        source_page: 5,
        source_text: 'Minimum 3 years of continuous operating experience in relevant project execution. (Synthetic Demo Extraction)',
        confidence: 0.92,
        requires_verification: true,
        is_approved: true,
        metadata_json: {},
        created_at: now,
      },
      {
        id: `req_${tenderId}_oem`,
        tender_id: tenderId,
        clause: 'Clause 4.1 (Certification)',
        requirement_type: 'CUSTOM',
        field: 'credentials.oem_authorization',
        operator: 'EXISTS',
        expected_value: 'VALID_OEM_LETTER',
        unit: null,
        mandatory: true,
        source_page: 7,
        source_text: 'Original Equipment Manufacturer (OEM) authorization certificate / Technical certification required. (Synthetic Demo Extraction)',
        confidence: 0.89,
        requires_verification: true,
        is_approved: true,
        metadata_json: {},
        created_at: now,
      },
    ];
  },

  mergeRequirements(existing: TenderRequirementRead[], newReqs: TenderRequirementRead[]): TenderRequirementRead[] {
    const resultMap = new Map<string, TenderRequirementRead>();
    for (const item of existing) {
      resultMap.set(item.id, item);
      resultMap.set(`field:${item.field}`, item);
    }
    for (const item of newReqs) {
      const fieldKey = `field:${item.field}`;
      if (resultMap.has(fieldKey)) {
        const existingItem = resultMap.get(fieldKey)!;
        resultMap.set(existingItem.id, { ...existingItem, ...item, id: existingItem.id });
      } else if (resultMap.has(item.id)) {
        resultMap.set(item.id, item);
      } else {
        resultMap.set(item.id, item);
        resultMap.set(fieldKey, item);
      }
    }
    const finalReqs: TenderRequirementRead[] = [];
    const seenIds = new Set<string>();
    for (const item of resultMap.values()) {
      if (!seenIds.has(item.id)) {
        seenIds.add(item.id);
        finalReqs.push(item);
      }
    }
    return finalReqs;
  },

  extractCriteria(
    tenderId: string,
    customJobId?: string,
    onProgress?: (stage: string, progress: number) => void
  ): Promise<TenderRequirementRead[]> {
    const tender = this.getTender(tenderId);
    const resolvedTenderId = tender ? tender.id : tenderId;
    const jobId = customJobId || `job_extract_${Date.now()}`;

    this.createJob(jobId, 'UPLOAD', 'RUNNING', 0, resolvedTenderId);

    return new Promise((resolve, reject) => {
      try {
        const steps: {
          stage: JobStage;
          pct: number;
          status: JobStatus;
          action: string;
          msg: string;
        }[] = [
          { stage: 'UPLOAD', pct: 15, status: 'RUNNING', action: 'DOCUMENT_UPLOADED', msg: 'Tender RFP document uploaded for analysis.' },
          { stage: 'UPLOAD', pct: 25, status: 'RUNNING', action: 'PROCESSING_STARTED', msg: 'Synthetic criteria extraction pipeline started.' },
          { stage: 'PARSING', pct: 45, status: 'RUNNING', action: 'PARSING_COMPLETED', msg: 'Document text, sections, and tables parsed.' },
          { stage: 'EXTRACTION', pct: 65, status: 'RUNNING', action: 'EXTRACTION_COMPLETED', msg: 'AI extraction identified statutory and technical criteria.' },
          { stage: 'VERIFICATION', pct: 85, status: 'RUNNING', action: 'VERIFICATION_COMPLETED', msg: 'Rule definitions and mandatory flags verified.' },
          { stage: 'COMPLIANCE', pct: 95, status: 'RUNNING', action: 'COMPLIANCE_COMPLETED', msg: 'Compliance matrix initialized with extracted criteria.' },
          { stage: 'REPORTING', pct: 100, status: 'COMPLETED', action: 'PROCESSING_COMPLETED', msg: 'Synthetic extraction pipeline completed successfully.' },
        ];

        let stepIdx = 0;
        const interval = setInterval(() => {
          try {
            if (stepIdx < steps.length) {
              const step = steps[stepIdx];
              if (onProgress) onProgress(step.stage, step.pct);

              this.updateJob(jobId, {
                current_stage: step.stage,
                status: step.status,
                progress: step.pct,
              });

              const evt: AuditEventRead = {
                id: `evt_${Date.now()}_${stepIdx}_${Math.floor(Math.random() * 1000)}`,
                job_id: jobId,
                stage: step.stage,
                status: step.status === 'COMPLETED' ? 'COMPLETED' : 'RUNNING',
                progress: step.pct,
                message: step.msg,
                timestamp: new Date().toISOString(),
              };
              this.addAuditEvent(evt);
              stepIdx++;
            } else {
              clearInterval(interval);

              const generated = this.generateSyntheticRequirements(resolvedTenderId, tender?.budget);
              const existing = this.getRequirements(resolvedTenderId);
              const merged = this.mergeRequirements(existing, generated);

              this.setRequirements(resolvedTenderId, merged);
              this.updateTenderStatus(resolvedTenderId, 'COMPLETED');

              resolve(merged);
            }
          } catch (err: unknown) {
            clearInterval(interval);
            const errMsg = err instanceof Error ? err.message : 'Extraction step execution failed.';
            this.updateJob(jobId, {
              status: 'FAILED',
              error_message: errMsg,
            });
            const failEvt: AuditEventRead = {
              id: `evt_${Date.now()}_fail`,
              job_id: jobId,
              stage: 'EXTRACTION',
              status: 'FAILED',
              progress: 0,
              message: `Synthetic extraction failed: ${errMsg}`,
              timestamp: new Date().toISOString(),
            };
            this.addAuditEvent(failEvt);
            reject(err instanceof Error ? err : new Error(errMsg));
          }
        }, 300);
      } catch (outerErr: unknown) {
        const errMsg = outerErr instanceof Error ? outerErr.message : 'Extraction initialization failed.';
        this.updateJob(jobId, {
          status: 'FAILED',
          error_message: errMsg,
        });
        reject(outerErr instanceof Error ? outerErr : new Error(errMsg));
      }
    });
  },

  // -------------------------------------------------------------------------
  // BIDDERS
  // -------------------------------------------------------------------------
  getBidders(tenderId: string): BidderRead[] {
    const state = loadFromStorage();
    return state.bidders[tenderId] || [];
  },

  getBidder(bidderId: string): BidderRead | null {
    const state = loadFromStorage();
    for (const list of Object.values(state.bidders)) {
      const found = list.find((b) => b.id === bidderId);
      if (found) return found;
    }
    return null;
  },

  createBidder(tenderId: string, data: BidderCreate): BidderRead {
    const state = loadFromStorage();
    const bidderId = `bidder_demo_${Date.now()}`;
    const now = new Date().toISOString();

    const newBidder: BidderRead = {
      id: bidderId,
      tender_id: tenderId,
      bidder_name: data.bidder_name,
      gstin: data.gstin || null,
      pan: data.pan || null,
      udyam_number: data.udyam_number || null,
      cin: data.cin || null,
      status: 'PENDING',
      metadata_json: {},
      created_at: now,
      documents: [],
    };

    if (!state.bidders[tenderId]) {
      state.bidders[tenderId] = [];
    }
    state.bidders[tenderId] = [newBidder, ...state.bidders[tenderId]];

    // Default pending decision
    state.humanDecisions[bidderId] = 'PENDING';

    // Default compliance matrix
    state.complianceMatrices[bidderId] = {
      bidder_id: bidderId,
      tender_id: tenderId,
      overall_status: 'REVIEW_REQUIRED',
      run_id: `run_eval_${Date.now()}`,
      historical_limitations_notice: 'Deterministic synthetic evaluation pending human officer review.',
      rows: [
        {
          requirement_id: 'req_default',
          clause: 'Clause 1.1',
          requirement_type: 'GST',
          field: 'tax.gstin',
          operator: 'EQ',
          expected_value: 'VALID_ACTIVE',
          observed_value: data.gstin ? 'VALID_ACTIVE' : 'NOT_PROVIDED',
          status: data.gstin ? 'PASS' : 'REVIEW_REQUIRED',
          reason_code: data.gstin ? 'EXACT_MATCH' : 'MISSING_DOCUMENT',
          evidence_ids: [],
          review_required: !data.gstin,
        },
      ],
    };

    // Log audit event
    const evt: AuditEventRead = {
      id: `evt_${Date.now()}_bidder`,
      job_id: `job_bidder_${bidderId}`,
      stage: 'UPLOAD',
      status: 'COMPLETED',
      progress: 100,
      message: `Bidder "${data.bidder_name}" registered for tender in demo workspace.`,
      timestamp: now,
    };
    state.auditEvents = [evt, ...state.auditEvents];

    saveToStorage(state);
    return newBidder;
  },

  // -------------------------------------------------------------------------
  // COMPLIANCE MATRIX & REPORTS
  // -------------------------------------------------------------------------
  getComplianceMatrix(bidderId: string): ComplianceMatrixRead | null {
    const state = loadFromStorage();
    return state.complianceMatrices[bidderId] || null;
  },

  recordHumanDecision(bidderId: string, decision: HumanDecisionStatus): void {
    const state = loadFromStorage();
    state.humanDecisions[bidderId] = decision;

    // Update bidder status
    for (const tenderId of Object.keys(state.bidders)) {
      const idx = state.bidders[tenderId].findIndex((b) => b.id === bidderId);
      if (idx !== -1) {
        state.bidders[tenderId][idx].status = decision;
        break;
      }
    }

    // Log audit event
    const evt: AuditEventRead = {
      id: `evt_${Date.now()}_decision`,
      job_id: `job_decision_${bidderId}`,
      stage: 'REPORTING',
      status: 'COMPLETED',
      progress: 100,
      message: `Officer recorded human decision: ${decision} for bidder ${bidderId}.`,
      timestamp: new Date().toISOString(),
    };
    state.auditEvents = [evt, ...state.auditEvents];

    saveToStorage(state);
  },

  getReport(bidderId: string): ReportRead | null {
    const state = loadFromStorage();
    const bidder = this.getBidder(bidderId);
    if (!bidder) return null;

    const tender = this.getTender(bidder.tender_id);
    const matrix = this.getComplianceMatrix(bidderId);
    const decision = state.humanDecisions[bidderId] || 'PENDING';

    return {
      generated_at: new Date().toISOString(),
      tender: tender || {
        id: bidder.tender_id,
        tender_number: 'N/A',
        title: 'Tender Detail',
        category: null,
        authority: null,
        budget: null,
        deadline: null,
        status: 'COMPLETED',
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      },
      bidder,
      compliance_overview: {
        bidder_id: bidderId,
        tender_id: bidder.tender_id,
        overall_status: matrix?.overall_status || 'REVIEW_REQUIRED',
        human_decision_status: decision,
      },
      compliance_matrix: matrix || {
        bidder_id: bidderId,
        tender_id: bidder.tender_id,
        overall_status: 'REVIEW_REQUIRED',
        run_id: 'run_demo',
        historical_limitations_notice: 'Synthetic evaluation report.',
        rows: [],
      },
      historical_limitations_notice: 'Evaluation completed with zero critical compliance flags.',
      audit_trail_count: state.auditEvents.length,
    };
  },

  // -------------------------------------------------------------------------
  // AUDIT EVENTS
  // -------------------------------------------------------------------------
  getAuditEvents(): AuditEventRead[] {
    const state = loadFromStorage();
    return state.auditEvents;
  },

  addAuditEvent(event: AuditEventRead): void {
    const state = loadFromStorage();
    state.auditEvents = [event, ...state.auditEvents];
    saveToStorage(state);
  },

  // -------------------------------------------------------------------------
  // DASHBOARD STATS (DYNAMIC DERIVATION)
  // -------------------------------------------------------------------------
  getDashboardStats() {
    const state = loadFromStorage();
    const activeTenders = state.tenders.length;

    let qualifiedBidders = 0;
    let disqualifiedBidders = 0;
    let pendingReviewBidders = 0;

    for (const list of Object.values(state.bidders)) {
      for (const b of list) {
        const decision = state.humanDecisions[b.id] || b.status;
        if (decision === 'QUALIFIED') {
          qualifiedBidders++;
        } else if (decision === 'DISQUALIFIED') {
          disqualifiedBidders++;
        } else {
          pendingReviewBidders++;
        }
      }
    }

    return {
      activeTenders,
      qualifiedBidders,
      disqualifiedBidders,
      pendingReviewBidders,
    };
  },

  // -------------------------------------------------------------------------
  // DEMO JOBS
  // -------------------------------------------------------------------------
  getJob(jobId: string): JobRead | null {
    const state = loadFromStorage();
    return state.jobs[jobId] || null;
  },

  createJob(jobId: string, stage: JobRead['current_stage'], status: JobRead['status'], progress: number, targetId = ''): JobRead {
    const state = loadFromStorage();
    const job: JobRead = {
      id: jobId,
      target_type: 'TENDER',
      target_id: targetId,
      job_type: 'SIMULATED_PIPELINE',
      current_stage: stage,
      status: status,
      progress: progress,
      error_message: null,
      started_at: new Date().toISOString(),
    };
    state.jobs[jobId] = job;
    saveToStorage(state);
    return job;
  },

  updateJob(jobId: string, updates: Partial<JobRead>): void {
    const state = loadFromStorage();
    if (state.jobs[jobId]) {
      state.jobs[jobId] = {
        ...state.jobs[jobId],
        ...updates,
      };
      saveToStorage(state);
    }
  },

  getJobEvents(jobId: string): JobEventRead[] {
    const state = loadFromStorage();
    const matching = state.auditEvents.filter((ev) => ev.job_id === jobId);
    return matching.map((ev, idx) => ({
      id: ev.id || `job_evt_${jobId}_${idx}`,
      job_id: ev.job_id || jobId,
      stage: (ev.stage as JobStage) || 'UPLOAD',
      status: (ev.status as JobStatus) || 'COMPLETED',
      progress: ev.progress ?? 100,
      message: ev.message || '',
      timestamp: ev.timestamp || new Date().toISOString(),
    }));
  },
};
