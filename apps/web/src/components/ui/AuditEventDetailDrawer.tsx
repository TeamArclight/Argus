'use client';

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import {
  X,
  ShieldCheck,
  Sparkles,
  Database,
  Scale,
  UserCheck,
  ArrowUpRight,
  Copy,
  Check,
  ExternalLink,
  Layers,
  Activity,
  Hash,
} from 'lucide-react';
import type { AuditEventRead } from '@/services/types';
import { formatDisplayValue, formatExpectedCondition, isStructuredValue } from '@/lib/formatters';
import { StructuredValueView } from './StructuredValueView';
import {
  getResolvedJobId,
  isJobBackedEvent,
  getResolvedProgress,
  resolveAuditStatus,
  AuditStatusBadge,
  formatAuditFullDate,
} from '@/lib/audit-helpers';

const SENSITIVE_KEY_PATTERNS = [
  'token',
  'access_token',
  'authorization',
  'password',
  'secret',
  'api_key',
  'apikey',
  'database_url',
  'service_role_key',
  'jwt',
  'bearer',
  'client_secret',
  'refresh_token',
];

export function isSensitiveKey(key: string): boolean {
  const lower = key.toLowerCase();
  return SENSITIVE_KEY_PATTERNS.some((pattern) => lower.includes(pattern));
}

export function redactSensitiveData<T>(obj: T): T {
  if (obj === null || obj === undefined) return obj;
  if (typeof obj !== 'object') return obj;

  if (Array.isArray(obj)) {
    return obj.map((item) => redactSensitiveData(item)) as unknown as T;
  }

  const result: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
    if (isSensitiveKey(k) && v !== null && v !== undefined) {
      result[k] = '[REDACTED]';
    } else if (typeof v === 'object' && v !== null) {
      result[k] = redactSensitiveData(v);
    } else {
      result[k] = v;
    }
  }
  return result as T;
}

interface AuditEventDetailDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  event: AuditEventRead | null;
}

export const AuditEventDetailDrawer: React.FC<AuditEventDetailDrawerProps> = ({
  isOpen,
  onClose,
  event,
}) => {
  const [copied, setCopied] = useState(false);

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  // Lock body scroll when drawer is open
  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
    return () => {
      document.body.style.overflow = '';
    };
  }, [isOpen]);

  if (!isOpen || !event) return null;

  const payload = (event.payload_json || {}) as Record<string, unknown>;
  const isClauseEval = event.action === 'CLAUSE_EVALUATED' || Boolean(payload.clause_reference);
  const isDecision = event.action === 'HUMAN_DECISION_RECORDED' || Boolean(payload.officer_decision);

  const stageDisplay = event.pipeline_stage || event.stage;
  const isDemo = event.mode === 'DEMO';

  const resolvedJobId = getResolvedJobId(event);
  const isJob = isJobBackedEvent(event);
  const progressVal = getResolvedProgress(event);
  const resolvedStatus = resolveAuditStatus(event);

  const formatFullTimestamp = (ts?: string | null) => {
    return formatAuditFullDate(ts);
  };

  const copyPayload = () => {
    const rawData = {
      id: event.id,
      timestamp: event.timestamp,
      mode: event.mode,
      event_category: event.event_category,
      pipeline_stage: stageDisplay,
      job_id: event.job_id,
      status: event.status,
      progress: event.progress,
      action: event.action,
      entity_type: event.entity_type,
      entity_id: event.entity_id,
      actor: event.actor,
      actor_user_id: event.actor_user_id || payload.actor_user_id,
      actor_name: event.actor_name || payload.actor_name,
      actor_email: event.actor_email || payload.actor_email,
      target_url: event.target_url,
      ...payload,
    };
    const sanitized = redactSensitiveData(rawData);
    const jsonStr = JSON.stringify(sanitized, null, 2);
    navigator.clipboard.writeText(jsonStr);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/70 backdrop-blur-sm animate-in fade-in duration-200">
      {/* Backdrop click dismiss */}
      <div className="fixed inset-0" onClick={onClose} aria-hidden="true" />

      {/* Right-Side Drawer / Mobile Full-Screen Sheet */}
      <div
        className="relative z-10 w-full sm:w-[min(720px,90vw)] h-full bg-zinc-950 border-l border-zinc-800 flex flex-col shadow-2xl overflow-hidden"
        role="dialog"
        aria-modal="true"
        aria-labelledby="drawer-title"
      >
        {/* Drawer Header */}
        <div className="p-5 border-b border-zinc-800 bg-zinc-900/80 flex items-start justify-between gap-4 flex-shrink-0">
          <div className="space-y-1.5 min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-mono font-semibold bg-blue-950/80 text-blue-300 border border-blue-800/60">
                <ShieldCheck className="w-3 h-3 text-blue-400" />
                AUDIT EVENT DETAIL
              </span>
              <span
                className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-mono font-semibold border ${
                  isDemo
                    ? 'bg-amber-950/60 text-amber-300 border-amber-800/60'
                    : 'bg-emerald-950/60 text-emerald-300 border-emerald-800/60'
                }`}
              >
                {isDemo ? <Sparkles className="w-2.5 h-2.5 text-amber-400" /> : <Database className="w-2.5 h-2.5 text-emerald-400" />}
                {isDemo ? 'DEMO' : 'AUTHENTIC'}
              </span>
            </div>

            <h2 id="drawer-title" className="text-base font-bold text-zinc-100 font-mono break-all leading-tight">
              {event.action || event.event_category || 'Audit Record'}
            </h2>

            <p className="text-xs font-mono text-zinc-400 flex flex-wrap items-center gap-x-3 gap-y-1">
              <span>{formatFullTimestamp(event.timestamp)}</span>
              <span className="text-zinc-600">•</span>
              {resolvedJobId ? (
                <span className="text-zinc-300 font-mono">Job: {resolvedJobId}</span>
              ) : (
                <span className="text-zinc-500 font-mono">Not job-based</span>
              )}
            </p>
          </div>

          <button
            onClick={onClose}
            className="p-1.5 rounded-lg border border-zinc-700 bg-zinc-800 hover:bg-zinc-700 text-zinc-400 hover:text-white transition-colors cursor-pointer"
            aria-label="Close drawer"
            title="Close (Esc)"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Badges Strip */}
        <div className="px-5 py-2.5 bg-zinc-900/40 border-b border-zinc-800/80 flex flex-wrap items-center gap-2 flex-shrink-0 text-xs font-mono">
          {/* Category */}
          <div className="flex items-center gap-1">
            <span className="text-zinc-500 text-[11px]">Category:</span>
            <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-indigo-950/80 text-indigo-300 border border-indigo-800/60">
              {event.event_category || '—'}
            </span>
          </div>

          {/* Synthetic Badge */}
          {isDemo && (
            <span className="px-2 py-0.5 rounded text-[10px] font-mono font-semibold bg-amber-950/80 text-amber-300 border border-amber-800/60">
              DEMO_SYNTHETIC
            </span>
          )}

          {/* Stage */}
          <div className="flex items-center gap-1">
            <span className="text-zinc-500 text-[11px]">Stage:</span>
            <span className="px-2 py-0.5 rounded text-[10px] bg-zinc-800 text-zinc-300 border border-zinc-700">
              {stageDisplay || '—'}
            </span>
          </div>

          {/* Status */}
          <div className="flex items-center gap-1">
            <span className="text-zinc-500 text-[11px]">Execution Status:</span>
            <AuditStatusBadge status={resolvedStatus} />
          </div>

          {/* Progress */}
          <div className="flex items-center gap-1">
            <span className="text-zinc-500 text-[11px]">Progress:</span>
            {isJob && progressVal !== null ? (
              <span className="text-blue-400 font-bold text-[11px] font-mono">{progressVal}%</span>
            ) : (
              <span className="text-zinc-500 text-[11px] font-mono">Not applicable</span>
            )}
          </div>
        </div>

        {/* Vertically Scrollable Content Body */}
        <div className="flex-1 p-6 overflow-y-auto space-y-6 text-xs">
          {/* 1. Full Message Card */}
          <div className="space-y-1.5">
            <span className="text-[11px] font-mono font-semibold uppercase tracking-wider text-zinc-400">
              Event Message
            </span>
            <div className="p-3.5 rounded-xl bg-zinc-900/80 border border-zinc-800 text-zinc-200 text-sm leading-relaxed break-words font-sans">
              {event.message || '—'}
            </div>
          </div>

          {/* 2. Core Metadata Grid */}
          <div className="space-y-2">
            <span className="text-[11px] font-mono font-semibold uppercase tracking-wider text-zinc-400 flex items-center gap-1.5">
              <Layers className="w-3.5 h-3.5 text-blue-400" />
              Event Provenance & Identifiers
            </span>
            <div className="p-4 rounded-xl bg-zinc-900/60 border border-zinc-800/80 grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs font-mono">
              <div>
                <span className="text-zinc-500 text-[11px] block">Actor Name</span>
                <span className="text-zinc-200 font-medium break-all">{event.actor_name || (payload.actor_name as string) || '—'}</span>
              </div>
              <div>
                <span className="text-zinc-500 text-[11px] block">Actor Email</span>
                <span className="text-zinc-200 font-medium break-all">{event.actor_email || (payload.actor_email as string) || '—'}</span>
              </div>
              <div>
                <span className="text-zinc-500 text-[11px] block">Actor ID / Principal</span>
                <span className="text-zinc-200 font-medium break-all">{event.actor_user_id || (payload.actor_user_id as string) || event.actor || '—'}</span>
              </div>
              <div>
                <span className="text-zinc-500 text-[11px] block">Action</span>
                <span className="text-zinc-200 font-medium break-all">{event.action || '—'}</span>
              </div>
              <div>
                <span className="text-zinc-500 text-[11px] block">Entity Type</span>
                <span className="text-zinc-200 font-medium break-all">{event.entity_type || '—'}</span>
              </div>
              <div>
                <span className="text-zinc-500 text-[11px] block">Entity ID</span>
                <span className="text-zinc-200 font-medium break-all">{event.entity_id || '—'}</span>
              </div>
              <div>
                <span className="text-zinc-500 text-[11px] block">Job Association</span>
                {resolvedJobId ? (
                  <span className="text-zinc-200 font-medium font-mono break-all">{resolvedJobId}</span>
                ) : (
                  <span className="text-zinc-500 font-mono text-xs">Not job-based</span>
                )}
              </div>
              <div>
                <span className="text-zinc-500 text-[11px] block">Execution Status</span>
                <span className="text-zinc-200 font-medium font-mono">{resolvedStatus.label}</span>
              </div>
              <div>
                <span className="text-zinc-500 text-[11px] block">Progress</span>
                {isJob && progressVal !== null ? (
                  <span className="text-blue-400 font-bold font-mono">{progressVal}%</span>
                ) : (
                  <span className="text-zinc-500 font-mono text-xs">Not applicable</span>
                )}
              </div>
              <div>
                <span className="text-zinc-500 text-[11px] block">Storage Source</span>
                <div className="flex items-center gap-1.5 flex-wrap">
                  <span className="text-zinc-300 font-medium">BACKEND / DATABASE</span>
                  {isDemo && (
                    <span className="px-1.5 py-0.2 rounded text-[10px] font-mono font-semibold bg-amber-950/80 text-amber-300 border border-amber-800/60">
                      DEMO_SYNTHETIC
                    </span>
                  )}
                </div>
              </div>
              {Boolean(event.tender_id || payload.tender_id) && (
                <div>
                  <span className="text-zinc-500 text-[11px] block">Tender ID</span>
                  <span className="text-zinc-300 break-all">
                    {String(event.tender_id || payload.tender_id)}
                  </span>
                </div>
              )}
              {Boolean(event.bidder_id || payload.bidder_id) && (
                <div>
                  <span className="text-zinc-500 text-[11px] block">Bidder ID</span>
                  <span className="text-zinc-300 break-all">
                    {String(event.bidder_id || payload.bidder_id)}
                  </span>
                </div>
              )}
              {Boolean(event.run_id || payload.run_id || payload.compliance_run_id) && (
                <div>
                  <span className="text-zinc-500 text-[11px] block">Compliance Run ID</span>
                  <span className="text-zinc-300 break-all">
                    {String(event.run_id || payload.run_id || payload.compliance_run_id)}
                  </span>
                </div>
              )}
            </div>
          </div>

          {/* 3. Action Trace / Evidence Section */}
          {isClauseEval ? (
            <div className="space-y-2">
              <span className="text-[11px] font-mono font-semibold uppercase tracking-wider text-indigo-400 flex items-center gap-1.5">
                <Scale className="w-3.5 h-3.5 text-indigo-400" />
                Compliance Rule Evaluation Trace
              </span>
              <div className="p-4 rounded-xl bg-zinc-900/90 border border-indigo-900/50 space-y-4 shadow-inner">
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-zinc-800 pb-3">
                  <div>
                    <div className="text-sm font-mono font-bold text-white flex items-center gap-2">
                      <span>Clause {String(payload.clause_reference || event.clause_reference || 'General')}</span>
                      {Boolean(payload.requirement_type) && (
                        <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-zinc-800 text-zinc-300 border border-zinc-700">
                          {String(payload.requirement_type)}
                        </span>
                      )}
                    </div>
                    <p className="text-xs font-mono text-zinc-400 mt-0.5">
                      Field: <span className="text-indigo-300">{String(payload.field || 'criterion')}</span>
                    </p>
                  </div>

                  <span
                    className={`px-3 py-1 rounded text-xs font-mono font-bold uppercase border ${
                      payload.status === 'PASS' || payload.status === 'SATISFIED'
                        ? 'bg-emerald-950 text-emerald-300 border-emerald-700/80 shadow-sm'
                        : payload.status === 'FAIL' || payload.status === 'FAILED'
                        ? 'bg-rose-950 text-rose-300 border-rose-700/80 shadow-sm'
                        : 'bg-amber-950 text-amber-300 border-amber-700/80 shadow-sm'
                    }`}
                  >
                    {String(payload.status || 'EVALUATED')}
                  </span>
                </div>

                {/* Expected vs Observed Matrix */}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 font-mono text-xs">
                  <div className="p-3 rounded-lg bg-zinc-950/80 border border-zinc-800/80 space-y-1">
                    <span className="text-zinc-500 text-[11px] block">Expected Condition</span>
                    <p className="text-zinc-200 font-semibold break-words">
                      {formatExpectedCondition(payload.operator as string, payload.expected_value, {
                        unit: payload.unit as string,
                        requirementType: payload.requirement_type as string,
                        field: payload.field as string,
                      })}
                    </p>
                  </div>
                  <div className="p-3 rounded-lg bg-zinc-950/80 border border-zinc-800/80 space-y-1">
                    <span className="text-zinc-500 text-[11px] block">Observed Verification Value</span>
                    <p className="text-blue-300 font-semibold break-words">
                      {formatDisplayValue(payload.observed_value, { fallback: '—' })}
                    </p>
                  </div>
                </div>

                {isStructuredValue(payload.observed_value) && (
                  <div className="pt-2 border-t border-zinc-800/80">
                    <StructuredValueView value={payload.observed_value} label="Structured Observed Details" />
                  </div>
                )}

                {/* Reason Code */}
                {Boolean(payload.reason_code) && (
                  <div className="p-3 rounded-lg bg-zinc-950/60 border border-zinc-800/60 font-mono text-xs">
                    <span className="text-zinc-500 text-[11px] block mb-0.5">Engine Reason Code</span>
                    <span className="text-zinc-300 font-semibold">{String(payload.reason_code)}</span>
                  </div>
                )}

                {/* Evidence / Provenance IDs */}
                {Array.isArray(payload.evidence_ids) && payload.evidence_ids.length > 0 && (
                  <div className="p-3 rounded-lg bg-zinc-950/60 border border-zinc-800/60 font-mono text-xs space-y-1">
                    <span className="text-zinc-500 text-[11px] block">Associated Evidence IDs</span>
                    <div className="flex flex-wrap gap-1.5 pt-1">
                      {payload.evidence_ids.map((eid, idx) => (
                        <span
                          key={idx}
                          className="px-2 py-0.5 rounded bg-zinc-800 text-zinc-300 border border-zinc-700 text-[10px]"
                        >
                          {String(eid)}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {/* Quick Link Actions */}
                <div className="pt-2 flex flex-wrap items-center justify-end gap-3 border-t border-zinc-800/80">
                  {event.target_url && (
                    <Link
                      href={event.target_url}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white font-mono text-xs font-semibold shadow transition-colors"
                    >
                      <span>Inspect Rule in Matrix</span>
                      <ArrowUpRight className="w-3.5 h-3.5" />
                    </Link>
                  )}
                  {Boolean(payload.bidder_id) && (
                    <Link
                      href={`/workspace/bidders/${String(payload.bidder_id)}`}
                      className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-200 font-mono text-xs border border-zinc-700 transition-colors"
                    >
                      <span>View Bidder Binder</span>
                      <ExternalLink className="w-3 h-3" />
                    </Link>
                  )}
                </div>
              </div>
            </div>
          ) : isDecision ? (
            <div className="space-y-2">
              <span className="text-[11px] font-mono font-semibold uppercase tracking-wider text-emerald-400 flex items-center gap-1.5">
                <UserCheck className="w-3.5 h-3.5 text-emerald-400" />
                Human Review Officer Decision
              </span>
              <div className="p-4 rounded-xl bg-zinc-900/90 border border-emerald-900/50 space-y-3 shadow-inner">
                <div className="flex items-center justify-between gap-2 border-b border-zinc-800 pb-2">
                  <div className="font-mono font-bold text-white text-sm">
                    Decision: {String(payload.officer_decision || payload.status || 'DETERMINED')}
                  </div>
                  {Boolean(payload.decision_type) && (
                    <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-purple-950/80 text-purple-300 border border-purple-800/60">
                      {String(payload.decision_type)}
                    </span>
                  )}
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs font-mono">
                  <div>
                    <span className="text-zinc-500 text-[11px] block">Officer Name</span>
                    <span className="text-zinc-200 font-medium">
                      {String(payload.officer_name || event.actor || 'Officer')}
                    </span>
                  </div>
                  <div>
                    <span className="text-zinc-500 text-[11px] block">Officer Role</span>
                    <span className="text-zinc-300">
                      {String(payload.officer_role || 'PROCUREMENT_OFFICER')}
                    </span>
                  </div>
                </div>

                <div className="p-3 rounded-lg bg-zinc-950/80 border border-zinc-800 text-xs">
                  <span className="text-zinc-500 font-mono text-[11px] block mb-1">Remarks / Justification</span>
                  <p className="text-zinc-200 leading-relaxed break-words">
                    {String(payload.remarks || 'No remarks provided.')}
                  </p>
                </div>

                {event.target_url && (
                  <div className="pt-2 flex justify-end">
                    <Link
                      href={event.target_url}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-mono text-xs font-semibold shadow transition-colors"
                    >
                      <span>Open Human Review Workspace</span>
                      <ArrowUpRight className="w-3.5 h-3.5" />
                    </Link>
                  </div>
                )}
              </div>
            </div>
          ) : (
            /* Other Events Structured Summary */
            Object.keys(payload).length > 0 && (
              <div className="space-y-2">
                <span className="text-[11px] font-mono font-semibold uppercase tracking-wider text-zinc-400 flex items-center gap-1.5">
                  <Activity className="w-3.5 h-3.5 text-zinc-400" />
                  Action Trace Details
                </span>
                <div className="p-4 rounded-xl bg-zinc-900/60 border border-zinc-800/80 grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs font-mono">
                  {Object.entries(payload).map(([k, v]) => {
                    if (k === 'message' || typeof v === 'object') return null;
                    const isSecret = isSensitiveKey(k);
                    return (
                      <div key={k}>
                        <span className="text-zinc-500 text-[11px] block">{k}</span>
                        <span className="text-zinc-200 font-medium break-all">
                          {isSecret ? '[REDACTED]' : String(v ?? '—')}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            )
          )}

          {/* 4. Complete Raw Event Payload (Auditor Inspection) */}
          <div className="space-y-2 pt-2 border-t border-zinc-800">
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-mono font-semibold uppercase tracking-wider text-zinc-400 flex items-center gap-1.5">
                <Hash className="w-3.5 h-3.5 text-zinc-500" />
                Raw Event Payload (Auditor Ledger)
              </span>
              <button
                onClick={copyPayload}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 hover:text-white text-[11px] font-mono border border-zinc-700 transition-colors cursor-pointer"
              >
                {copied ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3 text-zinc-400" />}
                <span>{copied ? 'Copied' : 'Copy JSON'}</span>
              </button>
            </div>

            <div className="p-3.5 rounded-xl bg-zinc-950 border border-zinc-800 max-h-72 overflow-y-auto overflow-x-hidden">
              <pre className="font-mono text-[11px] text-zinc-300 whitespace-pre-wrap break-all leading-relaxed">
                {JSON.stringify(
                  redactSensitiveData({
                    id: event.id,
                    timestamp: event.timestamp,
                    mode: event.mode,
                    event_category: event.event_category,
                    pipeline_stage: stageDisplay,
                    job_id: event.job_id,
                    status: event.status,
                    progress: event.progress,
                    action: event.action,
                    entity_type: event.entity_type,
                    entity_id: event.entity_id,
                    actor: event.actor,
                    target_url: event.target_url,
                    ...payload,
                  }),
                  null,
                  2
                )}
              </pre>
            </div>
          </div>
        </div>

        {/* Drawer Footer Actions */}
        <div className="p-4 border-t border-zinc-800 bg-zinc-900/80 flex items-center justify-between gap-3 flex-shrink-0">
          <div className="text-[11px] font-mono text-zinc-500">
            ARGUS Immutable Audit Record
          </div>
          <div className="flex items-center gap-2">
            {event.target_url && (
              <Link
                href={event.target_url}
                className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-mono text-xs font-semibold shadow transition-colors"
              >
                <span>View Resource</span>
                <ExternalLink className="w-3 h-3" />
              </Link>
            )}
            <button
              onClick={onClose}
              className="px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 hover:text-white font-mono text-xs border border-zinc-700 transition-colors cursor-pointer"
            >
              Close
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
