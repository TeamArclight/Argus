"use client";

import React, { useState, useEffect, useCallback } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { 
  ShieldCheck, Printer, Download, ArrowLeft, RefreshCw, AlertCircle
} from "lucide-react";
import { api } from "@/services/api";
import { demoStore } from "@/services/demo-store";
import { ReportRead } from "@/services/types";
import { SessionRequired } from "@/components/ui/SessionRequired";
import { useAuth } from "@/hooks/useAuth";

export default function ReportPage() {
  const params = useParams();
  const bidderId = params?.bidderId as string;
  const { isAuthenticated, isDemoPreview } = useAuth();
  const storedDemoBidder = bidderId ? demoStore.getBidder(bidderId) : null;
  const isDemo = isDemoPreview || Boolean(storedDemoBidder);

  const [report, setReport] = useState<ReportRead | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadReport = useCallback(async () => {
    if (!bidderId) return;
    setLoading(true);
    setError(null);

    if (isDemo) {
      const demoReport = demoStore.getReport(bidderId);
      if (!demoReport) {
        setReport(null);
        setError("Bidder Report Not Found");
        setLoading(false);
        return;
      }
      setReport(demoReport);
      setLoading(false);
      return;
    }

    if (!isAuthenticated) {
      setLoading(false);
      return;
    }

    try {
      const data = await api.getReport(bidderId);
      setReport(data);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load audit report.");
    } finally {
      setLoading(false);
    }
  }, [bidderId, isDemo, isAuthenticated]);

  useEffect(() => {
    loadReport();
  }, [loadReport]);

  const handlePrint = () => {
    window.print();
  };

  const handleExportJson = () => {
    if (!report) return;
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `argus-evaluation-report-${bidderId}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  if (!isAuthenticated && !isDemo) {
    return (
      <SessionRequired
        title="Session Required"
        description="To generate and export audit-ready procurement evaluation reports, connect an authorized Bearer token or explore in the Demo Workspace."
      />
    );
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="flex flex-col items-center gap-3">
          <RefreshCw className="w-8 h-8 text-blue-500 animate-spin" />
          <p className="text-zinc-400 text-sm">Generating authoritative evaluation report...</p>
        </div>
      </div>
    );
  }

  if (error && !report) {
    return (
      <div className="max-w-4xl mx-auto p-6">
        <div className="p-6 rounded-xl bg-rose-950/20 border border-rose-800/40 text-rose-300">
          <div className="flex items-center gap-3 mb-2">
            <AlertCircle className="w-6 h-6 text-rose-400" />
            <h2 className="text-lg font-semibold">Report Generation Incomplete</h2>
          </div>
          <p className="text-sm text-zinc-400 mb-4">{error}</p>
          <div className="flex gap-3">
            <button onClick={loadReport} className="px-4 py-2 bg-rose-600 hover:bg-rose-500 text-white rounded-lg text-sm font-medium">
              Retry Generation
            </button>
            <Link href={`/workspace/bidders/${bidderId}`} className="px-4 py-2 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg text-sm font-medium">
              Back to Bidder
            </Link>
          </div>
        </div>
      </div>
    );
  }

  const verifications = report?.verification_results ?? [];
  const matrixRows = report?.compliance_matrix?.rows ?? [];

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      {/* Action Bar (Hidden on print) */}
      <div className="print:hidden flex flex-col md:flex-row md:items-center justify-between gap-4">
        <Link href={`/workspace/bidders/${bidderId}`} className="inline-flex items-center gap-2 text-sm text-zinc-400 hover:text-zinc-200 transition-colors">
          <ArrowLeft className="w-4 h-4" /> Back to Bidder Evaluation
        </Link>
        <div className="flex items-center gap-3">
          <button
            onClick={handleExportJson}
            className="inline-flex items-center gap-2 px-3.5 py-2 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-sm font-medium rounded-lg transition-colors border border-zinc-700"
          >
            <Download className="w-4 h-4" /> Export JSON
          </button>
          <button
            onClick={handlePrint}
            className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded-lg transition-colors shadow-sm"
          >
            <Printer className="w-4 h-4" /> Print / Save PDF
          </button>
        </div>
      </div>

      {/* Bidder Sub-Navigation Tabs */}
      <div className="print:hidden border-b border-zinc-800 flex gap-4 text-xs font-mono">
        <Link
          href={`/workspace/bidders/${bidderId}/matrix`}
          className="pb-2.5 font-medium border-b-2 border-transparent text-zinc-400 hover:text-zinc-200 flex items-center gap-1.5"
        >
          Compliance Matrix
        </Link>
        <Link
          href={`/workspace/bidders/${bidderId}/review`}
          className="pb-2.5 font-medium border-b-2 border-transparent text-zinc-400 hover:text-zinc-200 flex items-center gap-1.5"
        >
          Human Officer Review
        </Link>
        <Link
          href={`/workspace/bidders/${bidderId}/report`}
          className="pb-2.5 font-semibold border-b-2 border-emerald-500 text-emerald-400 flex items-center gap-1.5"
        >
          <ShieldCheck className="w-3.5 h-3.5" /> Audit Report
        </Link>
      </div>

      {/* Printable Report Document Container */}
      <div className="bg-zinc-950 border border-zinc-800 rounded-2xl p-8 sm:p-12 space-y-8 text-zinc-100 shadow-xl print:border-none print:p-0 print:bg-white print:text-black">
        {/* Document Header */}
        <div className="border-b border-zinc-800 print:border-black/20 pb-6 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2.5 mb-1">
              <ShieldCheck className="w-6 h-6 text-blue-400 print:text-blue-700" />
              <span className="text-xs uppercase tracking-widest font-bold text-blue-400 print:text-blue-700">ARGUS PROCUREMENT INTELLIGENCE</span>
            </div>
            <h1 className="text-2xl font-bold tracking-tight">Statutory Compliance & Evaluation Report</h1>
            <p className="text-xs text-zinc-400 print:text-zinc-600 mt-1">
              Authoritative record generated from verified external registry feeds and document OCR pipelines.
            </p>
          </div>
          <div className="text-right sm:text-right font-mono text-xs text-zinc-400 print:text-zinc-600">
            <div>Generated: {report?.generated_at ? new Date(report.generated_at).toUTCString() : "—"}</div>
            <div>Audit Trail: {report?.audit_trail_count ?? 0} events</div>
          </div>
        </div>

        {/* Tender & Bidder Identification Table */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div className="p-4 rounded-xl bg-zinc-900/50 print:bg-zinc-100 border border-zinc-800/80 print:border-zinc-300 space-y-2">
            <h2 className="text-xs font-bold uppercase tracking-wider text-zinc-400 print:text-zinc-600">Tender Information</h2>
            <div className="text-sm font-semibold">{report?.tender.title}</div>
            <div className="text-xs font-mono text-zinc-400 print:text-zinc-700">Ref: {report?.tender.tender_number}</div>
            <div className="text-xs text-zinc-400 print:text-zinc-700">Procuring Authority: {report?.tender.authority || "Not specified"}</div>
          </div>

          <div className="p-4 rounded-xl bg-zinc-900/50 print:bg-zinc-100 border border-zinc-800/80 print:border-zinc-300 space-y-2">
            <h2 className="text-xs font-bold uppercase tracking-wider text-zinc-400 print:text-zinc-600">Bidder Entity</h2>
            <div className="text-sm font-semibold">{report?.bidder.bidder_name}</div>
            <div className="text-xs font-mono text-zinc-400 print:text-zinc-700">
              GSTIN: {report?.bidder.gstin || "N/A"} | CIN: {report?.bidder.cin || "N/A"}
            </div>
            <div className="text-xs font-mono text-zinc-400 print:text-zinc-700">
              PAN: {report?.bidder.pan || "N/A"} | UDYAM: {report?.bidder.udyam_number || "N/A"}
            </div>
          </div>
        </div>

        {/* Human Officer Decision (Authoritative) */}
        {report?.human_decision && (
          <div className="p-5 rounded-xl bg-blue-950/20 print:bg-blue-50 border border-blue-800/40 print:border-blue-300 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold uppercase tracking-wider text-blue-400 print:text-blue-800">Authoritative Officer Determination</span>
              <span className="text-xs font-mono text-zinc-400 print:text-zinc-600">{new Date(report.human_decision.decided_at).toLocaleString()}</span>
            </div>
            <div className="flex items-center gap-3 text-lg font-bold">
              <span className={report.human_decision.status === "QUALIFIED" ? "text-emerald-400 print:text-emerald-700" : "text-rose-400 print:text-rose-700"}>
                {report.human_decision.status}
              </span>
              <span className="text-sm font-normal text-zinc-400 print:text-zinc-700 font-mono">
                ({report.human_decision.reason_code})
              </span>
            </div>
            {report.human_decision.remarks && (
              <p className="text-xs text-zinc-300 print:text-zinc-800 bg-zinc-950/40 print:bg-white p-2.5 rounded border border-zinc-800/50 print:border-zinc-200">
                {report.human_decision.remarks}
              </p>
            )}
            <div className="text-xs font-mono text-zinc-500 print:text-zinc-600">
              Recorded By Officer: {report.human_decision.officer_name || report.human_decision.officer_id}
            </div>
          </div>
        )}

        {/* Statutory Registry Verification Table */}
        <div className="space-y-3">
          <h2 className="text-sm font-bold uppercase tracking-wider text-zinc-300 print:text-black">1. External Registry Verification Results</h2>
          <div className="overflow-x-auto rounded-xl border border-zinc-800 print:border-zinc-300">
            <table className="w-full text-left text-xs">
              <thead className="bg-zinc-900/60 print:bg-zinc-100 text-zinc-400 print:text-zinc-700 font-semibold border-b border-zinc-800 print:border-zinc-300">
                <tr>
                  <th className="px-4 py-2.5">Field</th>
                  <th className="px-4 py-2.5">Provenance Source</th>
                  <th className="px-4 py-2.5">Status</th>
                  <th className="px-4 py-2.5">Claimed vs Verified</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-800 print:divide-zinc-200">
                {verifications.map((v, i) => (
                  <tr key={v.id || i}>
                    <td className="px-4 py-2.5 font-medium font-mono">{v.field}</td>
                    <td className="px-4 py-2.5 font-mono">{v.source}</td>
                    <td className="px-4 py-2.5 font-bold">
                      <span className={v.status === "VERIFIED" ? "text-emerald-400 print:text-emerald-700" : "text-amber-400 print:text-amber-700"}>
                        {v.status}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-zinc-400 print:text-zinc-700">
                      Claimed: {String(v.claimed_value ?? "—")} | Verified: {String(v.verified_value ?? "—")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Compliance Evaluation Matrix Table */}
        <div className="space-y-3">
          <h2 className="text-sm font-bold uppercase tracking-wider text-zinc-300 print:text-black">2. Clause Compliance Matrix</h2>
          <div className="overflow-x-auto rounded-xl border border-zinc-800 print:border-zinc-300">
            <table className="w-full text-left text-xs">
              <thead className="bg-zinc-900/60 print:bg-zinc-100 text-zinc-400 print:text-zinc-700 font-semibold border-b border-zinc-800 print:border-zinc-300">
                <tr>
                  <th className="px-4 py-2.5">Clause Reference</th>
                  <th className="px-4 py-2.5">Type</th>
                  <th className="px-4 py-2.5">Expected Condition</th>
                  <th className="px-4 py-2.5">Observed Value</th>
                  <th className="px-4 py-2.5">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-800 print:divide-zinc-200">
                {matrixRows.map((m, i) => (
                  <tr key={m.requirement_id || i}>
                    <td className="px-4 py-2.5 font-medium">{m.clause}</td>
                    <td className="px-4 py-2.5 font-mono">{m.requirement_type}</td>
                    <td className="px-4 py-2.5 font-mono">{m.operator} {String(m.expected_value ?? "")}</td>
                    <td className="px-4 py-2.5 font-mono text-blue-400 print:text-blue-700">
                      {m.observed_value !== null && m.observed_value !== undefined ? String(m.observed_value) : "—"}
                    </td>
                    <td className="px-4 py-2.5 font-bold">
                      <span className={m.status === "PASS" ? "text-emerald-400 print:text-emerald-700" : m.status === "FAIL" ? "text-rose-400 print:text-rose-700" : "text-amber-400 print:text-amber-700"}>
                        {m.status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Limitations Notice */}
        {report?.historical_limitations_notice && (
          <div className="p-4 rounded-lg bg-zinc-900/40 print:bg-zinc-50 border border-zinc-800/60 print:border-zinc-200 text-xs text-zinc-500 print:text-zinc-600">
            <span className="font-semibold text-zinc-400 print:text-zinc-700">Notice: </span>
            {report.historical_limitations_notice}
          </div>
        )}
      </div>
    </div>
  );
}
