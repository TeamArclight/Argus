'use client';

import React, { useState } from 'react';
import { X, Users, Upload } from 'lucide-react';
import type { BidderCreate } from '@/types/api';

interface BidderCreateModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (data: BidderCreate, docFiles?: File[]) => Promise<void>;
  isSubmitting: boolean;
  tenderId: string;
}

export const BidderCreateModal: React.FC<BidderCreateModalProps> = ({
  isOpen,
  onClose,
  onSubmit,
  isSubmitting,
}) => {
  const [bidderName, setBidderName] = useState('');
  const [gstin, setGstin] = useState('');
  const [pan, setPan] = useState('');
  const [udyamNumber, setUdyamNumber] = useState('');
  const [cin, setCin] = useState('');
  const [files, setFiles] = useState<File[]>([]);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    await onSubmit(
      {
        bidder_name: bidderName,
        gstin: gstin || undefined,
        pan: pan || undefined,
        udyam_number: udyamNumber || undefined,
        cin: cin || undefined,
      },
      files.length > 0 ? files : undefined
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="w-full max-w-lg bg-slate-900 border border-slate-800 rounded-2xl shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="p-5 border-b border-slate-800 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-indigo-500/10 border border-indigo-500/30 flex items-center justify-center">
              <Users className="w-4 h-4 text-indigo-400" />
            </div>
            <div>
              <h3 className="text-sm font-bold text-white">Enroll Bidder Submission</h3>
              <p className="text-xs text-slate-400 font-mono">Register Vendor Dossier & Upload Artifacts</p>
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
            <label className="font-semibold text-slate-200">Legal Entity / Vendor Name *</label>
            <input
              type="text"
              required
              placeholder="e.g. Acme Tech Solutions Private Limited"
              value={bidderName}
              onChange={(e) => setBidderName(e.target.value)}
              className="w-full p-2.5 bg-slate-950 border border-slate-800 rounded-lg text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label className="font-semibold text-slate-200">GSTIN</label>
              <input
                type="text"
                placeholder="e.g. 07AAAAA0000A1Z5"
                value={gstin}
                onChange={(e) => setGstin(e.target.value)}
                className="w-full p-2.5 bg-slate-950 border border-slate-800 rounded-lg text-slate-200 font-mono placeholder-slate-500 focus:outline-none focus:border-indigo-500"
              />
            </div>

            <div className="space-y-1.5">
              <label className="font-semibold text-slate-200">PAN</label>
              <input
                type="text"
                placeholder="e.g. AAAAA0000A"
                value={pan}
                onChange={(e) => setPan(e.target.value)}
                className="w-full p-2.5 bg-slate-950 border border-slate-800 rounded-lg text-slate-200 font-mono placeholder-slate-500 focus:outline-none focus:border-indigo-500"
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label className="font-semibold text-slate-200">Udyam Registration</label>
              <input
                type="text"
                placeholder="e.g. UDYAM-DL-01-0012345"
                value={udyamNumber}
                onChange={(e) => setUdyamNumber(e.target.value)}
                className="w-full p-2.5 bg-slate-950 border border-slate-800 rounded-lg text-slate-200 font-mono placeholder-slate-500 focus:outline-none focus:border-indigo-500"
              />
            </div>

            <div className="space-y-1.5">
              <label className="font-semibold text-slate-200">CIN</label>
              <input
                type="text"
                placeholder="e.g. U72200DL2020PTC123456"
                value={cin}
                onChange={(e) => setCin(e.target.value)}
                className="w-full p-2.5 bg-slate-950 border border-slate-800 rounded-lg text-slate-200 font-mono placeholder-slate-500 focus:outline-none focus:border-indigo-500"
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <label className="font-semibold text-slate-200">Vendor Documents (PDF / Statements)</label>
            <div className="p-4 rounded-xl border border-dashed border-slate-700 bg-slate-950/40 text-center">
              <input
                type="file"
                multiple
                accept=".pdf,.doc,.docx"
                onChange={(e) => setFiles(Array.from(e.target.files || []))}
                className="hidden"
                id="bidder-files-input"
              />
              <label
                htmlFor="bidder-files-input"
                className="cursor-pointer inline-flex flex-col items-center justify-center gap-1 text-slate-400 hover:text-indigo-400 transition-colors"
              >
                <Upload className="w-5 h-5 text-indigo-400" />
                <span className="font-mono text-xs">
                  {files.length > 0 ? `${files.length} document(s) selected` : 'Select vendor PDFs (GST, CA Turnover, MSME)'}
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
              {isSubmitting ? 'Enrolling Bidder...' : 'Enroll Bidder'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
