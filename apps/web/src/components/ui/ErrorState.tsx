import React from 'react';
import { AlertOctagon, RefreshCw } from 'lucide-react';

interface ErrorStateProps {
  title?: string;
  message: string;
  actionLabel?: string;
  onAction?: () => void;
}

export const ErrorState: React.FC<ErrorStateProps> = ({
  title = 'Service Query Exception',
  message,
  actionLabel,
  onAction,
}) => {
  return (
    <div className="flex flex-col items-center justify-center p-8 min-h-[300px] rounded-xl border border-rose-900/50 bg-rose-950/20 text-center">
      <div className="w-10 h-10 rounded-full bg-rose-900/40 border border-rose-700 flex items-center justify-center mb-3">
        <AlertOctagon className="w-5 h-5 text-rose-400" />
      </div>
      <h3 className="text-sm font-semibold text-rose-200">{title}</h3>
      <p className="text-xs text-rose-300/80 max-w-md mt-1 font-mono leading-relaxed">{message}</p>
      {onAction && actionLabel && (
        <button
          onClick={onAction}
          className="mt-4 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-rose-800 bg-rose-900/60 text-xs font-medium text-rose-200 hover:bg-rose-900 transition-colors"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          {actionLabel}
        </button>
      )}
    </div>
  );
};
