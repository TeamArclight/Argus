'use client';

import React, { useState } from 'react';
import { X, FileText, Upload } from 'lucide-react';
import type { TenderCreate } from '@/types/api';

interface TenderCreateModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (data: TenderCreate, rfpFile?: File) => Promise<void>;
  isSubmitting: boolean;
}

export const TenderCreateModal: React.FC<TenderCreateModalProps> = ({
  isOpen,
  onClose,
  onSubmit,
  isSubmitting,
}) => {
  const [tenderNumber, setTenderNumber] = useState('');
  const [title, setTitle] = useState('');
  const [authority, setAuthority] = useState('');
  const [budget, setBudget] = useState('');
  const [deadline, setDeadline] = useState('');
  const [file, setFile] = useState<File | null>(null);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    await onSubmit(
      {
        tender_number: tenderNumber,
        title,
        authority: authority || undefined,
        budget: budget ? Number(budget) : undefined,
        deadline: deadline ? new Date(deadline).toISOString() : undefined,
      },
      file || undefined
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="w-full max-w-lg bg-slate-900 border border-slate-800 rounded-2xl shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="p-5 border-b border-slate-800 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-indigo-500/10 border border-indigo-500/30 flex items-center justify-center">
              <FileText className="w-4 h-4 text-indigo-400" />
            </div>
            <div>
              <h3 className="text-sm font-bold text-white">Create New Procurement Tender</h3>
              <p className="text-xs text-slate-400 font-mono">Publish RFP & Ingest Evaluation Criteria</p>
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
          <div className="space-y-1.5">
            <label className="font-semibold text-slate-200">Tender Reference Number *</label>
            <input
              type="text"
              required
              placeholder="e.g. GEM/2026/B/4521099"
              value={tenderNumber}
              onChange={(e) => setTenderNumber(e.target.value)}
              className="w-full p-2.5 bg-slate-950 border border-slate-800 rounded-lg text-slate-200 font-mono placeholder-slate-500 focus:outline-none focus:border-indigo-500"
            />
          </div>

          <div className="space-y-1.5">
            <label className="font-semibold text-slate-200">Tender Title *</label>
            <input
              type="text"
              required
              placeholder="e.g. Smart City Optical Fiber & Surveillance Network"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className="w-full p-2.5 bg-slate-950 border border-slate-800 rounded-lg text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label className="font-semibold text-slate-200">Procurement Authority</label>
              <input
                type="text"
                placeholder="e.g. NHAI, GeM, RailTel"
                value={authority}
                onChange={(e) => setAuthority(e.target.value)}
                className="w-full p-2.5 bg-slate-950 border border-slate-800 rounded-lg text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
              />
            </div>

            <div className="space-y-1.5">
              <label className="font-semibold text-slate-200">Estimated Budget (INR)</label>
              <input
                type="number"
                placeholder="e.g. 50000000"
                value={budget}
                onChange={(e) => setBudget(e.target.value)}
                className="w-full p-2.5 bg-slate-950 border border-slate-800 rounded-lg text-slate-200 font-mono placeholder-slate-500 focus:outline-none focus:border-indigo-500"
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <label className="font-semibold text-slate-200">Closing Date</label>
            <input
              type="date"
              value={deadline}
              onChange={(e) => setDeadline(e.target.value)}
              className="w-full p-2.5 bg-slate-950 border border-slate-800 rounded-lg text-slate-200 focus:outline-none focus:border-indigo-500"
            />
          </div>

          <div className="space-y-1.5">
            <label className="font-semibold text-slate-200">Upload RFP / Tender Document (PDF)</label>
            <div className="p-4 rounded-xl border border-dashed border-slate-700 bg-slate-950/40 text-center">
              <input
                type="file"
                accept=".pdf,.doc,.docx"
                onChange={(e) => setFile(e.target.files?.[0] || null)}
                className="hidden"
                id="rfp-file-input"
              />
              <label
                htmlFor="rfp-file-input"
                className="cursor-pointer inline-flex flex-col items-center justify-center gap-1 text-slate-400 hover:text-indigo-400 transition-colors"
              >
                <Upload className="w-5 h-5 text-indigo-400" />
                <span className="font-mono text-xs">
                  {file ? file.name : 'Select RFP PDF to automatically extract rules'}
                </span>
              </label>
            </div>
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
              {isSubmitting ? 'Creating Tender...' : 'Create Tender'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
