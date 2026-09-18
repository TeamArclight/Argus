import React from 'react';
import { CheckCircle, XCircle, Clock, AlertCircle, Info } from 'lucide-react';
import type { AuditEventRead } from '@/services/types';

export interface ResolvedStatus {
  label: string;
  variant: 'SUCCESS' | 'FAILED' | 'RUNNING' | 'PASS' | 'FAIL' | 'REVIEW_REQUIRED' | 'INFO';
}

/**
 * Extracts trustworthy Job ID from event or backend payload, never fabricating one.
 */
export function getResolvedJobId(event: AuditEventRead): string | null {
  if (typeof event.job_id === 'string' && event.job_id.trim() !== '' && event.job_id !== '—') {
    return event.job_id.trim();
  }
  const payload = (event.payload_json || {}) as Record<string, unknown>;
  if (typeof payload.job_id === 'string' && payload.job_id.trim() !== '' && payload.job_id !== '—') {
    return payload.job_id.trim();
  }
  if (typeof payload.parent_job_id === 'string' && payload.parent_job_id.trim() !== '' && payload.parent_job_id !== '—') {
    return payload.parent_job_id.trim();
  }
  if (typeof payload.jobId === 'string' && payload.jobId.trim() !== '' && payload.jobId !== '—') {
    return payload.jobId.trim();
  }
  if (event.entity_type === 'JOB' && typeof event.entity_id === 'string' && event.entity_id.trim() !== '') {
    return event.entity_id.trim();
  }
  return null;
}

/**
 * Determines whether an event is backed by a ProcessingJob.
 */
export function isJobBackedEvent(event: AuditEventRead): boolean {
  if (getResolvedJobId(event) !== null) return true;
  if (typeof event.progress === 'number' && Number.isFinite(event.progress)) return true;
  const payload = (event.payload_json || {}) as Record<string, unknown>;
  if (typeof payload.progress === 'number' && Number.isFinite(payload.progress)) return true;
  if (typeof payload.progress_pct === 'number' && Number.isFinite(payload.progress_pct)) return true;
  return false;
}

/**
 * Resolves progress percentage for job-backed events, or null for non-job events.
 */
export function getResolvedProgress(event: AuditEventRead): number | null {
  if (!isJobBackedEvent(event)) return null;

  if (typeof event.progress === 'number' && Number.isFinite(event.progress)) {
    return Math.min(Math.max(Math.round(event.progress), 0), 100);
  }

  const payload = (event.payload_json || {}) as Record<string, unknown>;
  if (typeof payload.progress === 'number' && Number.isFinite(payload.progress)) {
    return Math.min(Math.max(Math.round(payload.progress), 0), 100);
  }
  if (typeof payload.progress_pct === 'number' && Number.isFinite(payload.progress_pct)) {
    return Math.min(Math.max(Math.round(payload.progress_pct), 0), 100);
  }

  const rawStatus = (event.status || '').toUpperCase();
  const action = (event.action || '').toUpperCase();
  if (rawStatus === 'COMPLETED' || rawStatus === 'SUCCESS' || action.endsWith('_COMPLETED')) {
    return 100;
  }
  if (rawStatus === 'RUNNING' || action.endsWith('_STARTED')) {
    return 10;
  }

  return 0;
}

/**
 * Resolves meaningful status according to specified priority:
 * 1. event.status
 * 2. structured payload status / result
 * 3. clause evaluation result
 * 4. human decision state
 * 5. event category-specific informational state
 * Fallback: INFO (instead of empty dash)
 */
export function resolveAuditStatus(event: AuditEventRead): ResolvedStatus {
  const payload = (event.payload_json || {}) as Record<string, unknown>;
  const action = (event.action || '').toUpperCase();
  const rawStatus = (event.status || '').toUpperCase();

  // 1. Clause Evaluation
  if (action === 'CLAUSE_EVALUATED' || Boolean(payload.clause_reference)) {
    const clauseStatus = String(payload.status || event.status || '').toUpperCase();
    if (clauseStatus === 'PASS' || clauseStatus === 'SATISFIED') {
      return { label: 'PASS', variant: 'PASS' };
    }
    if (clauseStatus === 'FAIL' || clauseStatus === 'FAILED') {
      return { label: 'FAIL', variant: 'FAIL' };
    }
    if (clauseStatus === 'REVIEW_REQUIRED' || clauseStatus === 'REVIEW') {
      return { label: 'REVIEW', variant: 'REVIEW_REQUIRED' };
    }
    if (clauseStatus) {
      return { label: clauseStatus, variant: 'INFO' };
    }
  }

  // 2. Human Decision
  if (action === 'HUMAN_DECISION_RECORDED' || Boolean(payload.officer_decision)) {
    const decision = String(payload.officer_decision || payload.decision || event.status || 'RECORDED').toUpperCase();
    if (decision === 'APPROVED' || decision === 'CONFIRMED') {
      return { label: 'APPROVED', variant: 'SUCCESS' };
    }
    if (decision === 'REJECTED') {
      return { label: 'REJECTED', variant: 'FAILED' };
    }
    if (decision === 'OVERRIDE') {
      return { label: 'OVERRIDE', variant: 'REVIEW_REQUIRED' };
    }
    return { label: decision, variant: 'INFO' };
  }

  // 3. Provider Health Check
  if (action.startsWith('PROVIDER_HEALTH') || event.event_category === 'PROVIDER_HEALTH') {
    const pStatus = String(payload.status || payload.provider_status || rawStatus || '').toUpperCase();
    if (pStatus === 'DEGRADED') {
      return { label: 'DEGRADED', variant: 'REVIEW_REQUIRED' };
    }
    if (pStatus === 'FAILED' || pStatus === 'DOWN' || pStatus === 'ERROR') {
      return { label: 'FAILED', variant: 'FAILED' };
    }
    // Execution of health check confirmed passed
    return { label: 'SUCCESS', variant: 'SUCCESS' };
  }

  // 4. Demo Seed
  if (action === 'DEMO_SEEDED' || action.includes('SEED')) {
    if (payload.success === false || rawStatus === 'FAILED' || payload.status === 'FAILED') {
      return { label: 'FAILED', variant: 'FAILED' };
    }
    return { label: 'SUCCESS', variant: 'SUCCESS' };
  }

  // 5. Compliance Run Completed / Started
  if (action === 'COMPLIANCE_EVALUATION_COMPLETED' || action === 'COMPLIANCE_RUN_COMPLETED') {
    if (rawStatus === 'FAILED' || payload.status === 'FAILED') {
      return { label: 'FAILED', variant: 'FAILED' };
    }
    return { label: 'SUCCESS', variant: 'SUCCESS' };
  }

  // 6. RAG Ingestion / Document Processing
  if (action.includes('RAG_INGEST') || action.includes('DOCUMENT_UPLOADED') || action.includes('EXTRACTION') || action.includes('PARSING')) {
    if (rawStatus === 'SUCCESS' || rawStatus === 'COMPLETED' || action.endsWith('_COMPLETED') || action.endsWith('_UPLOADED') || action.endsWith('_INGESTED')) {
      return { label: 'SUCCESS', variant: 'SUCCESS' };
    }
    if (rawStatus === 'FAILED' || rawStatus === 'ERROR' || action.endsWith('_FAILED')) {
      return { label: 'FAILED', variant: 'FAILED' };
    }
    if (rawStatus === 'RUNNING' || action.endsWith('_STARTED')) {
      return { label: 'RUNNING', variant: 'RUNNING' };
    }
  }

  // 7. Direct event.status
  if (rawStatus === 'COMPLETED' || rawStatus === 'SUCCESS') {
    return { label: 'SUCCESS', variant: 'SUCCESS' };
  }
  if (rawStatus === 'RUNNING') {
    return { label: 'RUNNING', variant: 'RUNNING' };
  }
  if (rawStatus === 'FAILED' || rawStatus === 'ERROR') {
    return { label: 'FAILED', variant: 'FAILED' };
  }

  // 8. Payload result / status
  const payloadStatus = String(payload.status || payload.result || payload.outcome || '').toUpperCase();
  if (payloadStatus === 'SUCCESS' || payloadStatus === 'COMPLETED') {
    return { label: 'SUCCESS', variant: 'SUCCESS' };
  }
  if (payloadStatus === 'FAILED' || payloadStatus === 'ERROR') {
    return { label: 'FAILED', variant: 'FAILED' };
  }
  if (payloadStatus === 'RUNNING') {
    return { label: 'RUNNING', variant: 'RUNNING' };
  }

  // 9. Action name heuristics
  if (action.includes('COMPLETED') || action.includes('APPROVED') || action.includes('CREATED') || action.includes('RESOLVED')) {
    return { label: 'SUCCESS', variant: 'SUCCESS' };
  }
  if (action.includes('FAILED') || action.includes('ERROR') || action.includes('ORPHANED')) {
    return { label: 'FAILED', variant: 'FAILED' };
  }
  if (action.includes('STARTED') || action.includes('RUNNING') || action.includes('PROCESSING')) {
    return { label: 'RUNNING', variant: 'RUNNING' };
  }

  // 10. Informational status fallback (replaces missing dash)
  return { label: 'INFO', variant: 'INFO' };
}

/**
 * Renders consistent status pill badge.
 */
export function AuditStatusBadge({ status }: { status: ResolvedStatus }) {
  switch (status.variant) {
    case 'SUCCESS':
      return (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 whitespace-nowrap">
          <CheckCircle className="w-2.5 h-2.5" /> {status.label}
        </span>
      );
    case 'PASS':
      return (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-mono font-bold bg-emerald-950/80 text-emerald-300 border border-emerald-700/80 whitespace-nowrap">
          <CheckCircle className="w-2.5 h-2.5" /> PASS
        </span>
      );
    case 'FAIL':
      return (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-mono font-bold bg-rose-950/80 text-rose-300 border border-rose-700/80 whitespace-nowrap">
          <XCircle className="w-2.5 h-2.5" /> FAIL
        </span>
      );
    case 'FAILED':
      return (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-rose-500/10 text-rose-400 border border-rose-500/20 whitespace-nowrap">
          <XCircle className="w-2.5 h-2.5" /> {status.label}
        </span>
      );
    case 'RUNNING':
      return (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-blue-500/10 text-blue-400 border border-blue-500/20 whitespace-nowrap">
          <Clock className="w-2.5 h-2.5 animate-spin" /> RUNNING
        </span>
      );
    case 'REVIEW_REQUIRED':
      return (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-amber-500/10 text-amber-300 border border-amber-500/20 whitespace-nowrap">
          <AlertCircle className="w-2.5 h-2.5" /> {status.label}
        </span>
      );
    case 'INFO':
    default:
      return (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-mono bg-sky-950/60 text-sky-400 border border-sky-800/50 whitespace-nowrap">
          <Info className="w-2.5 h-2.5 text-sky-400/80" /> {status.label}
        </span>
      );
  }
}

/**
 * Safely parses an audit timestamp into a Date object.
 * If backend sent a naive ISO string without timezone ('Z' or offset),
 * it treats it as UTC (since all backend timestamps are stored as UTC)
 * rather than allowing JavaScript to mistakenly treat naive ISO strings as local time.
 */
export function parseAuditDate(ts?: string | null): Date | null {
  if (!ts || ts === '—' || ts === 'undefined' || ts === 'null') return null;
  try {
    let s = ts.trim().replace(/^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})/, '$1T$2');
    if (!s.endsWith('Z') && !/[+-]\d{2}(?::?\d{2})?$/.test(s)) {
      s += 'Z';
    }
    const d = new Date(s);
    return isNaN(d.getTime()) ? null : d;
  } catch {
    return null;
  }
}

/**
 * Formats an audit timestamp in the user's local timezone.
 */
export function formatAuditDate(ts?: string | null): string {
  const d = parseAuditDate(ts);
  if (!d) return '—';
  return (
    d.toLocaleDateString('en-GB', {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
    }) +
    ', ' +
    d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  );
}

/**
 * Formats full audit timestamp with seconds and timezone indicator for drawer/details.
 */
export function formatAuditFullDate(ts?: string | null): string {
  const d = parseAuditDate(ts);
  if (!d) return 'Time unavailable';
  return d.toLocaleString([], {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    timeZoneName: 'short',
  });
}

