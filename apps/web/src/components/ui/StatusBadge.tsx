import React from 'react';
import {
  CheckCircle2,
  XCircle,
  AlertTriangle,
  HelpCircle,
  MinusCircle,
  Clock,
  } from 'lucide-react';
import type { ComplianceStatus, HumanDecisionStatus, JobStatus } from '@/types/api';

type BadgeStatus =
  | ComplianceStatus
  | HumanDecisionStatus
  | JobStatus
  | 'PENDING'
  | 'ACTIVE'
  | 'DRAFT'
  | 'CLOSED'
  | 'ARCHIVED'
  | 'LOW'
  | 'MEDIUM'
  | 'HIGH'
  | 'CRITICAL'
  | string;

interface StatusBadgeProps {
  status: BadgeStatus;
  size?: 'sm' | 'md' | 'lg';
  showIcon?: boolean;
}

export const StatusBadge: React.FC<StatusBadgeProps> = ({
  status,
  size = 'md',
  showIcon = true,
}) => {
  const normalized = (status || 'UNKNOWN').toUpperCase();

  const sizeClasses = {
    sm: 'text-[10px] px-2 py-0.5 gap-1',
    md: 'text-xs px-2.5 py-1 gap-1.5',
    lg: 'text-sm px-3 py-1.5 gap-2',
  }[size];

  const iconSizes = {
    sm: 'w-3 h-3',
    md: 'w-3.5 h-3.5',
    lg: 'w-4 h-4',
  }[size];

  // 1. Compliance / Evaluation Status
  if (normalized === 'PASSED' || normalized === 'PASS' || normalized === 'QUALIFIED' || normalized === 'COMPLETED') {
    return (
      <span className={`inline-flex items-center font-mono font-semibold rounded-md bg-emerald-950/60 border border-emerald-800/80 text-emerald-400 shadow-sm ${sizeClasses}`}>
        {showIcon && <CheckCircle2 className={iconSizes} />}
        <span>{normalized}</span>
      </span>
    );
  }

  if (normalized === 'FAILED' || normalized === 'FAIL' || normalized === 'DISQUALIFIED') {
    return (
      <span className={`inline-flex items-center font-mono font-semibold rounded-md bg-rose-950/60 border border-rose-800/80 text-rose-400 shadow-sm ${sizeClasses}`}>
        {showIcon && <XCircle className={iconSizes} />}
        <span>{normalized}</span>
      </span>
    );
  }

  if (normalized === 'REVIEW_REQUIRED' || normalized === 'FLAGGED' || normalized === 'HIGH' || normalized === 'CRITICAL') {
    return (
      <span className={`inline-flex items-center font-mono font-semibold rounded-md bg-amber-950/60 border border-amber-800/80 text-amber-400 shadow-sm ${sizeClasses}`}>
        {showIcon && <AlertTriangle className={iconSizes} />}
        <span>{normalized}</span>
      </span>
    );
  }

  if (normalized === 'NOT_APPLICABLE' || normalized === 'NA') {
    return (
      <span className={`inline-flex items-center font-mono font-semibold rounded-md bg-slate-900 border border-slate-700 text-slate-400 shadow-sm ${sizeClasses}`}>
        {showIcon && <MinusCircle className={iconSizes} />}
        <span>N/A</span>
      </span>
    );
  }

  if (normalized === 'IN_PROGRESS' || normalized === 'RUNNING' || normalized === 'PENDING' || normalized === 'ACTIVE') {
    return (
      <span className={`inline-flex items-center font-mono font-semibold rounded-md bg-indigo-950/60 border border-indigo-800/80 text-indigo-300 shadow-sm ${sizeClasses}`}>
        {showIcon && <Clock className={iconSizes} />}
        <span>{normalized}</span>
      </span>
    );
  }

  // Default / Unknown
  return (
    <span className={`inline-flex items-center font-mono font-semibold rounded-md bg-slate-900 border border-slate-700 text-slate-400 shadow-sm ${sizeClasses}`}>
      {showIcon && <HelpCircle className={iconSizes} />}
      <span>{normalized}</span>
    </span>
  );
};
