import React from 'react';
import { Loader2 } from 'lucide-react';

interface LoadingStateProps {
  message?: string;
}

export const LoadingState: React.FC<LoadingStateProps> = ({
  message = 'Loading audit-ready data ledger...',
}) => {
  return (
    <div className="flex flex-col items-center justify-center p-12 min-h-[320px] rounded-xl border border-slate-800 bg-slate-900/40 text-center">
      <Loader2 className="w-8 h-8 text-indigo-400 animate-spin mb-3" />
      <p className="text-xs font-mono text-slate-300">{message}</p>
      <p className="text-[11px] font-mono text-slate-500 mt-1">Verifying cryptographic integrity & live services</p>
    </div>
  );
};
