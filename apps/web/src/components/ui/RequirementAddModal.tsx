'use client';

import React, { useState } from 'react';
import { X, Plus } from 'lucide-react';
import type { OperatorEnum, RequirementType, TenderRequirementCreate } from '@/types/api';
import { api } from '@/services/api';

export interface RequirementAddModalProps {
  isOpen: boolean;
  onClose: () => void;
  tenderId: string;
  onSubmit?: (data: TenderRequirementCreate) => Promise<void>;
  onAdded?: () => void | Promise<void>;
  isSubmitting?: boolean;
}

export const RequirementAddModal: React.FC<RequirementAddModalProps> = ({
  isOpen,
  onClose,
  tenderId,
  onSubmit,
  onAdded,
  isSubmitting: propSubmitting,
}) => {
  const [clause, setClause] = useState('');
  const [requirementType, setRequirementType] = useState<RequirementType>('TURNOVER');
  const [field, setField] = useState('');
  const [operator, setOperator] = useState<OperatorEnum>('GTE');
  const [expectedValue, setExpectedValue] = useState('');
  const [unit, setUnit] = useState('');
  const [mandatory, setMandatory] = useState(true);
  const [localSubmitting, setLocalSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLocalSubmitting(true);
    const payload: TenderRequirementCreate = {
      clause,
      requirement_type: requirementType,
      field,
      operator,
      expected_value: expectedValue,
      unit: unit || undefined,
      mandatory,
      confidence: 1.0,
      requires_verification: true,
      is_approved: true,
    };

    try {
      if (onSubmit) {
        await onSubmit(payload);
      } else {
        await api.createTenderRequirement(tenderId, payload);
        if (onAdded) await onAdded();
      }
      onClose();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to add requirement.');
    } finally {
      setLocalSubmitting(false);
    }
  };

  const isSubmitting = propSubmitting ?? localSubmitting;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="w-full max-w-lg rounded-2xl bg-zinc-900 border border-zinc-800 p-6 shadow-2xl space-y-4 text-zinc-100">
        <div className="flex items-center justify-between border-b border-zinc-800 pb-3">
          <h3 className="text-base font-semibold text-zinc-100 flex items-center gap-2">
            <Plus className="w-4 h-4 text-blue-400" /> Add Evaluation Clause
          </h3>
          <button onClick={onClose} className="text-zinc-400 hover:text-zinc-200">
            <X className="w-5 h-5" />
          </button>
        </div>

        {error && (
          <div className="p-3 rounded-lg bg-rose-950/30 border border-rose-800/50 text-rose-300 text-xs">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-zinc-400 mb-1">Clause Reference *</label>
            <input
              type="text"
              required
              value={clause}
              onChange={(e) => setClause(e.target.value)}
              placeholder="e.g. Section 4.2 - Annual Turnover"
              className="w-full px-3 py-2 rounded-lg bg-zinc-950 border border-zinc-800 text-zinc-100 text-sm focus:outline-none focus:border-blue-500"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-zinc-400 mb-1">Requirement Type *</label>
              <select
                value={requirementType}
                onChange={(e) => setRequirementType(e.target.value as RequirementType)}
                className="w-full px-3 py-2 rounded-lg bg-zinc-950 border border-zinc-800 text-zinc-100 text-sm focus:outline-none focus:border-blue-500"
              >
                <option value="TURNOVER">TURNOVER</option>
                <option value="EXPERIENCE_YEARS">EXPERIENCE_YEARS</option>
                <option value="SIMILAR_WORK_VALUE">SIMILAR_WORK_VALUE</option>
                <option value="GST_ACTIVE">GST_ACTIVE</option>
                <option value="PAN_MATCH">PAN_MATCH</option>
                <option value="CIN_ACTIVE">CIN_ACTIVE</option>
                <option value="UDYAM_MSME">UDYAM_MSME</option>
                <option value="LOCAL_CONTENT">LOCAL_CONTENT</option>
                <option value="OEM_AUTH">OEM_AUTH</option>
                <option value="TECHNICAL_SPEC">TECHNICAL_SPEC</option>
                <option value="OTHER">OTHER</option>
              </select>
            </div>

            <div>
              <label className="block text-xs font-medium text-zinc-400 mb-1">Field / Metric *</label>
              <input
                type="text"
                required
                value={field}
                onChange={(e) => setField(e.target.value)}
                placeholder="e.g. annual_turnover_inr"
                className="w-full px-3 py-2 rounded-lg bg-zinc-950 border border-zinc-800 text-zinc-100 text-sm font-mono focus:outline-none focus:border-blue-500"
              />
            </div>
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="block text-xs font-medium text-zinc-400 mb-1">Operator *</label>
              <select
                value={operator}
                onChange={(e) => setOperator(e.target.value as OperatorEnum)}
                className="w-full px-3 py-2 rounded-lg bg-zinc-950 border border-zinc-800 text-zinc-100 text-sm focus:outline-none focus:border-blue-500"
              >
                <option value="GTE">&gt;= (GTE)</option>
                <option value="GT">&gt; (GT)</option>
                <option value="LTE">&lt;= (LTE)</option>
                <option value="LT">&lt; (LT)</option>
                <option value="EQ">== (EQ)</option>
                <option value="CONTAINS">CONTAINS</option>
                <option value="EXISTS">EXISTS</option>
              </select>
            </div>

            <div>
              <label className="block text-xs font-medium text-zinc-400 mb-1">Expected Value *</label>
              <input
                type="text"
                required
                value={expectedValue}
                onChange={(e) => setExpectedValue(e.target.value)}
                placeholder="e.g. 50000000"
                className="w-full px-3 py-2 rounded-lg bg-zinc-950 border border-zinc-800 text-zinc-100 text-sm font-mono focus:outline-none focus:border-blue-500"
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-zinc-400 mb-1">Unit</label>
              <input
                type="text"
                value={unit}
                onChange={(e) => setUnit(e.target.value)}
                placeholder="INR, Years, etc."
                className="w-full px-3 py-2 rounded-lg bg-zinc-950 border border-zinc-800 text-zinc-100 text-sm focus:outline-none focus:border-blue-500"
              />
            </div>
          </div>

          <div className="flex items-center gap-2 pt-1">
            <input
              type="checkbox"
              id="mandatory"
              checked={mandatory}
              onChange={(e) => setMandatory(e.target.checked)}
              className="rounded border-zinc-800 text-blue-600 focus:ring-blue-500"
            />
            <label htmlFor="mandatory" className="text-xs text-zinc-300 cursor-pointer">
              Mandatory disqualifying requirement
            </label>
          </div>

          <div className="flex justify-end gap-3 pt-3 border-t border-zinc-800">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-xs font-medium"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSubmitting}
              className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-xs font-medium disabled:opacity-50"
            >
              {isSubmitting ? 'Saving...' : 'Save Clause'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
