'use client';

import React from 'react';
import { X, FileText, ShieldCheck } from 'lucide-react';
import type { ComplianceMatrixRow, EvidenceRead } from '@/types/api';
import { StatusBadge } from './StatusBadge';

interface EvidenceDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  row: ComplianceMatrixRow | null;
  evidence?: EvidenceRead | null;
  bidderName?: string;
}

export const EvidenceDrawer: React.FC<EvidenceDrawerProps> = ({
  isOpen,
  onClose,
  row,
  evidence,
  bidderName,
}) => {
  if (!isOpen || !row) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/70 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="w-full max-w-xl h-full bg-slate-900 border-l border-slate-800 flex flex-col shadow-2xl">
        {/* Header */}
        <div className="p-5 border-b border-slate-800 flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2">
              <ShieldCheck className="w-5 h-5 text-indigo-400" />
              <h3 className="text-sm font-bold text-white uppercase tracking-wider">
                Evidence Inspector
              </h3>
            </div>
            <p className="text-xs font-mono text-slate-400 mt-0.5">
              {bidderName ? `${bidderName} • ` : ''}{row.clause}
            </p>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg border border-slate-700 bg-slate-800 text-slate-400 hover:text-white transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 p-6 overflow-y-auto space-y-6 text-xs">
          {/* Rule Overview */}
          <div className="p-4 rounded-xl bg-slate-950 border border-slate-800 space-y-3">
            <div className="flex items-center justify-between">
              <span className="font-mono text-indigo-400 font-bold">{row.clause}</span>
              <StatusBadge status={row.status} size="sm" />
            </div>
            <p className="text-slate-300 font-mono">Field: {row.field}</p>
            <div className="grid grid-cols-2 gap-2 text-slate-400 pt-2 border-t border-slate-800 font-mono">
              <div>
                <p className="text-slate-500">Expected Value</p>
                <p className="text-slate-200">{String(row.expected_value)}</p>
              </div>
              <div>
                <p className="text-slate-500">Observed Value</p>
                <p className="text-slate-200">{String(row.observed_value ?? 'N/A')}</p>
              </div>
            </div>
          </div>

          {/* Engine Reason & Explanation */}
          <div className="space-y-2">
            <h4 className="font-semibold text-slate-300 uppercase tracking-wider text-[11px]">
              Deterministic Compliance Evaluation
            </h4>
            <div className="p-4 rounded-xl bg-slate-950/60 border border-slate-800 space-y-2">
              <p className="text-slate-300 font-mono">Reason Code: {row.reason_code}</p>
              <div className="flex items-center justify-between text-[11px] font-mono text-slate-500 pt-2 border-t border-slate-900">
                <span>Category: {row.requirement_type || 'GENERAL'}</span>
                <span>Review Required: {row.review_required ? 'YES' : 'NO'}</span>
              </div>
            </div>
          </div>

          {/* Evidence Snippet */}
          <div className="space-y-2">
            <h4 className="font-semibold text-slate-300 uppercase tracking-wider text-[11px]">
              Source Citations & Extracted Evidence
            </h4>

            {evidence ? (
              <div className="p-4 rounded-xl bg-slate-950/80 border border-slate-800 space-y-3">
                <div className="flex items-center justify-between text-indigo-400 font-mono text-[11px]">
                  <div className="flex items-center gap-1.5">
                    <FileText className="w-3.5 h-3.5" />
                    <span>{evidence.document_id || 'Extracted Dossier'}</span>
                  </div>
                  <span>Page {evidence.page_number ?? 'N/A'}</span>
                </div>

                <div className="p-3 rounded-lg bg-black/40 border border-slate-800 text-slate-300 font-mono italic leading-relaxed">
                  &quot;{evidence.snippet}&quot;
                </div>
              </div>
            ) : (
              <div className="p-4 rounded-xl bg-slate-950/40 border border-slate-800 text-slate-500 font-mono">
                No direct document citation required for deterministic evaluation.
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-slate-800 bg-slate-900 flex justify-end">
          <button
            onClick={onClose}
            className="px-4 py-1.5 rounded-lg border border-slate-700 bg-slate-800 text-xs text-slate-200 hover:text-white hover:bg-slate-700 transition-colors"
          >
            Close Inspector
          </button>
        </div>
      </div>
    </div>
  );
};
