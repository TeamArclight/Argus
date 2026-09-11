'use client';

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import {
  FileText,
  Plus,
  Search,
  RefreshCw,
  Calendar,
  ArrowRight,
} from 'lucide-react';
import { apiClient } from '@/services/api';
import { MOCK_TENDERS } from '@/services/mock-data';
import type { TenderCreate, TenderRead } from '@/types/api';
import { LoadingState } from '@/components/ui/LoadingState';
import { ErrorState } from '@/components/ui/ErrorState';
import { EmptyState } from '@/components/ui/EmptyState';
import { StatusBadge } from '@/components/ui/StatusBadge';
import { TenderCreateModal } from '@/components/ui/TenderCreateModal';
import { SessionRequired } from '@/components/ui/SessionRequired';
import { useAuth } from '@/hooks/useAuth';

export default function TendersListPage() {
  const { isAuthenticated, isDemoPreview, enableDemoPreview } = useAuth();

  const [tenders, setTenders] = useState<TenderRead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');

  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [isCreating, setIsCreating] = useState(false);

  const loadTenders = async () => {
    try {
      setLoading(true);
      setError(null);

      if (isDemoPreview) {
        setTenders(MOCK_TENDERS);
        setLoading(false);
        return;
      }

      if (!isAuthenticated) {
        setLoading(false);
        return;
      }

      const data = await apiClient.getTenders();
      setTenders(data);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to load tenders');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadTenders();
  }, [isDemoPreview, isAuthenticated]);

  const handleCreateTender = async (data: TenderCreate, rfpFile?: File) => {
    try {
      setIsCreating(true);

      if (isDemoPreview) {
        const syntheticTender: TenderRead = {
          id: `tender_demo_${Date.now()}`,
          tender_number: data.tender_number,
          title: data.title,
          category: data.category || null,
          authority: data.authority || null,
          budget: data.budget || null,
          deadline: data.deadline || null,
          status: 'COMPLETED',
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        };
        setTenders((prev) => [syntheticTender, ...prev]);
        setCreateModalOpen(false);
        return;
      }

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

  const filteredTenders = tenders.filter(
    (t) =>
      t.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
      t.tender_number.toLowerCase().includes(searchQuery.toLowerCase()) ||
      (t.authority && t.authority.toLowerCase().includes(searchQuery.toLowerCase()))
  );

  if (!isAuthenticated && !isDemoPreview) {
    return (
      <SessionRequired
        title="Session Required"
        description="To view and manage live procurement tenders, connect an authorized Bearer token or explore in the Demo Workspace."
      />
    );
  }

  return (
    <div className="space-y-6 max-w-7xl mx-auto pb-12">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 border-b border-slate-800 pb-4">
        <div>
          <div className="flex items-center gap-2">
            <FileText className="w-6 h-6 text-indigo-400" />
            <h1 className="text-2xl font-bold text-white tracking-tight">
              Procurement Tenders
            </h1>
          </div>
          <p className="text-xs text-slate-400 mt-1 font-mono">
            Manage RFP notices, deterministic requirement rules, and bidder evaluations
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={loadTenders}
            disabled={loading}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-800 bg-slate-900 text-xs text-slate-300 hover:text-white hover:bg-slate-800 transition-colors font-mono"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} /> Refresh
          </button>
          <button
            onClick={() => setCreateModalOpen(true)}
            className="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs text-white font-medium shadow-sm shadow-indigo-500/20 transition-colors"
          >
            <Plus className="w-4 h-4" /> New Tender
          </button>
        </div>
      </div>

      {/* Search Bar */}
      <div className="relative max-w-md">
        <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
        <input
          type="text"
          placeholder="Search by tender reference or title..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="w-full pl-9 pr-4 py-2 bg-slate-900 border border-slate-800 rounded-lg text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500 transition-colors font-mono"
        />
      </div>

      {/* Tenders Grid */}
      {loading ? (
        <LoadingState message="Loading procurement tenders..." />
      ) : error ? (
        <div className="space-y-4 max-w-2xl mx-auto py-8">
          <ErrorState
            title="Tender Catalog Query Error"
            message={error}
            actionLabel="Retry"
            onAction={loadTenders}
          />
          <div className="text-center">
            <button
              onClick={() => enableDemoPreview(true)}
              className="text-xs text-indigo-400 hover:text-indigo-300 underline font-mono"
            >
              Or view offline demo fixtures
            </button>
          </div>
        </div>
      ) : filteredTenders.length === 0 ? (
        <EmptyState
          title="No Procurement Tenders Found"
          description={searchQuery ? 'No tenders match your search criteria.' : 'No procurement tenders have been registered yet.'}
          icon={FileText}
          actionLabel="Create First Tender"
          onAction={() => setCreateModalOpen(true)}
        />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
          {filteredTenders.map((tender) => (
            <Link
              key={tender.id}
              href={`/workspace/tenders/${tender.id}`}
              className="p-6 rounded-2xl bg-slate-900 border border-slate-800 hover:border-indigo-500/50 hover:bg-slate-900/80 transition-all flex flex-col justify-between space-y-4 group"
            >
              <div className="space-y-3">
                <div className="flex items-start justify-between gap-2">
                  <span className="font-mono text-xs text-indigo-400 font-semibold group-hover:text-indigo-300">
                    {tender.tender_number}
                  </span>
                  <StatusBadge status={tender.status} size="sm" />
                </div>

                <h3 className="text-base font-semibold text-white line-clamp-2 leading-snug">
                  {tender.title}
                </h3>

                <p className="text-xs text-slate-400 font-mono">
                  Authority: {tender.authority || 'Procurement Authority'}
                </p>
              </div>

              <div className="pt-4 border-t border-slate-800 flex items-center justify-between text-xs font-mono text-slate-500">
                <div className="flex items-center gap-1.5">
                  <Calendar className="w-3.5 h-3.5" />
                  <span>{tender.deadline ? new Date(tender.deadline).toLocaleDateString() : 'N/A'}</span>
                </div>

                <span className="text-indigo-400 font-semibold flex items-center gap-1 group-hover:translate-x-0.5 transition-transform">
                  Enter Tender <ArrowRight className="w-3.5 h-3.5" />
                </span>
              </div>
            </Link>
          ))}
        </div>
      )}

      {/* Creation Modal */}
      <TenderCreateModal
        isOpen={createModalOpen}
        onClose={() => setCreateModalOpen(false)}
        onSubmit={handleCreateTender}
        isSubmitting={isCreating}
      />
    </div>
  );
}
