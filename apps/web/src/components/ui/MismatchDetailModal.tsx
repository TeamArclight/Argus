'use client';

import React, { useEffect } from 'react';
import { X, AlertTriangle, ShieldAlert, CheckCircle2, ArrowRight } from 'lucide-react';
import type { RiskSignalRead, RiskSeverity } from '@/types/api';

export interface MismatchDetailItem {
  mismatch_type?: string;
  field: string;
  existing_value?: string | number | null;
  extracted_value?: string | number | null;
  reason?: string;
  severity?: RiskSeverity | 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW' | 'INFO';
  source_filename?: string;
  source_page?: number | null;
  evidence_ref?: string;
  provider_source?: string;
  recommendation?: string;
}

interface MismatchDetailModalProps {
  isOpen: boolean;
  onClose: () => void;
  title?: string;
  documentFilename?: string;
  mismatches?: MismatchDetailItem[];
  riskSignals?: RiskSignalRead[];
}

export const MismatchDetailModal: React.FC<MismatchDetailModalProps> = ({
  isOpen,
  onClose,
  title = 'Detected Document & Cross-Source Mismatches',
  documentFilename,
  mismatches = [],
  riskSignals = [],
}) => {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const getSeverityBadge = (severity?: string) => {
    const sev = (severity || 'MEDIUM').toUpperCase();
    switch (sev) {
      case 'CRITICAL':
      case 'HIGH':
        return (
          <span className="px-2 py-0.5 rounded text-[11px] font-mono font-semibold bg-rose-950/80 text-rose-300 border border-rose-800/80 inline-flex items-center gap-1">
            <AlertTriangle className="w-3 h-3 text-rose-400" /> {sev}
          </span>
        );
      case 'MEDIUM':
        return (
          <span className="px-2 py-0.5 rounded text-[11px] font-mono font-semibold bg-amber-950/80 text-amber-300 border border-amber-800/80 inline-flex items-center gap-1">
            <AlertTriangle className="w-3 h-3 text-amber-400" /> {sev}
          </span>
        );
      default:
        return (
          <span className="px-2 py-0.5 rounded text-[11px] font-mono font-semibold bg-slate-800 text-slate-300 border border-slate-700 inline-flex items-center gap-1">
            <CheckCircle2 className="w-3 h-3 text-slate-400" /> {sev}
          </span>
        );
    }
  };

  const totalCount = mismatches.length + riskSignals.length;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-sm animate-fade-in"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="mismatch-modal-title"
    >
      <div className="relative w-full max-w-3xl max-h-[85vh] bg-slate-900 border border-slate-800 rounded-xl shadow-2xl flex flex-col overflow-hidden">
        {/* Header */}
        <div className="px-6 py-4 border-b border-slate-800 flex items-center justify-between bg-slate-950/60">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-lg bg-amber-950/80 border border-amber-800/60 text-amber-400">
              <ShieldAlert className="w-5 h-5" />
            </div>
            <div>
              <h2 id="mismatch-modal-title" className="text-lg font-bold text-slate-100">
                {title}
              </h2>
              <p className="text-xs text-slate-400 mt-0.5">
                {totalCount} anomaly signal{totalCount === 1 ? '' : 's'} identified by the Advisory Risk Engine
                {documentFilename ? ` in ${documentFilename}` : ''}
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-800 transition-colors"
            aria-label="Close modal"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content Body */}
        <div className="p-6 overflow-y-auto space-y-4">
          {totalCount === 0 ? (
            <div className="p-8 text-center rounded-lg bg-slate-950/40 border border-slate-800/60">
              <CheckCircle2 className="w-8 h-8 text-emerald-400 mx-auto mb-2" />
              <p className="text-sm font-medium text-slate-300">No Document Mismatches Detected</p>
              <p className="text-xs text-slate-500 mt-1">
                Extracted facts match registered profile details and reference registry records cleanly.
              </p>
            </div>
          ) : (
            <>
              {/* Document Mismatches */}
              {mismatches.map((item, idx) => {
                const typeLabel = item.mismatch_type || 'CROSS_SOURCE_MISMATCH';
                const fieldName = item.field || 'Not available';
                const claimedVal = item.extracted_value !== undefined && item.extracted_value !== null ? String(item.extracted_value) : 'Not available';
                const refVal = item.existing_value !== undefined && item.existing_value !== null ? String(item.existing_value) : 'Not available';
                const reasonText = item.reason || `Extracted document value ("${claimedVal}") conflicts with verified profile/reference value ("${refVal}").`;
                const docName = item.source_filename || documentFilename || 'Not available';
                const pageNum = item.source_page !== undefined && item.source_page !== null ? `Page ${item.source_page}` : 'Not available';
                const evRef = item.evidence_ref || 'Not available';
                const provSource = item.provider_source || 'ARGUS Risk & Document Fact Extractor';
                const recText = item.recommendation || 'Advisory flag for human procurement officer review. Does not alter statutory compliance status.';

                return (
                  <div
                    key={`mismatch_${idx}`}
                    className="p-4 rounded-xl bg-slate-950/60 border border-amber-900/40 space-y-3"
                  >
                    <div className="flex items-center justify-between flex-wrap gap-2">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-xs font-semibold text-amber-400 bg-amber-950/80 px-2 py-0.5 rounded border border-amber-800/60">
                          {typeLabel}
                        </span>
                        <span className="text-sm font-bold text-slate-200">Field: {fieldName}</span>
                      </div>
                      {getSeverityBadge(item.severity)}
                    </div>

                    {/* Comparison Box */}
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 p-3 rounded-lg bg-slate-900/80 border border-slate-800/80 text-xs">
                      <div>
                        <span className="text-slate-500 font-mono block mb-1">Extracted / Claimed Value:</span>
                        <span className="font-mono font-semibold text-amber-300 bg-amber-950/40 px-2 py-1 rounded border border-amber-800/40 inline-block break-all">
                          {claimedVal}
                        </span>
                      </div>
                      <div>
                        <span className="text-slate-500 font-mono block mb-1">Verified / Reference Value:</span>
                        <span className="font-mono font-semibold text-emerald-300 bg-emerald-950/40 px-2 py-1 rounded border border-emerald-800/40 inline-block break-all">
                          {refVal}
                        </span>
                      </div>
                    </div>

                    {/* Explanation */}
                    <div className="text-xs text-slate-300 space-y-1">
                      <p className="font-medium text-slate-200">Explanation / Reason:</p>
                      <p className="text-slate-400 bg-slate-900/60 p-2 rounded border border-slate-800/60">
                        {reasonText}
                      </p>
                    </div>

                    {/* Provenance Metadata */}
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-2 border-t border-slate-800/60 text-[11px] font-mono text-slate-400">
                      <div>
                        <span className="text-slate-500 block">Source File:</span>
                        <span className="text-slate-300 truncate block" title={docName}>{docName}</span>
                      </div>
                      <div>
                        <span className="text-slate-500 block">Source Page:</span>
                        <span className="text-slate-300 block">{pageNum}</span>
                      </div>
                      <div>
                        <span className="text-slate-500 block">Evidence Ref:</span>
                        <span className="text-slate-300 truncate block" title={evRef}>{evRef}</span>
                      </div>
                      <div>
                        <span className="text-slate-500 block">Provider:</span>
                        <span className="text-slate-300 truncate block" title={provSource}>{provSource}</span>
                      </div>
                    </div>

                    {/* Recommendation */}
                    <div className="p-2.5 rounded-lg bg-indigo-950/30 border border-indigo-800/40 text-xs text-indigo-300 flex items-start gap-2">
                      <ArrowRight className="w-3.5 h-3.5 text-indigo-400 flex-shrink-0 mt-0.5" />
                      <div>
                        <span className="font-semibold text-indigo-200">Recommendation: </span>
                        {recText}
                      </div>
                    </div>
                  </div>
                );
              })}

              {/* Risk Signals */}
              {riskSignals.map((sig, idx) => (
                <div
                  key={sig.id || `risk_${idx}`}
                  className="p-4 rounded-xl bg-slate-950/60 border border-rose-900/40 space-y-3"
                >
                  <div className="flex items-center justify-between flex-wrap gap-2">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs font-semibold text-rose-400 bg-rose-950/80 px-2 py-0.5 rounded border border-rose-800/60">
                        {sig.signal_type}
                      </span>
                      <span className="text-sm font-bold text-slate-200">{sig.title}</span>
                    </div>
                    {getSeverityBadge(sig.severity)}
                  </div>

                  <p className="text-xs text-slate-300 bg-slate-900/60 p-2.5 rounded border border-slate-800/60">
                    {sig.description}
                  </p>

                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-2 border-t border-slate-800/60 text-[11px] font-mono text-slate-400">
                    <div>
                      <span className="text-slate-500 block">Reason Code:</span>
                      <span className="text-slate-300 block">{sig.reason_code || 'Not available'}</span>
                    </div>
                    <div>
                      <span className="text-slate-500 block">Source Mode:</span>
                      <span className="text-slate-300 block">{sig.source_mode || 'Not available'}</span>
                    </div>
                    <div>
                      <span className="text-slate-500 block">Evidence Count:</span>
                      <span className="text-slate-300 block">{sig.evidence_ids?.length || 0}</span>
                    </div>
                    <div>
                      <span className="text-slate-500 block">Signal ID:</span>
                      <span className="text-slate-300 truncate block" title={sig.id}>{sig.id || 'Not available'}</span>
                    </div>
                  </div>
                </div>
              ))}
            </>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-3 border-t border-slate-800 bg-slate-950/80 flex items-center justify-between text-xs text-slate-400">
          <span>Advisory risk engine signals do not automatically alter deterministic compliance authority.</span>
          <button
            onClick={onClose}
            className="px-4 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg font-medium transition-colors cursor-pointer"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
};
