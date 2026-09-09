'use client';

import React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  ShieldCheck,
  LayoutDashboard,
  FileText,
  Activity,
  History,
  Info,
} from 'lucide-react';

const NAV_ITEMS = [
  { href: '/workspace', label: 'Executive Overview', icon: LayoutDashboard, exact: true },
  { href: '/workspace/tenders', label: 'Procurement Tenders', icon: FileText },
  { href: '/workspace/audit', label: 'Audit Trail Ledger', icon: History },
  { href: '/workspace/status', label: 'System Telemetry', icon: Activity },
];

export const Sidebar: React.FC = () => {
  const pathname = usePathname();

  return (
    <aside className="w-64 border-r border-slate-800 bg-slate-950 flex flex-col h-screen select-none shrink-0">
      {/* Brand Header */}
      <div className="h-16 border-b border-slate-800 px-6 flex items-center justify-between">
        <Link href="/workspace" className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-indigo-600/20 border border-indigo-500/40 flex items-center justify-center">
            <ShieldCheck className="w-4 h-4 text-indigo-400" />
          </div>
          <div>
            <span className="font-bold text-sm tracking-tight text-white">ARGUS</span>
            <span className="text-[10px] block font-mono text-indigo-400 leading-none">PROCUREMENT</span>
          </div>
        </Link>
      </div>

      {/* Navigation Links */}
      <div className="flex-1 py-6 px-3 space-y-1 overflow-y-auto">
        <div className="px-3 pb-2 text-[10px] font-mono uppercase tracking-wider text-slate-500 font-semibold">
          Procurement Operations
        </div>

        {NAV_ITEMS.map((item) => {
          const isActive = item.exact
            ? pathname === item.href
            : pathname.startsWith(item.href);

          const Icon = item.icon;

          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center gap-3 px-3 py-2 rounded-lg text-xs font-medium transition-all ${
                isActive
                  ? 'bg-indigo-600/20 border border-indigo-500/40 text-indigo-300 font-semibold shadow-sm'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900 border border-transparent'
              }`}
            >
              <Icon className={`w-4 h-4 ${isActive ? 'text-indigo-400' : 'text-slate-500'}`} />
              <span>{item.label}</span>
            </Link>
          );
        })}

        <div className="pt-4 border-t border-slate-900 mt-4">
          <Link
            href="/"
            className="flex items-center gap-3 px-3 py-2 rounded-lg text-xs font-medium text-slate-400 hover:text-slate-200 hover:bg-slate-900 border border-transparent transition-all"
          >
            <ShieldCheck className="w-4 h-4 text-slate-500" />
            <span>Public Showcase</span>
          </Link>
        </div>
      </div>

      {/* Operational Principles Footer */}
      <div className="p-4 border-t border-slate-800/80 bg-slate-900/30 space-y-2 text-[11px] font-mono">
        <div className="flex items-center gap-2 text-slate-400">
          <Info className="w-3.5 h-3.5 text-indigo-400" />
          <span className="font-semibold text-slate-300">Core Principles</span>
        </div>
        <p className="text-slate-500 text-[10px] leading-tight">
          AI assists extraction, deterministic rules evaluate criteria, evidence explains, and human officers make final decisions.
        </p>
      </div>
    </aside>
  );
};
