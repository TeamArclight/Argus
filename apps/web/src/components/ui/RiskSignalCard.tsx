import React from 'react';
import { AlertTriangle, AlertCircle, Info } from 'lucide-react';
import type { RiskSignalRead } from '@/types/api';

interface RiskSignalCardProps {
  risk: RiskSignalRead;
}

export const RiskSignalCard: React.FC<RiskSignalCardProps> = ({ risk }) => {
  const severityColors = {
    CRITICAL: 'border-rose-900/80 bg-rose-950/30 text-rose-300',
    HIGH: 'border-rose-900/60 bg-rose-950/20 text-rose-300',
    MEDIUM: 'border-amber-900/60 bg-amber-950/20 text-amber-300',
    LOW: 'border-blue-900/50 bg-blue-950/20 text-blue-300',
  }[risk.severity || 'MEDIUM'];

  const IconComponent =
    risk.severity === 'CRITICAL' || risk.severity === 'HIGH'
      ? AlertTriangle
      : risk.severity === 'MEDIUM'
      ? AlertCircle
      : Info;

  return (
    <div className={`p-3.5 rounded-xl border ${severityColors} space-y-2`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <IconComponent className="w-4 h-4 shrink-0" />
          <span className="text-xs font-bold font-mono uppercase tracking-wide">
            {risk.signal_type || 'RISK_SIGNAL'}
          </span>
        </div>
        <span className="px-1.5 py-0.5 text-[10px] font-mono font-bold rounded bg-black/40 uppercase">
          {risk.severity || 'ADVISORY'}
        </span>
      </div>

      <p className="text-xs font-semibold text-white">{risk.title}</p>
      <p className="text-xs leading-relaxed text-slate-300 font-mono">{risk.description}</p>
    </div>
  );
};
