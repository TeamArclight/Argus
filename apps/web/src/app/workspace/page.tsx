'use client';

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import {
  FileText,
  Users,
  CheckCircle2,
  AlertTriangle,
  ArrowRight,
  Plus,
  RefreshCw,
  Activity,
} from 'lucide-react';
import { apiClient } from '@/services/api';
import { demoStore } from '@/services/demo-store';
import {
  MOCK_PROVIDERS,
} from '@/services/mock-data';
import type { ProviderHealthRead, TenderCreate, TenderRead } from '@/types/api';
import { LoadingState } from '@/components/ui/LoadingState';
import { ErrorState } from '@/components/ui/ErrorState';
import { StatusBadge } from '@/components/ui/StatusBadge';
import { TenderCreateModal } from '@/components/ui/TenderCreateModal';
import { SessionRequired } from '@/components/ui/SessionRequired';
import { useAuth } from '@/hooks/useAuth';

export default function WorkspaceDashboard() {
  const { isAuthenticated, isDemoPreview, enableDemoPreview } = useAuth();

  const [tenders, setTenders] = useState<TenderRead[]>([]);
  const [demoStats, setDemoStats] = useState({
    activeTenders: 0,
    qualifiedBidders: 0,
    disqualifiedBidders: 0,
    pendingReviewBidders: 0,
  });
  const [providers, setProviders] = useState<ProviderHealthRead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [isCreating, setIsCreating] = useState(false);

  const loadDashboardData = async () => {
    try {
      setLoading(true);
      setError(null);

      if (isDemoPreview) {
        const demoTenders = demoStore.getTenders();
        const stats = demoStore.getDashboardStats();
        setTenders(demoTenders);
        setDemoStats(stats);
        setProviders(MOCK_PROVIDERS);
        setLoading(false);
        return;
      }

      if (!isAuthenticated) {
        setLoading(false);
        return;
      }

      // Live mode
      const tenderList = await apiClient.getTenders();
      let providerList: ProviderHealthRead[] = [];
      try {
        providerList = await apiClient.getProviders();
      } catch {
        providerList = [];
      }

      setTenders(tenderList);
      setProviders(providerList);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to load procurement overview.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadDashboardData();
  }, [isDemoPreview, isAuthenticated]);

  const handleCreateTender = async (data: TenderCreate, rfpFile?: File) => {
    try {
      setIsCreating(true);

      if (isDemoPreview) {
        demoStore.createTender(data, rfpFile);
        await loadDashboardData();
        setCreateModalOpen(false);
        return;
      }

      // Live API call: do NOT fake success on failure
      const newTender = await apiClient.createTender(data);
      if (rfpFile) {
        await apiClient.uploadTenderDocument(newTender.id, rfpFile, 'TENDER');
        await apiClient.processTender(newTender.id);
      }

      setTenders((prev) => [newTender, ...prev]);
      setCreateModalOpen(false);
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : 'Failed to create tender on backend.');
    } finally {
      setIsCreating(false);
    }
  };

  if (!isAuthenticated && !isDemoPreview) {
    return (
      <SessionRequired
        title="Session Required"
        description="To inspect live procurement tenders and evaluate bidder binders with the live FastAPI backend, connect an authorized Bearer token. Alternatively, open the Demo Workspace to explore the complete deterministic verification pipeline with synthetic data."
      />
    );
  }

  if (loading) {
    return <LoadingState message="Loading procurement telemetry..." />;
  }

  if (error) {
    return (
      <div className="space-y-4 max-w-2xl mx-auto py-12">
        <ErrorState
          title="Backend Query Exception"
          message={error}
          actionLabel="Retry Live Connection"
          onAction={loadDashboardData}
        />
        <div className="text-center">
          <button
            onClick={() => enableDemoPreview(true)}
            className="text-xs text-indigo-400 hover:text-indigo-300 underline font-mono"
          >
            Or open synthetic demo workspace
          </button>
        </div>
      </div>
    );
  }

  const qualifiedCount = isDemoPreview ? demoStats.qualifiedBidders : 0;
  const disqualifiedCount = isDemoPreview ? demoStats.disqualifiedBidders : 0;
  const pendingCount = isDemoPreview ? demoStats.pendingReviewBidders : 0;

  return (
    <div className="space-y-8 max-w-7xl mx-auto pb-12">
      {/* Top Banner */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 border-b border-slate-800 pb-6">
        <div>
          <div className="flex items-center gap-2">
            <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-indigo-950 border border-indigo-800/60 text-indigo-300">
              PROCUREMENT WORKSPACE
            </span>
            {isDemoPreview ? (
              <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-amber-950 border border-amber-800/60 text-amber-300">
                SYNTHETIC DEMO DATA
              </span>
            ) : isAuthenticated ? (
              <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-emerald-950 border border-emerald-800/60 text-emerald-300">
                AUTHENTICATED SESSION
              </span>
            ) : null}
          </div>
          <h1 className="text-2xl font-bold text-white tracking-tight mt-1">
            Executive Procurement Overview
          </h1>
          <p className="text-xs text-slate-400 font-mono mt-0.5">
            Deterministic rule evaluation, evidence verification, and procurement governance
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={loadDashboardData}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-800 bg-slate-900 text-xs text-slate-300 hover:text-white hover:bg-slate-800 transition-colors font-mono"
          >
            <RefreshCw className="w-3.5 h-3.5" /> Refresh
          </button>
          <button
            onClick={() => setCreateModalOpen(true)}
            className="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs text-white font-medium shadow-sm shadow-indigo-500/20 transition-colors"
          >
            <Plus className="w-4 h-4" /> New Tender
          </button>
        </div>
      </div>

      {/* KPI Stats Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="p-5 rounded-2xl bg-slate-900 border border-slate-800 flex flex-col justify-between">
          <div className="flex items-center justify-between text-slate-400">
            <span className="text-xs font-semibold font-mono uppercase tracking-wider">Active Tenders</span>
            <FileText className="w-4 h-4 text-indigo-400" />
          </div>
          <div className="mt-3">
            <span className="text-3xl font-bold text-white font-mono">{tenders.length}</span>
            <p className="text-[11px] text-slate-500 mt-1 font-mono">Managed tenders in workspace</p>
          </div>
        </div>

        <div className="p-5 rounded-2xl bg-slate-900 border border-slate-800 flex flex-col justify-between">
          <div className="flex items-center justify-between text-emerald-400">
            <span className="text-xs font-semibold font-mono uppercase tracking-wider text-slate-400">
              Qualified Bidders
            </span>
            <CheckCircle2 className="w-4 h-4" />
          </div>
          <div className="mt-3">
            <span className="text-3xl font-bold text-emerald-400 font-mono">{qualifiedCount}</span>
            <p className="text-[11px] text-slate-500 mt-1 font-mono">Satisfied mandatory criteria</p>
          </div>
        </div>

        <div className="p-5 rounded-2xl bg-slate-900 border border-slate-800 flex flex-col justify-between">
          <div className="flex items-center justify-between text-rose-400">
            <span className="text-xs font-semibold font-mono uppercase tracking-wider text-slate-400">
              Disqualified
            </span>
            <AlertTriangle className="w-4 h-4" />
          </div>
          <div className="mt-3">
            <span className="text-3xl font-bold text-rose-400 font-mono">{disqualifiedCount}</span>
            <p className="text-[11px] text-slate-500 mt-1 font-mono">Non-compliant criteria</p>
          </div>
        </div>

        <div className="p-5 rounded-2xl bg-slate-900 border border-slate-800 flex flex-col justify-between">
          <div className="flex items-center justify-between text-amber-400">
            <span className="text-xs font-semibold font-mono uppercase tracking-wider text-slate-400">
              Pending Review
            </span>
            <Users className="w-4 h-4" />
          </div>
          <div className="mt-3">
            <span className="text-3xl font-bold text-amber-400 font-mono">{pendingCount}</span>
            <p className="text-[11px] text-slate-500 mt-1 font-mono">Awaiting officer decision</p>
          </div>
        </div>
      </div>

      {/* Active Tenders Section */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-bold text-white tracking-tight flex items-center gap-2">
            <FileText className="w-4 h-4 text-indigo-400" />
            Active Procurement Tenders
          </h2>
          <Link
            href="/workspace/tenders"
            className="text-xs font-mono text-indigo-400 hover:text-indigo-300 flex items-center gap-1 transition-colors"
          >
            View All Tenders <ArrowRight className="w-3.5 h-3.5" />
          </Link>
        </div>

        {tenders.length === 0 ? (
          <div className="p-8 rounded-2xl border border-slate-800 bg-slate-900/40 text-center font-mono text-xs text-slate-400">
            No procurement tenders currently registered. Click 'New Tender' to create one.
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {tenders.slice(0, 3).map((tender) => (
              <Link
                key={tender.id}
                href={`/workspace/tenders/${tender.id}`}
                className="p-5 rounded-2xl bg-slate-900 border border-slate-800 hover:border-indigo-500/50 hover:bg-slate-900/80 transition-all space-y-3 group"
              >
                <div className="flex items-start justify-between gap-2">
                  <span className="font-mono text-xs text-indigo-400 font-semibold group-hover:text-indigo-300">
                    {tender.tender_number}
                  </span>
                  <StatusBadge status={tender.status} size="sm" />
                </div>

                <h3 className="text-sm font-semibold text-white line-clamp-2 leading-snug">
                  {tender.title}
                </h3>

                <div className="pt-3 border-t border-slate-800/80 flex items-center justify-between text-[11px] font-mono text-slate-500">
                  <span>Authority: {tender.authority || 'N/A'}</span>
                  <span className="text-indigo-400 font-semibold flex items-center gap-1">
                    Manage <ArrowRight className="w-3 h-3" />
                  </span>
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>

      {/* Provider Health Section */}
      <div className="p-6 rounded-2xl bg-slate-900 border border-slate-800 space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Activity className="w-4 h-4 text-indigo-400" />
            <h3 className="text-sm font-bold text-white tracking-tight">
              Backend Integration Status
            </h3>
          </div>
          <Link
            href="/workspace/status"
            className="text-xs font-mono text-indigo-400 hover:text-indigo-300 flex items-center gap-1"
          >
            Telemetry Details <ArrowRight className="w-3 h-3" />
          </Link>
        </div>

        {providers.length === 0 ? (
          <div className="p-4 rounded-xl bg-slate-950/40 border border-slate-800 text-xs font-mono text-slate-500">
            Provider telemetry is queried live from the backend API.
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {providers.map((p, idx) => (
              <div
                key={idx}
                className="p-3.5 rounded-xl bg-slate-950/60 border border-slate-800 space-y-1.5"
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono text-xs font-semibold text-slate-200">{p.provider_identifier}</span>
                  <span
                    className={`w-2 h-2 rounded-full ${
                      p.operational_health === 'AVAILABLE' ? 'bg-emerald-400 animate-pulse' : 'bg-rose-400'
                    }`}
                  />
                </div>
                <p className="text-[11px] text-slate-400 leading-tight font-mono">{p.notes || p.operational_health}</p>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Tender Creation Modal */}
      <TenderCreateModal
        isOpen={createModalOpen}
        onClose={() => setCreateModalOpen(false)}
        onSubmit={handleCreateTender}
        isSubmitting={isCreating}
      />
    </div>
  );
}
