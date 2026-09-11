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
  payload_json?: Record<string, unknown>;
  payload?: Record<string, unknown>;
  job_id?: string;
  stage?: JobStage | string;
  status?: JobStatus | string;
  progress?: number;
  message?: string;
  timestamp?: string;
  created_at?: string;
}

export interface AuditEventRead {
  id?: string;
  job_id?: string | null;
  stage?: JobStage | null;
  status?: JobStatus | null;
  progress?: number | null;
  message?: string | null;
  timestamp?: string | null;
}

