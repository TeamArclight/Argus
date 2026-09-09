'use client';

import React, { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Activity, LogOut, AlertCircle, Home, Sparkles, Terminal, RefreshCw } from 'lucide-react';
import { useAuth } from '@/hooks/useAuth';

export const TopNav: React.FC = () => {
  const router = useRouter();
  const { principal, isAuthenticated, isDemoPreview, enableDemoPreview, setToken, loginDevOfficer, logout, error } =
    useAuth();

  const [tokenInputOpen, setTokenInputOpen] = useState(false);
  const [tokenInputValue, setTokenInputValue] = useState('');
  const [tokenSubmitting, setTokenSubmitting] = useState(false);
  const [devEmail, setDevEmail] = useState('demo.procurement@argus.local');
  const [devPassword, setDevPassword] = useState('');
  const [devRole, setDevRole] = useState('PROCUREMENT_OFFICER');
  const [devLoginLoading, setDevLoginLoading] = useState(false);
  const [modalError, setModalError] = useState<string | null>(null);

  const handleConnectToken = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!tokenInputValue.trim()) return;
    try {
      setTokenSubmitting(true);
      await setToken(tokenInputValue.trim());
      setTokenInputOpen(false);
      setTokenInputValue('');
    } catch {
      // Error handled by AuthContext
    } finally {
      setTokenSubmitting(false);
    }
  };

  const handleDevSignInSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!devEmail.trim() || !devPassword) {
      setModalError('Please enter both email and password.');
      return;
    }
    try {
      setDevLoginLoading(true);
      setModalError(null);
      const success = await loginDevOfficer({
        email: devEmail.trim(),
        password: devPassword,
        role: devRole,
      });
      if (success) {
        setTokenInputOpen(false);
        setDevPassword('');
      } else {
        setModalError('Invalid email, password, or unsupported role.');
      }
    } catch (err) {
      setModalError(err instanceof Error ? err.message : 'Sign-in error.');
    } finally {
      setDevLoginLoading(false);
    }
  };

  return (
    <header className="h-16 border-b border-slate-800 bg-slate-950/80 backdrop-blur-md px-6 flex items-center justify-between select-none shrink-0 sticky top-0 z-30">
      {/* State Badge: Live vs Synthetic Demo Data vs Session Required */}
      <div className="flex items-center gap-3">
        {isDemoPreview ? (
          <div className="flex items-center gap-2 px-2.5 py-1 rounded-md bg-amber-950/60 border border-amber-800/80 text-amber-300 text-xs font-mono">
            <span className="w-2 h-2 rounded-full bg-amber-400" />
            <span className="font-semibold tracking-wide">SYNTHETIC DEMO DATA</span>
            <span className="text-amber-400/70 hidden lg:inline">• Non-authoritative preview</span>
            <button
              onClick={() => enableDemoPreview(false)}
              className="ml-2 underline text-amber-200 hover:text-white text-[11px] cursor-pointer"
            >
              Exit Demo
            </button>
          </div>
        ) : isAuthenticated ? (
          <div className="flex items-center gap-2 px-2.5 py-1 rounded-md bg-emerald-950/60 border border-emerald-800/80 text-emerald-300 text-xs font-mono">
            <span className="w-2 h-2 rounded-full bg-emerald-400" />
            <span className="font-semibold tracking-wide">LOCAL DEVELOPMENT SESSION</span>
          </div>
        ) : (
          <div className="flex items-center gap-2 px-2.5 py-1 rounded-md bg-slate-900 border border-slate-800 text-slate-400 text-xs font-mono">
            <AlertCircle className="w-3.5 h-3.5 text-amber-500" />
            <span>Session Required / Token Expired</span>
            <button
              onClick={() => enableDemoPreview(true)}
              className="ml-2 px-2.5 py-0.5 rounded bg-indigo-600 hover:bg-indigo-500 text-white text-[11px] font-sans font-medium inline-flex items-center gap-1 transition-colors cursor-pointer"
            >
              <Sparkles className="w-3 h-3" />
              <span>Open Demo Workspace</span>
            </button>
          </div>
        )}
      </div>

      {/* User Session & Status Controls */}
      <div className="flex items-center gap-3">
        <Link
          href="/"
          className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-slate-900 border border-slate-800 text-slate-300 hover:text-white text-xs font-mono transition-colors"
          title="Return to Public Landing Page"
        >
          <Home className="w-3.5 h-3.5 text-slate-400" />
          <span>Landing Page</span>
        </Link>

        <Link
          href="/workspace/status"
          className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-slate-900 border border-slate-800 text-slate-300 hover:text-white text-xs font-mono transition-colors"
        >
          <Activity className="w-3.5 h-3.5 text-indigo-400" />
          <span>Status</span>
        </Link>

        {isAuthenticated && principal ? (
          <div className="flex items-center gap-3 pl-3 border-l border-slate-800">
            <div className="text-right hidden sm:block">
              <p className="text-xs font-semibold text-slate-200 leading-tight">
                {principal.name || 'Officer'}
              </p>
              <div className="flex items-center gap-1 justify-end">
                <span className="text-[10px] font-mono font-semibold text-indigo-400 bg-indigo-950/80 px-1.5 py-0.5 rounded border border-indigo-800/60 leading-tight">
                  {principal.role}
                </span>
              </div>
            </div>
            <button
              onClick={() => {
                logout();
                setTokenInputOpen(true);
              }}
              title="Switch Development Role"
              className="flex items-center gap-1 px-2.5 py-1 rounded-md border border-indigo-500/40 bg-indigo-950/60 text-indigo-200 hover:bg-indigo-900/80 hover:text-white text-xs font-mono transition-colors cursor-pointer"
            >
              <RefreshCw className="w-3 h-3" />
              <span>Switch Role</span>
            </button>
            <button
              onClick={() => {
                logout();
                router.push('/');
              }}
              title="Sign Out"
              className="p-1.5 rounded-lg border border-slate-800 bg-slate-900 text-slate-400 hover:text-rose-400 transition-colors cursor-pointer"
            >
              <LogOut className="w-4 h-4" />
            </button>
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <button
              onClick={() => setTokenInputOpen(true)}
              className="inline-flex items-center gap-1.5 px-3 py-1 rounded-md bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-mono transition-colors cursor-pointer"
            >
              <Sparkles className="w-3.5 h-3.5" />
              <span>Sign In</span>
            </button>
            <button
              onClick={() => setTokenInputOpen(true)}
              className="inline-flex items-center gap-1.5 px-3 py-1 rounded-md bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white border border-slate-700 text-xs font-mono transition-colors cursor-pointer"
              title="Advanced: Enter Authorized Bearer Token"
            >
              <Terminal className="w-3.5 h-3.5 text-slate-400" />
              <span>Authorized Access</span>
            </button>
          </div>
        )}
      </div>

      {/* Connect Development Token Modal */}
      {tokenInputOpen && (
        <div className="absolute top-16 right-6 z-50 w-96 p-4 rounded-xl bg-slate-900 border border-slate-800 shadow-2xl space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1.5">
              <Terminal className="w-3.5 h-3.5 text-indigo-400" />
              <h4 className="text-xs font-bold text-white font-mono">Workspace Authentication</h4>
            </div>
            <button
              onClick={() => {
                setTokenInputOpen(false);
                setModalError(null);
              }}
              className="text-slate-500 hover:text-slate-300 text-xs cursor-pointer"
            >
              ✕
            </button>
          </div>

          <form onSubmit={handleDevSignInSubmit} className="p-3 rounded-lg bg-indigo-950/40 border border-indigo-800/60 space-y-2.5">
            <div className="flex items-center justify-between">
              <span className="text-[11px] text-indigo-200 font-semibold font-mono">ARGUS Local Development Sign In</span>
              <button
                type="button"
                onClick={() => {
                  setDevEmail('demo.procurement@argus.local');
                  setDevPassword('ArgusDemo2026!');
                  setModalError(null);
                }}
                className="text-[10px] text-indigo-400 hover:text-indigo-300 underline cursor-pointer"
              >
                Fill Dev Account
              </button>
            </div>

            <div>
              <label className="block text-[10px] font-mono text-slate-400 mb-0.5">Email Address</label>
              <input
                type="email"
                required
                value={devEmail}
                onChange={(e) => setDevEmail(e.target.value)}
                placeholder="demo.procurement@argus.local"
                className="w-full p-1.5 bg-slate-950 border border-slate-800 rounded text-xs text-slate-200 font-mono focus:outline-none focus:border-indigo-500"
              />
            </div>

            <div>
              <label className="block text-[10px] font-mono text-slate-400 mb-0.5">Password</label>
              <input
                type="password"
                required
                value={devPassword}
                onChange={(e) => setDevPassword(e.target.value)}
                placeholder="••••••••••••"
                className="w-full p-1.5 bg-slate-950 border border-slate-800 rounded text-xs text-slate-200 font-mono focus:outline-none focus:border-indigo-500"
              />
            </div>

            <div>
              <label className="block text-[10px] font-mono text-slate-400 mb-0.5">Workspace Role</label>
              <select
                value={devRole}
                onChange={(e) => setDevRole(e.target.value)}
                className="w-full p-1.5 bg-slate-950 border border-slate-800 rounded text-xs text-slate-200 font-mono focus:outline-none focus:border-indigo-500"
              >
                <option value="PROCUREMENT_OFFICER">Procurement Officer</option>
                <option value="REVIEWER">Reviewer</option>
                <option value="AUDITOR">Auditor</option>
                <option value="ADMIN">Administrator</option>
              </select>
            </div>

            {modalError && (
              <p className="text-[10px] text-rose-400 font-mono leading-tight">{modalError}</p>
            )}

            <button
              type="submit"
              disabled={devLoginLoading}
              className="w-full py-1.5 rounded-md bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-medium transition-colors cursor-pointer disabled:opacity-50"
            >
              {devLoginLoading ? 'Validating credentials...' : 'Sign In'}
            </button>
          </form>

          <div className="border-t border-slate-800 pt-2 space-y-2">
            <p className="text-[11px] text-slate-400 font-medium">
              Or Connect Custom Bearer Token:
            </p>
            <form onSubmit={handleConnectToken} className="space-y-2">
              <input
                type="password"
                placeholder="Paste Bearer Token..."
                value={tokenInputValue}
                onChange={(e) => setTokenInputValue(e.target.value)}
                className="w-full p-2 bg-slate-950 border border-slate-800 rounded-lg text-xs text-slate-200 font-mono focus:outline-none focus:border-indigo-500"
              />
              {error && <p className="text-[11px] text-rose-400 font-mono leading-tight">{error}</p>}
              <div className="flex items-center justify-between pt-1">
                <button
                  type="button"
                  onClick={() => {
                    enableDemoPreview(true);
                    setTokenInputOpen(false);
                  }}
                  className="text-[11px] text-indigo-400 hover:text-indigo-300 underline cursor-pointer"
                >
                  Open Demo Workspace
                </button>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setTokenInputOpen(false)}
                    className="px-2.5 py-1 text-xs text-slate-400 hover:text-white cursor-pointer"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={tokenSubmitting}
                    className="px-3 py-1 bg-slate-700 hover:bg-slate-600 text-white text-xs rounded-lg font-medium cursor-pointer"
                  >
                    {tokenSubmitting ? 'Verifying...' : 'Verify Token'}
                  </button>
                </div>
              </div>
            </form>
          </div>
        </div>
      )}
    </header>
  );
};
