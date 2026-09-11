"use client";

import React, { useState, useEffect } from "react";
import { 
  ShieldCheck, RefreshCw, Filter, Search, Clock, 
  CheckCircle, XCircle, AlertCircle
} from "lucide-react";
import { api } from "@/services/api";
import { JobEventRead } from "@/services/types";
import { MOCK_AUDIT_EVENTS } from "@/services/mock-data";
import { SessionRequired } from "@/components/ui/SessionRequired";
import { useAuth } from "@/hooks/useAuth";

export default function AuditPage() {
  const { isAuthenticated, isDemoPreview } = useAuth();
  const [events, setEvents] = useState<JobEventRead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [searchTerm, setSearchTerm] = useState("");
  const [stageFilter, setStageFilter] = useState("ALL");
  const [statusFilter, setStatusFilter] = useState("ALL");

  const loadAuditEvents = async () => {
    setLoading(true);
    setError(null);

    if (isDemoPreview) {
      setEvents(MOCK_AUDIT_EVENTS);
      setLoading(false);
      return;
    }

    if (!isAuthenticated) {
      setLoading(false);
      return;
    }

    try {
      const data = await api.getAuditEvents();
      setEvents(data);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load authoritative audit event log.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAuditEvents();
  }, [isDemoPreview, isAuthenticated]);

  const filteredEvents = events.filter((ev) => {
    if (stageFilter !== "ALL" && ev.stage !== stageFilter) return false;
    if (statusFilter !== "ALL" && ev.status !== statusFilter) return false;
    if (searchTerm) {
      const q = searchTerm.toLowerCase();
      const msgMatch = ev.message?.toLowerCase().includes(q);
      const stageMatch = ev.stage?.toLowerCase().includes(q);
      const jobMatch = ev.job_id?.toLowerCase().includes(q);
      if (!msgMatch && !stageMatch && !jobMatch) return false;
    }
    return true;
  });

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "COMPLETED":
      case "SUCCESS":
        return <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"><CheckCircle className="w-3 h-3" /> SUCCESS</span>;
      case "RUNNING":
        return <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-500/10 text-blue-400 border border-blue-500/20"><Clock className="w-3 h-3 animate-spin" /> RUNNING</span>;
      case "FAILED":
      case "ERROR":
        return <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-rose-500/10 text-rose-400 border border-rose-500/20"><XCircle className="w-3 h-3" /> FAILED</span>;
      default:
        return <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-zinc-800 text-zinc-400 border border-zinc-700">{status}</span>;
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
        <button
          onClick={loadAuditEvents}
          className="inline-flex items-center gap-2 px-3.5 py-2 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-sm font-medium rounded-lg transition-colors border border-zinc-700"
        >
          <RefreshCw className="w-4 h-4" /> Refresh Audit Trail
        </button>
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
              placeholder="Search audit messages or Job IDs..."
              className="w-full pl-9 pr-3 py-1.5 rounded-lg bg-zinc-950 border border-zinc-800 text-xs text-zinc-200 focus:outline-none focus:border-blue-500"
            />
          </div>
          <div className="flex items-center gap-2">
            <Filter className="w-3.5 h-3.5 text-zinc-400" />
            <select
              value={stageFilter}
              onChange={(e) => setStageFilter(e.target.value)}
              className="px-2.5 py-1.5 rounded-lg bg-zinc-950 border border-zinc-800 text-xs text-zinc-200 focus:outline-none focus:border-blue-500"
            >
              <option value="ALL">All Stages</option>
              <option value="OCR">OCR Extraction</option>
              <option value="VERIFICATION">Verification</option>
              <option value="COMPLIANCE">Compliance</option>
              <option value="AI_EXTRACTION">AI Extraction</option>
            </select>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="px-2.5 py-1.5 rounded-lg bg-zinc-950 border border-zinc-800 text-xs text-zinc-200 focus:outline-none focus:border-blue-500"
            >
              <option value="ALL">All Statuses</option>
              <option value="SUCCESS">SUCCESS</option>
              <option value="RUNNING">RUNNING</option>
              <option value="FAILED">FAILED</option>
            </select>
          </div>
        </div>
        <div className="text-xs text-zinc-400">
          Showing <span className="font-semibold text-zinc-200">{filteredEvents.length}</span> events
        </div>
      </div>

      {/* Events Table */}
      {loading ? (
        <div className="flex items-center justify-center min-h-[40vh]">
          <div className="flex flex-col items-center gap-3">
            <RefreshCw className="w-6 h-6 text-blue-500 animate-spin" />
            <p className="text-zinc-400 text-xs">Streaming audit telemetry...</p>
          </div>
        </div>
      ) : filteredEvents.length === 0 ? (
        <div className="p-12 text-center rounded-xl bg-zinc-900/40 border border-zinc-800/80">
          <ShieldCheck className="w-12 h-12 text-zinc-600 mx-auto mb-3" />
          <h3 className="text-base font-semibold text-zinc-300">No Audit Events Logged</h3>
          <p className="text-sm text-zinc-500 mt-1">Actions performed across pipelines and reviews are streamed directly into this ledger.</p>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-zinc-800/80 bg-zinc-900/40">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-zinc-800 text-xs text-zinc-400 font-semibold bg-zinc-950/40">
                <th className="px-5 py-3">Timestamp</th>
                <th className="px-5 py-3">Pipeline Stage</th>
                <th className="px-5 py-3">Job ID</th>
                <th className="px-5 py-3">Status</th>
                <th className="px-5 py-3">Progress</th>
                <th className="px-5 py-3">Message</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/60">
              {filteredEvents.map((ev, i) => (
                <tr key={ev.id || i} className="hover:bg-zinc-800/30 transition-colors">
                  <td className="px-5 py-3 font-mono text-xs text-zinc-400 whitespace-nowrap">
                    {new Date(ev.timestamp).toLocaleTimeString()}
                  </td>
                  <td className="px-5 py-3">
                    <span className="px-2 py-0.5 rounded text-xs font-mono bg-zinc-800 text-zinc-300 border border-zinc-700">
                      {ev.stage}
                    </span>
                  </td>
                  <td className="px-5 py-3 font-mono text-xs text-zinc-500 truncate max-w-[120px]">
                    {ev.job_id}
                  </td>
                  <td className="px-5 py-3">{getStatusBadge(ev.status)}</td>
                  <td className="px-5 py-3 font-mono text-xs text-blue-400">
                    {ev.progress != null ? `${ev.progress}%` : "—"}
                  </td>
                  <td className="px-5 py-3 text-xs text-zinc-300">
                    {ev.message}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
