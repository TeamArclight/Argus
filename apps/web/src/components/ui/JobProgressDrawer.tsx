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

const STAGES: { key: JobStage; label: string }[] = [
  { key: 'UPLOAD', label: 'Upload' },
  { key: 'OCR', label: 'OCR & Parsing' },
  { key: 'EXTRACTION', label: 'Extraction' },
  { key: 'VERIFICATION', label: 'Verification' },
  { key: 'COMPLIANCE', label: 'Compliance' },
  { key: 'REPORTING', label: 'Reporting' },
];

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
  const progressPercent = job?.progress ?? stream.progress ?? (events.length > 0 ? events[events.length - 1].progress : 0);
  const isFailed = job?.status === 'FAILED' || stream.isFailed;
  const isCompleted = job?.status === 'COMPLETED' || stream.isCompleted;
  // REVIEW_REQUIRED is a terminal job state, not an in-flight one. Without this
  // the drawer pulsed indefinitely on the outcome the workflow produces most
  // often (audit finding H-2).
  const isReviewRequired = job?.status === 'REVIEW_REQUIRED' || stream.isReviewRequired;
  const isTerminal = isCompleted || isFailed || isReviewRequired;

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/70 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="w-full max-w-xl h-full bg-slate-900 border-l border-slate-800 flex flex-col shadow-2xl">
        {/* Header */}
        <div className="p-5 border-b border-slate-800 flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2">
              <span className={`w-2.5 h-2.5 rounded-full ${isCompleted ? 'bg-emerald-500' : isFailed ? 'bg-rose-500' : isReviewRequired ? 'bg-amber-500' : 'bg-indigo-500 animate-pulse'}`} />
              <h2 className="text-base font-semibold text-slate-100">{title}</h2>
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
              const stageIdx = STAGES.findIndex((st) => st.key === currentStage);
              const isPast = stageIdx > idx || isCompleted;
              const isCurrent = stageIdx === idx && !isTerminal;
              return (
                <div key={s.key} className="flex flex-col items-center gap-1">
                  <div
                    className={`w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold ${
                      isPast
                        ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40'
                        : isCurrent
                        ? 'bg-indigo-500/20 text-indigo-300 border border-indigo-500/60 animate-pulse'
                        : 'bg-slate-800 text-slate-500 border border-slate-700'
                    }`}
                  >
                    {isPast ? '✓' : idx + 1}
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
                  <span className="text-slate-500">{new Date(ev.timestamp).toLocaleTimeString()}</span>
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
