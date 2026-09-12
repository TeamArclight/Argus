'use client';

import React from 'react';
import { X, Loader2, Terminal } from 'lucide-react';
import type { JobEventRead, JobRead, JobStage } from '@/types/api';
import { useJobStream } from '@/hooks/useJobStream';

export interface JobProgressDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  jobId?: string | null;
  job?: JobRead | null;
  events?: JobEventRead[];
  title?: string;
  onComplete?: () => void;
}

const STAGES: { key: JobStage; label: string; weight: number }[] = [
  { key: 'UPLOAD', label: 'Upload', weight: 15 },
  { key: 'OCR', label: 'Parsing / OCR', weight: 30 },
  { key: 'EXTRACTION', label: 'Extraction', weight: 50 },
  { key: 'VERIFICATION', label: 'Verification', weight: 70 },
  { key: 'COMPLIANCE', label: 'Compliance', weight: 85 },
  { key: 'REPORTING', label: 'Reporting', weight: 100 },
];

function formatEventTimestamp(ev: JobEventRead): string {
  const raw = ev.timestamp || (ev as Record<string, unknown>).created_at || (ev as Record<string, unknown>).occurred_at;
  if (!raw || typeof raw !== 'string' || raw === '—' || raw === 'null' || raw === 'undefined') {
    return 'Time unavailable';
  }
  try {
    const d = new Date(raw);
    if (isNaN(d.getTime())) return 'Time unavailable';
    return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' });
  } catch {
    return 'Time unavailable';
  }
}

export const JobProgressDrawer: React.FC<JobProgressDrawerProps> = ({
  isOpen,
  onClose,
  jobId,
  job: propJob,
  events: propEvents,
  title = 'Autonomous Pipeline Execution',
  onComplete,
}) => {
  const stream = useJobStream({
    jobId: jobId ?? null,
    onCompleted: () => {
      onComplete?.();
    },
  });

  const job = propJob ?? stream.job;
  const events = propEvents ?? stream.events;

  if (!isOpen) return null;

  const currentStage = job?.current_stage ?? stream.currentStage ?? 'UPLOAD';
  const isFailed = job?.status === 'FAILED' || stream.isFailed;
  const isCompleted = job?.status === 'COMPLETED' || stream.isCompleted;
  const isReviewRequired = job?.status === 'REVIEW_REQUIRED' || stream.isReviewRequired;
  const isTerminal = isCompleted || isFailed || isReviewRequired;

  const stageIdx = STAGES.findIndex((st) => st.key === currentStage);
  const activeStageWeight = stageIdx >= 0 ? STAGES[stageIdx].weight : 15;

  let progressPercent = 0;
  if (isCompleted) {
    progressPercent = 100;
  } else if (isFailed) {
    // If failed, do NOT display 100%. Set progress percentage to the stage where it failed.
    progressPercent = activeStageWeight;
  } else if (isReviewRequired) {
    progressPercent = activeStageWeight;
  } else {
    // In-flight: use max of reported progress or stage baseline
    const reported = job?.progress ?? stream.progress ?? (events.length > 0 ? events[events.length - 1].progress : 0);
    progressPercent = Math.max(reported, activeStageWeight);
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/70 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="w-full max-w-xl h-full bg-slate-900 border-l border-slate-800 flex flex-col shadow-2xl">
        {/* Header */}
        <div className="p-5 border-b border-slate-800 flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2.5">
              <span className={`w-2.5 h-2.5 rounded-full ${isCompleted ? 'bg-emerald-500' : isFailed ? 'bg-rose-500' : isReviewRequired ? 'bg-amber-500' : 'bg-indigo-500 animate-pulse'}`} />
              <h2 className="text-base font-semibold text-slate-100">{title}</h2>
              {isFailed ? (
                <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-rose-950 border border-rose-800/60 text-rose-400">
                  FAILED
                </span>
              ) : isCompleted ? (
                <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-emerald-950 border border-emerald-800/60 text-emerald-400">
                  COMPLETED
                </span>
              ) : isReviewRequired ? (
                <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-amber-950 border border-amber-800/60 text-amber-400">
                  REVIEW REQUIRED
                </span>
              ) : (
                <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-indigo-950 border border-indigo-800/60 text-indigo-400 animate-pulse">
                  RUNNING
                </span>
              )}
            </div>
            <p className="text-xs font-mono text-slate-400 mt-1">
              Job ID: {job?.id ?? jobId ?? 'Pending...'}
            </p>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-800 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Progress Bar & Status */}
        <div className="p-5 border-b border-slate-800/80 bg-slate-950/40 space-y-4">
          <div className="flex items-center justify-between text-xs">
            <span className="text-slate-400">Pipeline Execution Progress</span>
            <span className="font-mono font-medium text-slate-200">{progressPercent}%</span>
          </div>
          <div className="w-full h-2 bg-slate-800 rounded-full overflow-hidden">
            <div
              className={`h-full transition-all duration-300 ${
                isFailed ? 'bg-rose-500' : isCompleted ? 'bg-emerald-500' : isReviewRequired ? 'bg-amber-500' : 'bg-indigo-500'
              }`}
              style={{ width: `${progressPercent}%` }}
            />
          </div>

          {/* Stage Badges */}
          <div className="grid grid-cols-6 gap-1 pt-2">
            {STAGES.map((s, idx) => {
              let dotContent: React.ReactNode = idx + 1;
              let dotClass = 'bg-slate-800 text-slate-500 border border-slate-700';

              if (isFailed) {
                if (idx < stageIdx) {
                  dotContent = '✓';
                  dotClass = 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40';
                } else if (idx === stageIdx) {
                  dotContent = '✕';
                  dotClass = 'bg-rose-500/20 text-rose-400 border border-rose-500/50 font-bold';
                } else {
                  dotContent = idx + 1;
                  dotClass = 'bg-slate-800 text-slate-500 border border-slate-700';
                }
              } else if (isCompleted) {
                dotContent = '✓';
                dotClass = 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40';
              } else if (isReviewRequired) {
                if (idx < stageIdx) {
                  dotContent = '✓';
                  dotClass = 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40';
                } else if (idx === stageIdx) {
                  dotContent = '!';
                  dotClass = 'bg-amber-500/20 text-amber-400 border border-amber-500/50 font-bold animate-pulse';
                } else {
                  dotContent = idx + 1;
                  dotClass = 'bg-slate-800 text-slate-500 border border-slate-700';
                }
              } else {
                if (idx < stageIdx) {
                  dotContent = '✓';
                  dotClass = 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40';
                } else if (idx === stageIdx && !isTerminal) {
                  dotContent = idx + 1;
                  dotClass = 'bg-indigo-500/20 text-indigo-300 border border-indigo-500/60 animate-pulse';
                } else {
                  dotContent = idx + 1;
                  dotClass = 'bg-slate-800 text-slate-500 border border-slate-700';
                }
              }

              return (
                <div key={s.key} className="flex flex-col items-center gap-1">
                  <div className={`w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold ${dotClass}`}>
                    {dotContent}
                  </div>
                  <span className="text-[10px] text-slate-400 text-center truncate w-full">
                    {s.label}
                  </span>
                </div>
              );
            })}
          </div>
        </div>

        {/* Events / Live Terminal Log */}
        <div className="flex-1 overflow-y-auto p-5 space-y-2.5 font-mono text-xs">
          <div className="flex items-center gap-2 text-slate-500 pb-2 border-b border-slate-800/60">
            <Terminal className="w-4 h-4" />
            <span>Authoritative Event Stream</span>
          </div>

          {events.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-48 text-slate-500">
              <Loader2 className="w-6 h-6 animate-spin mb-2" />
              <p>Waiting for pipeline events from worker...</p>
            </div>
          ) : (
            events.map((ev, i) => (
              <div key={ev.id || i} className="p-2.5 rounded bg-slate-950/60 border border-slate-800/60 space-y-1">
                <div className="flex items-center justify-between text-[11px]">
                  <span className="text-indigo-400 font-semibold">{ev.stage}</span>
                  <span className="text-slate-500">{formatEventTimestamp(ev)}</span>
                </div>
                <p className="text-slate-200">{ev.message}</p>
              </div>
            ))
          )}
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-slate-800 bg-slate-950 flex justify-between items-center">
          <span className="text-xs text-slate-500 font-mono">
            {stream.isStreaming
              ? 'Stream: CONNECTED (SSE)'
              : isReviewRequired
                ? 'Status: FINISHED — OFFICER REVIEW REQUIRED'
                : isCompleted
                  ? 'Status: FINISHED'
                  : isFailed
                    ? 'Status: FAILED'
                    : 'Stream: IDLE'}
          </span>
          <button
            onClick={onClose}
            className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-xs font-medium"
          >
            Close Drawer
          </button>
        </div>
      </div>
    </div>
  );
};
