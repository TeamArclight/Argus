'use client';

import React, { useState, useEffect, useCallback } from 'react';
import Link from 'next/link';
import {
  ShieldCheck,
  RefreshCw,
  Filter,
  Search,
  AlertCircle,
  ExternalLink,
  Database,
  Sparkles,
  ArrowUpRight,
} from 'lucide-react';
import { api } from '@/services/api';
import type { AuditEventRead } from '@/services/types';
import { SessionRequired } from '@/components/ui/SessionRequired';
import { useAuth } from '@/hooks/useAuth';
import { AuditEventDetailDrawer } from '@/components/ui/AuditEventDetailDrawer';
import {
  getResolvedJobId,
  isJobBackedEvent,
  getResolvedProgress,
  resolveAuditStatus,
  AuditStatusBadge,
  formatAuditDate,
} from '@/lib/audit-helpers';
import { formatDisplayValue, formatExpectedCondition } from '@/lib/formatters';

export default function AuditPage() {
  const { isAuthenticated, isDemoPreview } = useAuth();
  const [events, setEvents] = useState<AuditEventRead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Selected event for detail drawer
  const [selectedEvent, setSelectedEvent] = useState<AuditEventRead | null>(null);

  // Filters
  const [searchTerm, setSearchTerm] = useState('');
  const [modeFilter, setModeFilter] = useState<'ALL' | 'AUTHENTIC' | 'DEMO'>('ALL');
  const [categoryFilter, setCategoryFilter] = useState('ALL');
  const [stageFilter, setStageFilter] = useState('ALL');
  const [statusFilter, setStatusFilter] = useState('ALL');

  const loadAuditEvents = useCallback(async (options?: { silent?: boolean }) => {
    if (!options?.silent) {
      setLoading(true);
    }
    setError(null);

    if (!isAuthenticated && !isDemoPreview) {
      setLoading(false);
      return;
    }

    try {
      const data = await api.getAuditEvents({ limit: 200 });
      setEvents(data);
    } catch (err: unknown) {
      if (!options?.silent) {
        setError(err instanceof Error ? err.message : 'Failed to load authoritative audit event log.');
      }
    } finally {
      if (!options?.silent) {
        setLoading(false);
      }
    }
  }, [isDemoPreview, isAuthenticated]);

  useEffect(() => {
    loadAuditEvents();
    const interval = setInterval(() => {
      loadAuditEvents({ silent: true });
    }, 3000);
    return () => clearInterval(interval);
  }, [loadAuditEvents]);

  const filteredEvents = events.filter((ev) => {
    const evMode = ev.mode || (isDemoPreview ? 'DEMO' : 'AUTHENTIC');
    if (modeFilter !== 'ALL' && evMode !== modeFilter) return false;
    if (categoryFilter !== 'ALL' && ev.event_category !== categoryFilter) return false;
    if (stageFilter !== 'ALL') {
      const evStage = ev.pipeline_stage || ev.stage;
      if (evStage !== stageFilter) return false;
    }
    if (statusFilter !== 'ALL') {
      const s = (ev.status || '').toUpperCase();
      if (statusFilter === 'SUCCESS' && s !== 'SUCCESS' && s !== 'COMPLETED') return false;
      if (statusFilter === 'FAILED' && s !== 'FAILED' && s !== 'ERROR') return false;
      if (statusFilter === 'RUNNING' && s !== 'RUNNING') return false;
    }
    if (searchTerm) {
      const q = searchTerm.toLowerCase();
      const msgMatch = ev.message?.toLowerCase().includes(q);
      const stageMatch = (ev.pipeline_stage || ev.stage)?.toLowerCase().includes(q);
      const jobMatch = ev.job_id?.toLowerCase().includes(q);
      const actionMatch = ev.action?.toLowerCase().includes(q);
      const entityMatch = ev.entity_id?.toLowerCase().includes(q) || ev.entity_type?.toLowerCase().includes(q);
      const actorMatch = ev.actor?.toLowerCase().includes(q);
      const actorNameMatch = ev.actor_name?.toLowerCase().includes(q);
      const actorEmailMatch = ev.actor_email?.toLowerCase().includes(q);
      const clauseMatch = ev.clause_reference?.toLowerCase().includes(q);
      if (!msgMatch && !stageMatch && !jobMatch && !actionMatch && !entityMatch && !actorMatch && !actorNameMatch && !actorEmailMatch && !clauseMatch) return false;
    }
    return true;
  });

  const formatTimestamp = (ts?: string | null) => {
    return formatAuditDate(ts);
  };

  const getModeBadge = (mode?: string, source?: string) => {
    const isDemo = mode === 'DEMO';
    return (
      <span
        className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-mono font-semibold border ${
          isDemo
            ? 'bg-amber-950/60 text-amber-300 border-amber-800/60'
            : 'bg-emerald-950/60 text-emerald-300 border-emerald-800/60'
        }`}
        title={`Source: ${source || 'BACKEND / DATABASE'}`}
      >
        {isDemo ? <Sparkles className="w-2.5 h-2.5 text-amber-400" /> : <Database className="w-2.5 h-2.5 text-emerald-400" />}
        {isDemo ? 'DEMO' : 'AUTHENTIC'}
      </span>
    );
  };

  const getCategoryBadge = (category?: string) => {
    switch (category) {
      case 'PROVIDER_HEALTH':
        return <span className="px-1.5 py-0.5 rounded text-[10px] font-mono font-bold bg-amber-950/80 text-amber-300 border border-amber-800/60">PROVIDER</span>;
      case 'COMPLIANCE':
        return <span className="px-1.5 py-0.5 rounded text-[10px] font-mono font-bold bg-indigo-950/80 text-indigo-300 border border-indigo-800/60">COMPLIANCE</span>;
      case 'PIPELINE':
        return <span className="px-1.5 py-0.5 rounded text-[10px] font-mono font-bold bg-blue-950/80 text-blue-300 border border-blue-800/60">PIPELINE</span>;
      case 'HUMAN_DECISION':
        return <span className="px-1.5 py-0.5 rounded text-[10px] font-mono font-bold bg-emerald-950/80 text-emerald-300 border border-emerald-800/60">DECISION</span>;
      case 'TENDER':
        return <span className="px-1.5 py-0.5 rounded text-[10px] font-mono font-bold bg-purple-950/80 text-purple-300 border border-purple-800/60">TENDER</span>;
      case 'BIDDER':
        return <span className="px-1.5 py-0.5 rounded text-[10px] font-mono font-bold bg-cyan-950/80 text-cyan-300 border border-cyan-800/60">BIDDER</span>;
      case 'AUTH':
        return <span className="px-1.5 py-0.5 rounded text-[10px] font-mono font-bold bg-rose-950/80 text-rose-300 border border-rose-800/60">AUTH</span>;
      default:
        return <span className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-zinc-800 text-zinc-400 border border-zinc-700">{category || 'EVENT'}</span>;
    }
  };

  if (!isAuthenticated && !isDemoPreview) {
    return (
      <SessionRequired
        title="Session Required"
        description="To inspect the immutable audit ledger and telemetry logs from the live FastAPI backend, connect an authorized Bearer token or explore in the Demo Workspace."
      />
    );
  }

  return (
    <div className="space-y-6">
      {/* Top Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-zinc-100 flex items-center gap-2.5">
            <ShieldCheck className="w-6 h-6 text-blue-400" />
            Authoritative Audit Log
          </h1>
          <p className="text-sm text-zinc-400 mt-1">
            Immutable pipeline event streams, OCR extractions, and verification telemetry.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 px-2.5 py-1 rounded-full bg-emerald-950/60 border border-emerald-800/40 text-emerald-400 text-xs font-mono font-medium">
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
            LIVE (3s)
          </div>
          <button
            onClick={() => loadAuditEvents()}
            className="inline-flex items-center gap-2 px-3.5 py-2 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-sm font-medium rounded-lg transition-colors border border-zinc-700 cursor-pointer"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} /> Refresh Audit Trail
          </button>
        </div>
      </div>

      {error && (
        <div className="p-4 rounded-xl bg-rose-950/20 border border-rose-800/40 text-rose-300 text-sm flex items-center gap-3">
          <AlertCircle className="w-5 h-5 text-rose-400 flex-shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Filter & Search Bar */}
      <div className="flex flex-wrap items-center justify-between gap-4 p-4 rounded-xl bg-zinc-900/60 border border-zinc-800/80">
        <div className="flex flex-wrap items-center gap-3 flex-1 min-w-[280px]">
          <div className="relative flex-1 min-w-[200px]">
            <Search className="w-4 h-4 absolute left-3 top-2.5 text-zinc-500" />
            <input
              type="text"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder="Search audit events, clauses, or Job IDs..."
              className="w-full pl-9 pr-3 py-1.5 rounded-lg bg-zinc-950 border border-zinc-800 text-xs text-zinc-200 focus:outline-none focus:border-blue-500 font-mono"
            />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Filter className="w-3.5 h-3.5 text-zinc-400" />
            <select
              value={modeFilter}
              onChange={(e) => setModeFilter(e.target.value as 'ALL' | 'AUTHENTIC' | 'DEMO')}
              className="px-2.5 py-1.5 rounded-lg bg-zinc-950 border border-zinc-800 text-xs text-zinc-200 focus:outline-none focus:border-blue-500 font-mono cursor-pointer"
            >
              <option value="ALL">All Modes</option>
              <option value="AUTHENTIC">Authentic Mode</option>
              <option value="DEMO">Demo Preview</option>
            </select>
            <select
              value={categoryFilter}
              onChange={(e) => setCategoryFilter(e.target.value)}
              className="px-2.5 py-1.5 rounded-lg bg-zinc-950 border border-zinc-800 text-xs text-zinc-200 focus:outline-none focus:border-blue-500 font-mono cursor-pointer"
            >
              <option value="ALL">All Categories</option>
              <option value="COMPLIANCE">Compliance Engine</option>
              <option value="PIPELINE">Pipeline Execution</option>
              <option value="PROVIDER_HEALTH">Provider Health</option>
              <option value="TENDER">Tender Governance</option>
              <option value="BIDDER">Bidder Management</option>
              <option value="DOCUMENT">Document Lifecycle</option>
              <option value="HUMAN_DECISION">Human Decisions</option>
              <option value="AUTH">Authentication</option>
              <option value="SYSTEM">System</option>
            </select>
            <select
              value={stageFilter}
              onChange={(e) => setStageFilter(e.target.value)}
              className="px-2.5 py-1.5 rounded-lg bg-zinc-950 border border-zinc-800 text-xs text-zinc-200 focus:outline-none focus:border-blue-500 font-mono cursor-pointer"
            >
              <option value="ALL">All Stages</option>
              <option value="UPLOAD">UPLOAD</option>
              <option value="PARSING">PARSING</option>
              <option value="OCR">OCR</option>
              <option value="EXTRACTION">EXTRACTION</option>
              <option value="VERIFICATION">VERIFICATION</option>
              <option value="COMPLIANCE">COMPLIANCE</option>
              <option value="RISK_ANALYSIS">RISK_ANALYSIS</option>
              <option value="REPORTING">REPORTING</option>
              <option value="SYSTEM / PROVIDER">SYSTEM / PROVIDER</option>
            </select>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="px-2.5 py-1.5 rounded-lg bg-zinc-950 border border-zinc-800 text-xs text-zinc-200 focus:outline-none focus:border-blue-500 font-mono cursor-pointer"
            >
              <option value="ALL">All Statuses</option>
              <option value="SUCCESS">SUCCESS / COMPLETED</option>
              <option value="RUNNING">RUNNING</option>
              <option value="FAILED">FAILED / ERROR</option>
            </select>
          </div>
        </div>
        <div className="text-xs text-zinc-400 font-mono">
          Showing <span className="font-semibold text-zinc-200">{filteredEvents.length}</span> events
        </div>
      </div>

      {/* Events Table */}
      {loading ? (
        <div className="flex items-center justify-center min-h-[40vh]">
          <div className="flex flex-col items-center gap-3">
            <RefreshCw className="w-6 h-6 text-blue-500 animate-spin" />
            <p className="text-zinc-400 text-xs font-mono">Streaming audit telemetry...</p>
          </div>
        </div>
      ) : filteredEvents.length === 0 ? (
        <div className="p-12 text-center rounded-xl bg-zinc-900/40 border border-zinc-800/80">
          <ShieldCheck className="w-12 h-12 text-zinc-600 mx-auto mb-3" />
          <h3 className="text-base font-semibold text-zinc-300">No Audit Events Logged</h3>
          <p className="text-sm text-zinc-500 mt-1">
            Actions performed across pipelines and reviews are streamed directly into this ledger.
          </p>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-zinc-800/80 bg-zinc-900/40 shadow-sm">
          <table className="w-full text-left text-sm table-fixed min-w-[1080px]">
            <colgroup>
              <col className="w-[130px]" />
              <col className="w-[160px]" />
              <col className="w-[180px]" />
              <col className="w-[135px]" />
              <col className="w-[105px]" />
              <col />
            </colgroup>
            <thead>
              <tr className="border-b border-zinc-800 text-xs text-zinc-400 font-semibold bg-zinc-950/60">
                <th className="px-3.5 py-2.5">Timestamp</th>
                <th className="px-3.5 py-2.5">Actor (WHO)</th>
                <th className="px-3.5 py-2.5">Action (WHAT)</th>
                <th className="px-3.5 py-2.5">Target</th>
                <th className="px-3.5 py-2.5">Status</th>
                <th className="px-3.5 py-2.5">Message &amp; Event Details</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/60">
              {filteredEvents.map((ev, i) => {
                const stageDisplay = ev.pipeline_stage || ev.stage;
                const isSelected = selectedEvent?.id === ev.id;
                const resolvedJobId = getResolvedJobId(ev);
                const isJob = isJobBackedEvent(ev);
                const progressVal = getResolvedProgress(ev);
                const resolvedStatus = resolveAuditStatus(ev);

                return (
                  <tr
                    key={ev.id || i}
                    onClick={() => setSelectedEvent(ev)}
                    className={`cursor-pointer transition-colors ${
                      isSelected
                        ? 'bg-zinc-800/70 ring-1 ring-inset ring-blue-500/50'
                        : 'hover:bg-zinc-800/40'
                    }`}
                  >
                    {/* Timestamp (WHEN) */}
                    <td className="px-3.5 py-2.5 whitespace-nowrap">
                      <div className="font-mono text-xs text-zinc-300">
                        {formatTimestamp(ev.timestamp)}
                      </div>
                      <div className="mt-1">
                        {getModeBadge(ev.mode, ev.source)}
                      </div>
                    </td>

                    {/* Actor (WHO) */}
                    <td className="px-3.5 py-2.5">
                      {ev.actor_name ? (
                        <div className="min-w-0">
                          <div className="font-medium text-xs text-zinc-200 truncate" title={ev.actor_name}>
                            {ev.actor_name}
                          </div>
                          <div className="text-[10px] text-zinc-500 font-mono truncate" title={ev.actor_email || ''}>
                            {ev.actor_email || ev.actor_user_id || '—'}
                          </div>
                        </div>
                      ) : (
                        <div className="text-xs font-mono text-zinc-400 truncate" title={ev.actor || ev.actor_user_id || 'SYSTEM'}>
                          {ev.actor || ev.actor_user_id || 'SYSTEM'}
                        </div>
                      )}
                    </td>

                    {/* Action & Category (WHAT) */}
                    <td className="px-3.5 py-2.5">
                      <div className="flex items-center gap-1.5 flex-wrap mb-1">
                        {getCategoryBadge(ev.event_category)}
                        {stageDisplay && String(stageDisplay) !== '—' && (
                          <span className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-zinc-800 text-zinc-300 border border-zinc-700 truncate max-w-[95px]" title={String(stageDisplay)}>
                            {stageDisplay}
                          </span>
                        )}
                      </div>
                      <div className="font-mono text-xs font-semibold text-zinc-200 truncate" title={ev.action || undefined}>
                        {ev.action || '—'}
                      </div>
                    </td>

                    {/* Target */}
                    <td className="px-3.5 py-2.5 font-mono text-xs">
                      {ev.bidder_id ? (
                        <div className="space-y-0.5">
                          <span className="px-1 py-0.2 rounded text-[9px] font-mono bg-cyan-950/70 text-cyan-300 border border-cyan-800/60">BIDDER</span>
                          <div className="text-zinc-300 truncate max-w-[125px]" title={ev.bidder_id}>
                            {ev.bidder_id}
                          </div>
                        </div>
                      ) : resolvedJobId ? (
                        <div className="space-y-0.5">
                          <span className="px-1 py-0.2 rounded text-[9px] font-mono bg-blue-950/70 text-blue-300 border border-blue-800/60">JOB</span>
                          <div className="text-zinc-300 truncate max-w-[125px]" title={resolvedJobId}>
                            {resolvedJobId}
                          </div>
                        </div>
                      ) : ev.entity_id ? (
                        <div className="space-y-0.5">
                          <span className="px-1 py-0.2 rounded text-[9px] font-mono bg-zinc-800 text-zinc-400 border border-zinc-700">
                            {ev.entity_type || 'ENTITY'}
                          </span>
                          <div className="text-zinc-300 truncate max-w-[125px]" title={ev.entity_id}>
                            {ev.entity_id}
                          </div>
                        </div>
                      ) : (
                        <span className="text-zinc-600 font-mono text-[11px]">N/A</span>
                      )}
                    </td>

                    {/* Status */}
                    <td className="px-3.5 py-2.5 whitespace-nowrap">
                      <AuditStatusBadge status={resolvedStatus} />
                      {isJob && progressVal !== null && (
                        <div className="flex items-center gap-1.5 mt-1.5">
                          <div className="w-8 h-1 bg-zinc-800 rounded-full overflow-hidden flex-shrink-0">
                            <div
                              className={`h-full rounded-full transition-all ${
                                progressVal === 100
                                  ? 'bg-emerald-500'
                                  : resolvedStatus.variant === 'FAILED' || resolvedStatus.variant === 'FAIL'
                                  ? 'bg-rose-500'
                                  : 'bg-blue-500'
                              }`}
                              style={{ width: `${progressVal}%` }}
                            />
                          </div>
                          <span className="text-blue-400 text-[10px] font-mono font-semibold">
                            {progressVal}%
                          </span>
                        </div>
                      )}
                    </td>

                    {/* Message & Action Traces (Details) */}
                    <td className="px-3.5 py-2.5 text-xs text-zinc-300">
                      <AuditMessagePreview
                        event={ev}
                        onOpenDrawer={() => setSelectedEvent(ev)}
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Full Event Detail Drawer */}
      <AuditEventDetailDrawer
        isOpen={Boolean(selectedEvent)}
        onClose={() => setSelectedEvent(null)}
        event={selectedEvent}
      />
    </div>
  );
}

/**
 * Renders a compact preview (approx 2-3 lines) of the message and action traces
 * for an audit event, avoiding large height blowouts in table rows.
 */
function AuditMessagePreview({
  event,
  onOpenDrawer,
}: {
  event: AuditEventRead;
  onOpenDrawer: () => void;
}) {
  const payload = (event.payload_json || {}) as Record<string, unknown>;
  const isClauseEval = event.action === 'CLAUSE_EVALUATED' || Boolean(payload.clause_reference);
  const isDecision = event.action === 'HUMAN_DECISION_RECORDED' || Boolean(payload.officer_decision);
  const isComplianceCompleted =
    event.action === 'COMPLIANCE_EVALUATION_COMPLETED' ||
    event.action === 'COMPLIANCE_RUN_COMPLETED';

  // 1. Clause Evaluation Preview
  if (isClauseEval) {
    const clause = String(payload.clause_reference || event.clause_reference || 'Clause');
    const field = String(payload.field || 'criterion');
    const status = String(payload.status || 'EVALUATED').toUpperCase();
    const isPass = status === 'PASS' || status === 'SATISFIED';
    const isFail = status === 'FAIL' || status === 'FAILED';
    const exp = formatExpectedCondition(payload.operator as string, payload.expected_value, {
      unit: payload.unit as string,
      requirementType: payload.requirement_type as string,
      field: payload.field as string,
    });
    const obs = formatDisplayValue(payload.observed_value, { fallback: '—' });

    return (
      <div className="flex items-center justify-between gap-2.5 min-w-0">
        <div className="min-w-0 flex-1 space-y-0.5">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="font-mono font-semibold text-zinc-100 text-xs">
              Clause {clause}
            </span>
            <span className="text-zinc-500 font-mono text-[11px] truncate max-w-[180px]" title={field}>
              • {field}
            </span>
            <span
              className={`px-1.5 py-0.2 rounded text-[10px] font-mono font-bold uppercase border ${
                isPass
                  ? 'bg-emerald-950/80 text-emerald-300 border-emerald-800/60'
                  : isFail
                  ? 'bg-rose-950/80 text-rose-300 border-rose-800/60'
                  : 'bg-amber-950/80 text-amber-300 border-amber-800/60'
              }`}
            >
              {status}
            </span>
          </div>
          <div className="text-[11px] font-mono text-zinc-400 truncate" title={`Expected ${exp} • Observed ${obs}`}>
            <span className="text-zinc-500">Expected:</span> {exp}
            <span className="text-zinc-600 mx-1.5">•</span>
            <span className="text-zinc-500">Observed:</span>{' '}
            <span className={isFail ? 'text-rose-300 font-semibold' : 'text-zinc-300'}>{obs}</span>
          </div>
        </div>

        {/* Quick Actions */}
        <div className="flex items-center gap-1.5 flex-shrink-0">
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onOpenDrawer();
            }}
            className="px-2 py-0.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 hover:text-white font-mono text-[11px] border border-zinc-700 transition-colors cursor-pointer"
          >
            Details
          </button>
          {event.target_url && (
            <Link
              href={event.target_url}
              onClick={(e) => e.stopPropagation()}
              className="px-2 py-0.5 rounded bg-indigo-950/60 hover:bg-indigo-900/80 text-indigo-300 hover:text-indigo-200 font-mono text-[11px] border border-indigo-800/60 transition-colors inline-flex items-center gap-0.5"
            >
              <span>Matrix</span>
              <ArrowUpRight className="w-2.5 h-2.5" />
            </Link>
          )}
        </div>
      </div>
    );
  }

  // 2. Human Review Decision Preview
  if (isDecision) {
    const decision = String(payload.officer_decision || payload.status || 'DETERMINED').toUpperCase();
    const officer = String(payload.officer_name || event.actor || 'Officer');
    const remarks = String(payload.remarks || 'Human review decision recorded.');

    return (
      <div className="flex items-center justify-between gap-2.5 min-w-0">
        <div className="min-w-0 flex-1 space-y-0.5">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="font-mono font-semibold text-emerald-300 text-xs">
              Decision: {decision}
            </span>
            <span className="text-zinc-500 font-mono text-[11px]">
              • by {officer}
            </span>
          </div>
          <div className="text-[11px] text-zinc-400 truncate" title={remarks}>
            {remarks}
          </div>
        </div>

        <div className="flex items-center gap-1.5 flex-shrink-0">
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onOpenDrawer();
            }}
            className="px-2 py-0.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 hover:text-white font-mono text-[11px] border border-zinc-700 transition-colors cursor-pointer"
          >
            Details
          </button>
          {event.target_url && (
            <Link
              href={event.target_url}
              onClick={(e) => e.stopPropagation()}
              className="px-2 py-0.5 rounded bg-emerald-950/60 hover:bg-emerald-900/80 text-emerald-300 hover:text-emerald-200 font-mono text-[11px] border border-emerald-800/60 transition-colors inline-flex items-center gap-0.5"
            >
              <span>Review</span>
              <ArrowUpRight className="w-2.5 h-2.5" />
            </Link>
          )}
        </div>
      </div>
    );
  }

  // 3. Compliance Evaluation Completed Preview
  if (isComplianceCompleted) {
    const overall = String(payload.overall_status || 'COMPLETED').toUpperCase();
    const isPass = overall === 'PASS';
    const isFail = overall === 'FAIL';
    const rulesCount = payload.rules_count || payload.evaluations_count;

    return (
      <div className="flex items-center justify-between gap-2.5 min-w-0">
        <div className="min-w-0 flex-1 space-y-0.5">
          <div className="text-xs text-zinc-200 truncate">
            {event.message || 'Compliance evaluation completed.'}
          </div>
          <div className="text-[11px] font-mono text-zinc-400 flex items-center gap-1.5">
            <span className="text-zinc-500">Overall:</span>
            <span
              className={`font-bold ${
                isPass ? 'text-emerald-400' : isFail ? 'text-rose-400' : 'text-amber-400'
              }`}
            >
              {overall}
            </span>
            {Boolean(rulesCount) && (
              <>
                <span className="text-zinc-600">•</span>
                <span className="text-zinc-400">{String(rulesCount)} clauses evaluated</span>
              </>
            )}
          </div>
        </div>

        <div className="flex items-center gap-1.5 flex-shrink-0">
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onOpenDrawer();
            }}
            className="px-2 py-0.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 hover:text-white font-mono text-[11px] border border-zinc-700 transition-colors cursor-pointer"
          >
            Details
          </button>
          {event.target_url && (
            <Link
              href={event.target_url}
              onClick={(e) => e.stopPropagation()}
              className="px-2 py-0.5 rounded bg-blue-950/60 hover:bg-blue-900/80 text-blue-300 hover:text-blue-200 font-mono text-[11px] border border-blue-800/60 transition-colors inline-flex items-center gap-0.5"
            >
              <span>Resource</span>
              <ExternalLink className="w-2.5 h-2.5" />
            </Link>
          )}
        </div>
      </div>
    );
  }

  // 4. Default / Generic Event Preview (with line-clamp)
  return (
    <div className="flex items-center justify-between gap-2.5 min-w-0">
      <div className="min-w-0 flex-1 space-y-0.5">
        <div className="text-xs text-zinc-200 line-clamp-1 break-words" title={event.message || undefined}>
          {event.message || '—'}
        </div>
        {(event.action || event.entity_type) && (
          <div className="text-[11px] font-mono text-zinc-500 truncate">
            {event.action && <span className="text-zinc-400">{event.action}</span>}
            {event.entity_type && (
              <span>
                {' '}• {event.entity_type}:{event.entity_id || '—'}
              </span>
            )}
            {event.actor && <span> • actor:{event.actor}</span>}
          </div>
        )}
      </div>

      <div className="flex items-center gap-1.5 flex-shrink-0">
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onOpenDrawer();
          }}
          className="px-2 py-0.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 hover:text-white font-mono text-[11px] border border-zinc-700 transition-colors cursor-pointer"
        >
          Details
        </button>
        {event.target_url && (
          <Link
            href={event.target_url}
            onClick={(e) => e.stopPropagation()}
            className="px-2 py-0.5 rounded bg-blue-950/60 hover:bg-blue-900/80 text-blue-300 hover:text-blue-200 font-mono text-[11px] border border-blue-800/60 transition-colors inline-flex items-center gap-0.5"
          >
            <span>Resource</span>
            <ExternalLink className="w-2.5 h-2.5" />
          </Link>
        )}
      </div>
    </div>
  );
}
