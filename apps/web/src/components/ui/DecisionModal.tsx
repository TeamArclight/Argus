'use client';

import React, { useState } from 'react';
import { X, ShieldCheck, CheckCircle2, XCircle, Lock } from 'lucide-react';
import type { HumanDecisionCreate, HumanDecisionStatus } from '@/types/api';

interface DecisionModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (data: HumanDecisionCreate) => Promise<void>;
  bidderName: string;
  isSubmitting: boolean;
  currentDecision?: HumanDecisionStatus | null;
}

export const DecisionModal: React.FC<DecisionModalProps> = ({
  isOpen,
  onClose,
  onSubmit,
  bidderName,
  isSubmitting,
  currentDecision,
}) => {
  const [decision, setDecision] = useState<'QUALIFIED' | 'DISQUALIFIED'>(
    currentDecision === 'DISQUALIFIED' ? 'DISQUALIFIED' : 'QUALIFIED'
  );
  const [reasonCode, setReasonCode] = useState('TECHNICAL_EVALUATION');
  const [remarks, setRemarks] = useState('');

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    await onSubmit({
      status: decision,
      reason_code: reasonCode,
      remarks: remarks || null,
    });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="w-full max-w-lg bg-slate-900 border border-slate-800 rounded-2xl shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="p-5 border-b border-slate-800 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-indigo-500/10 border border-indigo-500/30 flex items-center justify-center">
              <ShieldCheck className="w-4 h-4 text-indigo-400" />
            </div>
            <div>
              <h3 className="text-sm font-bold text-white">Record Human Qualification Decision</h3>
              <p className="text-xs text-slate-400 font-mono">{bidderName}</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg border border-slate-700 bg-slate-800 text-slate-400 hover:text-white transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-6 space-y-4 text-xs">
          {/* Authority notice */}
          <div className="p-3 rounded-lg border border-indigo-900/40 bg-indigo-950/20 text-slate-400 leading-relaxed flex items-start gap-2">
            <Lock className="w-4 h-4 text-indigo-400 shrink-0 mt-0.5" />
            <span>
              This decision is final and recorded immutably under P11/P12 statutory authority.
            </span>
          </div>

          {/* Decision Selector */}
          <div className="space-y-2">
            <label className="font-semibold text-slate-200">Official Decision</label>
            <div className="grid grid-cols-2 gap-3">
              <button
                type="button"
                onClick={() => setDecision('QUALIFIED')}
                className={`p-3 rounded-xl border flex items-center justify-center gap-2 font-mono font-bold transition-all ${
                  decision === 'QUALIFIED'
                    ? 'border-emerald-600 bg-emerald-950/60 text-emerald-300 ring-2 ring-emerald-500/30'
                    : 'border-slate-800 bg-slate-950/60 text-slate-400 hover:text-slate-200'
                }`}
              >
                <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                QUALIFIED
              </button>

              <button
                type="button"
                onClick={() => setDecision('DISQUALIFIED')}
                className={`p-3 rounded-xl border flex items-center justify-center gap-2 font-mono font-bold transition-all ${
                  decision === 'DISQUALIFIED'
                    ? 'border-rose-600 bg-rose-950/60 text-rose-300 ring-2 ring-rose-500/30'
                    : 'border-slate-800 bg-slate-950/60 text-slate-400 hover:text-slate-200'
                }`}
              >
                <XCircle className="w-4 h-4 text-rose-400" />
                DISQUALIFIED
              </button>
            </div>
          </div>

          {/* Reason Code */}
          <div className="space-y-1.5">
            <label className="font-semibold text-slate-200">Standard Reason Code</label>
            <select
              value={reasonCode}
              onChange={(e) => setReasonCode(e.target.value)}
              className="w-full p-2.5 bg-slate-950 border border-slate-800 rounded-lg text-slate-200 focus:outline-none focus:border-indigo-500"
            >
              <option value="TECHNICAL_EVALUATION">Technical & Financial Criteria Satisfied</option>
              <option value="STATUTORY_COMPLIANCE">Statutory MSME / GST Compliance Validated</option>
              <option value="INSUFFICIENT_TURNOVER">Disqualification: Minimum Turnover Not Met</option>
              <option value="INVALID_DOCUMENTS">Disqualification: Inauthentic or Incomplete Documents</option>
              <option value="MANUAL_OVERRIDE">Officer Discretionary Override</option>
            </select>
          </div>

          {/* Remarks */}
          <div className="space-y-1.5">
            <label className="font-semibold text-slate-200">Official Officer Remarks</label>
            <textarea
              rows={3}
              value={remarks}
              onChange={(e) => setRemarks(e.target.value)}
              placeholder="Provide statutory justification for the recorded decision..."
              className="w-full p-2.5 bg-slate-950 border border-slate-800 rounded-lg text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
            />
          </div>

          {/* Modal Actions */}
          <div className="flex items-center justify-end gap-3 pt-4 border-t border-slate-800">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded-lg border border-slate-700 bg-slate-800 text-slate-300 hover:text-white transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSubmitting}
              className="px-5 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white font-medium shadow-sm transition-colors disabled:opacity-50"
            >
              {isSubmitting ? 'Recording Decision...' : 'Commit Decision'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
