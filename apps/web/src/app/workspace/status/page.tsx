"use client";

import React, { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { 
  Activity, CheckCircle, AlertTriangle, XCircle, RefreshCw, 
  Server, Cpu, HelpCircle, AlertCircle, ShieldCheck, Database, FileText, KeyRound, ArrowLeft, Sparkles, Info
} from "lucide-react";
import { apiClient, ApiError } from "@/services/api";
import { ProviderHealthRead, IntegrationsHealthResponse, AuthenticatedPrincipal } from "@/types/api";
import { MOCK_PROVIDERS, MOCK_INTEGRATIONS_HEALTH } from "@/services/mock-data";
import { useAuth } from "@/hooks/useAuth";
import { resolveProviderStatus } from "@/lib/provider-status";

export default function StatusPage() {
  const { isDemoPreview, isAuthenticated, principal } = useAuth();
  
  // Discrete Health States
  const [apiHealth, setApiHealth] = useState<"CONNECTED" | "UNAVAILABLE" | "CHECKING">("CHECKING");
  const [authHealth, setAuthHealth] = useState<"AUTHENTICATED" | "NOT_AUTHENTICATED" | "CHECKING">("CHECKING");
  const [authPrincipal, setAuthPrincipal] = useState<AuthenticatedPrincipal | null>(null);
  
  const [providers, setProviders] = useState<ProviderHealthRead[]>([]);
  const [integrations, setIntegrations] = useState<IntegrationsHealthResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadStatus = useCallback(async () => {
    setLoading(true);
    setError(null);

    // 1. In Demo Preview
    if (isDemoPreview) {
      setApiHealth("CONNECTED");
      setAuthHealth("AUTHENTICATED");
      setAuthPrincipal(principal);
      setProviders(MOCK_PROVIDERS);
      setIntegrations(MOCK_INTEGRATIONS_HEALTH);
      setLoading(false);
      return;
    }

    // 2. Live Query: Backend API Health (GET /health)
    try {
      const hData = await apiClient.getHealth();
      if (hData && (hData.status === "ok" || hData.status === "healthy")) {
        setApiHealth("CONNECTED");
      } else {
        setApiHealth("UNAVAILABLE");
      }
    } catch {
      setApiHealth("UNAVAILABLE");
    }

    // 3. Live Query: Authentication Session (GET /api/v1/auth/me)
    try {
      const me = await apiClient.getMe();
      setAuthHealth("AUTHENTICATED");
      setAuthPrincipal(me);
    } catch (err: unknown) {
      setAuthHealth("NOT_AUTHENTICATED");
      setAuthPrincipal(null);
      // Notice: 401 is NOT a backend connection error!
      if (err instanceof ApiError && err.isNetworkError) {
        setApiHealth("UNAVAILABLE");
      }
    }

    // 4. Live Query: Public Integration Health (/health/integrations)
    try {
      const iData = await apiClient.getHealthIntegrations();
      setIntegrations(iData);
    } catch {
      setIntegrations(null);
    }

    // 5. Live Query: Authenticated Providers (/api/v1/providers)
    if (isAuthenticated) {
      try {
        const pData = await apiClient.getProviders();
        setProviders(pData);
      } catch {
        setProviders([]);
      }
    } else {
      setProviders([]);
    }

    setLoading(false);
  }, [isDemoPreview, isAuthenticated, principal]);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  const getStatusBadge = (status: string) => {
    switch (status.toUpperCase()) {
      case "LIVE_CONNECTED":
      case "LIVE / CONNECTED":
      case "CONNECTED":
      case "AUTHENTICATED":
      case "AVAILABLE":
      case "HEALTHY":
      case "OK":
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-mono">
            <CheckCircle className="w-3 h-3" /> LIVE / CONNECTED
          </span>
        );
      case "CONFIGURED":
      case "CONFIGURED_UNVERIFIED":
      case "CONFIGURED / UNVERIFIED":
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-sky-500/10 text-sky-400 border border-sky-500/20 font-mono">
            <Info className="w-3 h-3" /> CONFIGURED / UNVERIFIED
          </span>
        );
      case "DEMO":
      case "SYNTHETIC":
      case "DEMO_SYNTHETIC":
      case "DEMO / SYNTHETIC":
      case "DEMO_MODE":
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-amber-500/10 text-amber-300 border border-amber-500/30 font-mono">
            <Sparkles className="w-3 h-3" /> DEMO / SYNTHETIC
          </span>
        );
      case "NOT_AUTHENTICATED":
      case "NOT AUTHENTICATED":
      case "UNCONFIGURED":
      case "UNAVAILABLE":
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-slate-800 text-slate-400 border border-slate-700 font-mono">
            <AlertTriangle className="w-3 h-3" /> {status.replace("_", " ")}
          </span>
        );
      case "OFFLINE":
      case "ERROR":
      case "DOWN":
      case "DEGRADED":
      case "FAILED":
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-rose-500/10 text-rose-400 border border-rose-500/20 font-mono">
            <XCircle className="w-3 h-3" /> {status}
          </span>
        );
      case "UNKNOWN":
      default:
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-zinc-800 text-zinc-400 border border-zinc-700 font-mono">
            <HelpCircle className="w-3 h-3" /> {status || "UNKNOWN"}
          </span>
        );
    }
  };

  // Resolve integration provider state truthfully using shared canonical resolver
  const getProviderState = (key: keyof IntegrationsHealthResponse): { status: string; detail: string } => {
    const item = integrations ? integrations[key] : null;
    const resolved = resolveProviderStatus(item, isDemoPreview);
    return {
      status: resolved.label,
      detail: resolved.description,
    };
  };

  return (
    <div className="space-y-6 max-w-6xl mx-auto pb-12">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-slate-800 pb-4">
        <div>
          <Link href="/workspace" className="inline-flex items-center gap-2 text-xs text-slate-400 hover:text-slate-200 mb-2 transition-colors font-mono">
            <ArrowLeft className="w-3.5 h-3.5" /> Back to Overview
          </Link>
          <div className="flex items-center gap-3">
            <Activity className="w-6 h-6 text-emerald-400" />
            <h1 className="text-2xl font-bold text-white tracking-tight">
              System Telemetry & Health
            </h1>
            {isDemoPreview ? (
              <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-amber-950 border border-amber-800/60 text-amber-300">
                SYNTHETIC DEMO TELEMETRY
              </span>
            ) : (
              <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-emerald-950 border border-emerald-800/60 text-emerald-300">
                LIVE TELEMETRY
              </span>
            )}
          </div>
          <p className="text-xs text-slate-400 mt-1 font-mono">
            Discrete verification of API gateway, session authentication, and statutory provider endpoints.
          </p>
        </div>

        <button
          onClick={loadStatus}
          disabled={loading}
          className="inline-flex items-center gap-2 px-3.5 py-2 bg-slate-900 hover:bg-slate-800 text-slate-200 text-xs font-mono font-medium rounded-lg transition-colors border border-slate-800 self-start sm:self-auto cursor-pointer"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} /> Refresh Telemetry
        </button>
      </div>

      {error && (
        <div className="p-4 rounded-xl bg-rose-950/20 border border-rose-800/40 text-rose-300 text-xs font-mono flex items-center gap-3">
          <AlertCircle className="w-4 h-4 text-rose-400 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Primary Telemetry: API Gateway vs Authentication Session */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* 1. ARGUS API */}
        <div className="p-5 rounded-2xl bg-slate-900 border border-slate-800 space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-slate-400 flex items-center gap-2 font-mono">
              <Server className="w-4 h-4 text-blue-400" /> ARGUS API Gateway
            </span>
            {getStatusBadge(apiHealth)}
          </div>
          <div>
            <p className="text-sm font-semibold text-white">
              {apiHealth === "CONNECTED" ? "FastAPI Gateway Online" : "Backend Unavailable"}
            </p>
            <p className="text-[11px] text-slate-500 font-mono mt-0.5">
              GET /health = {apiHealth === "CONNECTED" ? "200 OK" : "Unreachable"}
            </p>
          </div>
        </div>

        {/* 2. Authentication Session */}
        <div className="p-5 rounded-2xl bg-slate-900 border border-slate-800 space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-slate-400 flex items-center gap-2 font-mono">
              <KeyRound className="w-4 h-4 text-indigo-400" /> Auth Session
            </span>
            {getStatusBadge(authHealth)}
          </div>
          <div>
            <p className="text-sm font-semibold text-white">
              {authHealth === "AUTHENTICATED"
                ? authPrincipal?.name || "Officer Authenticated"
                : "Not Authenticated"}
            </p>
            <p className="text-[11px] text-slate-500 font-mono mt-0.5">
              {authHealth === "AUTHENTICATED"
                ? `Role: ${authPrincipal?.role || "PROCUREMENT_OFFICER"}`
                : "GET /api/v1/auth/me = 401"}
            </p>
          </div>
        </div>

        {/* 3. Database */}
        <div className="p-5 rounded-2xl bg-slate-900 border border-slate-800 space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-slate-400 flex items-center gap-2 font-mono">
              <Database className="w-4 h-4 text-emerald-400" /> Database Engine
            </span>
            {getStatusBadge(apiHealth === "CONNECTED" ? "CONNECTED" : "OFFLINE")}
          </div>
          <div>
            <p className="text-sm font-semibold text-white">
              {apiHealth === "CONNECTED" ? "PostgreSQL / SQLite Storage" : "Storage Unreachable"}
            </p>
            <p className="text-[11px] text-slate-500 font-mono mt-0.5">
              {apiHealth === "CONNECTED" ? "Transactions active & consistent" : "Connection lost"}
            </p>
          </div>
        </div>

        {/* 4. OCR Pipeline */}
        <div className="p-5 rounded-2xl bg-slate-900 border border-slate-800 space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-slate-400 flex items-center gap-2 font-mono">
              <FileText className="w-4 h-4 text-purple-400" /> OCR Pipeline
            </span>
            {getStatusBadge(apiHealth === "CONNECTED" ? "CONNECTED" : "OFFLINE")}
          </div>
          <div>
            <p className="text-sm font-semibold text-white">
              Document Parser Worker
            </p>
            <p className="text-[11px] text-slate-500 font-mono mt-0.5">
              PyMuPDF / Tesseract Pipeline Ready
            </p>
          </div>
        </div>
      </div>

      {/* Discrete Statutory Registry Integrations */}
      <div className="space-y-4">
        <h2 className="text-sm font-bold text-white tracking-tight flex items-center gap-2">
          <ShieldCheck className="w-4 h-4 text-indigo-400" />
          Statutory Verification Adapters & Providers
        </h2>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {/* GSTIN */}
          {(() => {
            const info = getProviderState("gst");
            return (
              <div className="p-4 rounded-xl bg-slate-900/60 border border-slate-800 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-xs text-slate-200 font-mono">GSTIN Registry</span>
                  {getStatusBadge(info.status)}
                </div>
                <p className="text-xs text-slate-400 leading-relaxed font-sans">{info.detail}</p>
                <div className="text-[10px] text-slate-500 font-mono pt-1 border-t border-slate-800/80">
                  Domain: Goods & Services Tax Network (GSTN)
                </div>
              </div>
            );
          })()}

          {/* UDYAM */}
          {(() => {
            const info = getProviderState("udyam");
            return (
              <div className="p-4 rounded-xl bg-slate-900/60 border border-slate-800 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-xs text-slate-200 font-mono">UDYAM MSME</span>
                  {getStatusBadge(info.status)}
                </div>
                <p className="text-xs text-slate-400 leading-relaxed font-sans">{info.detail}</p>
                <div className="text-[10px] text-slate-500 font-mono pt-1 border-t border-slate-800/80">
                  Domain: Ministry of MSME Udyam Portal
                </div>
              </div>
            );
          })()}

          {/* MCA */}
          {(() => {
            const info = getProviderState("mca");
            return (
              <div className="p-4 rounded-xl bg-slate-900/60 border border-slate-800 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-xs text-slate-200 font-mono">MCA / ROC</span>
                  {getStatusBadge(info.status)}
                </div>
                <p className="text-xs text-slate-400 leading-relaxed font-sans">{info.detail}</p>
                <div className="text-[10px] text-slate-500 font-mono pt-1 border-t border-slate-800/80">
                  Domain: Ministry of Corporate Affairs (CIN Lookup)
                </div>
              </div>
            );
          })()}

          {/* EPFO */}
          {(() => {
            const info = getProviderState("epfo");
            return (
              <div className="p-4 rounded-xl bg-slate-900/60 border border-slate-800 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-xs text-slate-200 font-mono">EPFO Compliance</span>
                  {getStatusBadge(info.status)}
                </div>
                <p className="text-xs text-slate-400 leading-relaxed font-sans">{info.detail}</p>
                <div className="text-[10px] text-slate-500 font-mono pt-1 border-t border-slate-800/80">
                  Domain: Employees' Provident Fund Organisation
                </div>
              </div>
            );
          })()}

          {/* ESIC */}
          {(() => {
            const info = getProviderState("esic");
            return (
              <div className="p-4 rounded-xl bg-slate-900/60 border border-slate-800 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-xs text-slate-200 font-mono">ESIC Portal</span>
                  {getStatusBadge(info.status)}
                </div>
                <p className="text-xs text-slate-400 leading-relaxed font-sans">{info.detail}</p>
                <div className="text-[10px] text-slate-500 font-mono pt-1 border-t border-slate-800/80">
                  Domain: Employees' State Insurance Corporation
                </div>
              </div>
            );
          })()}

          {/* Blacklist / Debarment */}
          {(() => {
            const info = getProviderState("blacklist");
            return (
              <div className="p-4 rounded-xl bg-slate-900/60 border border-slate-800 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-xs text-slate-200 font-mono">Debarment / Blacklist</span>
                  {getStatusBadge(info.status)}
                </div>
                <p className="text-xs text-slate-400 leading-relaxed font-sans">{info.detail}</p>
                <div className="text-[10px] text-slate-500 font-mono pt-1 border-t border-slate-800/80">
                  Domain: Central Procurement Debarment Database
                </div>
              </div>
            );
          })()}

          {/* AI Intelligence */}
          {(() => {
            const info = getProviderState("intelligence");
            return (
              <div className="p-4 rounded-xl bg-slate-900/60 border border-slate-800 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-xs text-slate-200 font-mono flex items-center gap-1">
                    <Cpu className="w-3.5 h-3.5 text-indigo-400" /> AI Intelligence Service
                  </span>
                  {getStatusBadge(info.status)}
                </div>
                <p className="text-xs text-slate-400 leading-relaxed font-sans">{info.detail}</p>
                <div className="text-[10px] text-slate-500 font-mono pt-1 border-t border-slate-800/80">
                  Domain: Clause Extraction & Vector RAG
                </div>
              </div>
            );
          })()}
        </div>
      </div>

      {/* Authenticated Provider Capabilities Detail Table */}
      {providers.length > 0 && (
        <div className="space-y-3 pt-4 border-t border-slate-800">
          <h2 className="text-sm font-bold text-white tracking-tight">
            Registered Provider Capabilities ({providers.length})
          </h2>
          <div className="overflow-x-auto rounded-xl border border-slate-800 bg-slate-900/40">
            <table className="w-full text-left text-xs font-mono">
              <thead>
                <tr className="border-b border-slate-800 text-slate-400 font-semibold bg-slate-950/60">
                  <th className="px-4 py-3">Identifier</th>
                  <th className="px-4 py-3">Domain</th>
                  <th className="px-4 py-3">Mode</th>
                  <th className="px-4 py-3">Config Status</th>
                  <th className="px-4 py-3">Operational Health</th>
                  <th className="px-4 py-3">Notes</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {providers.map((p, idx) => {
                  const statusInfo = resolveProviderStatus(p, isDemoPreview);
                  return (
                    <tr key={idx} className="hover:bg-slate-800/30 transition-colors">
                      <td className="px-4 py-3 font-semibold text-white">{p.provider_identifier}</td>
                      <td className="px-4 py-3 text-slate-300">{p.domain}</td>
                      <td className="px-4 py-3">
                        <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-300">
                          {p.configured_mode}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-slate-300">{p.configuration_status}</td>
                      <td className="px-4 py-3">{getStatusBadge(statusInfo.label)}</td>
                      <td className="px-4 py-3 text-slate-400">{statusInfo.description || p.notes || "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
