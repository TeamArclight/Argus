"use client";

import React, { useState, useEffect } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { 
  FileText, CheckCircle, AlertTriangle, XCircle, ArrowLeft, 
  RefreshCw, Filter, Eye, AlertCircle
} from "lucide-react";
import { api } from "@/services/api";
import { demoStore } from "@/services/demo-store";
import { ComplianceMatrixRow } from "@/services/types";
import { EvidenceDrawer } from "@/components/ui/EvidenceDrawer";
import { SessionRequired } from "@/components/ui/SessionRequired";
import { useAuth } from "@/hooks/useAuth";

export default function ComplianceMatrixPage() {
  const params = useParams();
  const bidderId = params?.bidderId as string;
  const { isAuthenticated, isDemoPreview } = useAuth();

  const [matrix, setMatrix] = useState<ComplianceMatrixRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [statusFilter, setStatusFilter] = useState<string>("ALL");
  const [typeFilter, setTypeFilter] = useState<string>("ALL");

  // Evidence Drawer
  const [selectedEvidenceRow, setSelectedEvidenceRow] = useState<ComplianceMatrixRow | null>(null);

  const loadMatrix = async () => {
    if (!bidderId) return;
    setLoading(true);
    setError(null);

    if (isDemoPreview) {
      const bidder = demoStore.getBidder(bidderId);
      if (!bidder) {
        setMatrix([]);
        setError("Bidder Not Found");
        setLoading(false);
        return;
      }
      const demoMatrix = demoStore.getComplianceMatrix(bidderId);
      setMatrix(demoMatrix?.rows || []);
      setLoading(false);
      return;
    }

    if (!isAuthenticated) {
      setLoading(false);
      return;
    }

    try {
      const data = await api.getComplianceMatrix(bidderId);
      setMatrix(data);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load compliance matrix.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadMatrix();
  }, [bidderId, isDemoPreview, isAuthenticated]);

  const filteredMatrix = matrix.filter((row) => {
    if (statusFilter !== "ALL" && row.status !== statusFilter) return false;
    if (typeFilter !== "ALL" && row.requirement_type !== typeFilter) return false;
    return true;
  });

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "PASS":
        return <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"><CheckCircle className="w-3 h-3" /> PASS</span>;
      case "FAIL":
        return <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-rose-500/10 text-rose-400 border border-rose-500/20"><XCircle className="w-3 h-3" /> FAIL</span>;
      case "REVIEW_REQUIRED":
        return <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-amber-500/10 text-amber-400 border border-amber-500/20"><AlertTriangle className="w-3 h-3" /> REVIEW</span>;
      default:
        return <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-zinc-800 text-zinc-400 border border-zinc-700">{status}</span>;
    }
  };

  if (!isAuthenticated && !isDemoPreview) {
    return (
      <SessionRequired
        title="Session Required"
        description="To inspect the full deterministic compliance matrix for this bidder, connect an authorized Bearer token or explore in the Demo Workspace."
      />
    );
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="flex flex-col items-center gap-3">
          <RefreshCw className="w-8 h-8 text-blue-500 animate-spin" />
          <p className="text-zinc-400 text-sm">Evaluating compliance matrix from verified evidence...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <Link href={`/workspace/bidders/${bidderId}`} className="inline-flex items-center gap-2 text-sm text-zinc-400 hover:text-zinc-200 mb-4 transition-colors">
          <ArrowLeft className="w-4 h-4" /> Back to Bidder Evaluation
        </Link>
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-zinc-100 flex items-center gap-2.5">
              <FileText className="w-6 h-6 text-blue-400" />
              Compliance Matrix
            </h1>
            <p className="text-sm text-zinc-400 mt-1">
              Authoritative rule evaluations mapped against submitted tender evidence.
            </p>
          </div>
          <button
            onClick={loadMatrix}
            className="inline-flex items-center gap-2 px-3.5 py-2 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-sm font-medium rounded-lg transition-colors border border-zinc-700"
          >
            <RefreshCw className="w-4 h-4" /> Refresh Matrix
          </button>
        </div>
      </div>

      {/* Bidder Sub-Navigation Tabs */}
      <div className="border-b border-zinc-800 flex gap-4 text-xs font-mono">
        <Link
          href={`/workspace/bidders/${bidderId}/matrix`}
          className="pb-2.5 font-semibold border-b-2 border-blue-500 text-blue-400 flex items-center gap-1.5"
        >
          <FileText className="w-3.5 h-3.5" /> Compliance Matrix
        </Link>
        <Link
          href={`/workspace/bidders/${bidderId}/review`}
          className="pb-2.5 font-medium border-b-2 border-transparent text-zinc-400 hover:text-zinc-200 flex items-center gap-1.5"
        >
          Human Officer Review
        </Link>
        <Link
          href={`/workspace/bidders/${bidderId}/report`}
          className="pb-2.5 font-medium border-b-2 border-transparent text-zinc-400 hover:text-zinc-200 flex items-center gap-1.5"
        >
          Audit Report
        </Link>
      </div>

      {error && (
        <div className="p-4 rounded-xl bg-rose-950/20 border border-rose-800/40 text-rose-300 text-sm flex items-center gap-3">
          <AlertCircle className="w-5 h-5 text-rose-400 flex-shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Filter Controls */}
      <div className="flex flex-wrap items-center justify-between gap-4 p-4 rounded-xl bg-zinc-900/60 border border-zinc-800/80">
        <div className="flex items-center gap-2">
          <Filter className="w-4 h-4 text-zinc-400" />
          <span className="text-xs font-medium text-zinc-400">Filters:</span>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="px-3 py-1.5 rounded-lg bg-zinc-950 border border-zinc-800 text-xs text-zinc-200 focus:outline-none focus:border-blue-500"
          >
            <option value="ALL">All Statuses</option>
            <option value="PASS">PASS</option>
            <option value="FAIL">FAIL</option>
            <option value="REVIEW_REQUIRED">REVIEW REQUIRED</option>
          </select>
          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
            className="px-3 py-1.5 rounded-lg bg-zinc-950 border border-zinc-800 text-xs text-zinc-200 focus:outline-none focus:border-blue-500"
          >
            <option value="ALL">All Requirement Types</option>
            <option value="STATUTORY">STATUTORY</option>
            <option value="TURNOVER">TURNOVER</option>
            <option value="EXPERIENCE_YEARS">EXPERIENCE_YEARS</option>
            <option value="SIMILAR_WORK_VALUE">SIMILAR_WORK_VALUE</option>
            <option value="GST_ACTIVE">GST_ACTIVE</option>
            <option value="PAN_MATCH">PAN_MATCH</option>
            <option value="CIN_ACTIVE">CIN_ACTIVE</option>
            <option value="UDYAM_MSME">UDYAM_MSME</option>
            <option value="LOCAL_CONTENT">LOCAL_CONTENT</option>
            <option value="OEM_AUTH">OEM_AUTH</option>
            <option value="TECHNICAL_SPEC">TECHNICAL_SPEC</option>
            <option value="OTHER">OTHER</option>
          </select>
        </div>
        <div className="text-xs text-zinc-400">
          Showing <span className="font-semibold text-zinc-200">{filteredMatrix.length}</span> of {matrix.length} clauses
        </div>
      </div>

      {/* Matrix Table */}
      {filteredMatrix.length === 0 ? (
        <div className="p-12 text-center rounded-xl bg-zinc-900/40 border border-zinc-800/80">
          <FileText className="w-12 h-12 text-zinc-600 mx-auto mb-3" />
          <h3 className="text-base font-semibold text-zinc-300">No Matching Criteria</h3>
          <p className="text-sm text-zinc-500 mt-1">Adjust filters or evaluate compliance rules for this bidder.</p>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-zinc-800/80 bg-zinc-900/40">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-zinc-800 text-xs text-zinc-400 font-semibold bg-zinc-950/40">
                <th className="px-5 py-3">Clause Reference</th>
                <th className="px-5 py-3">Category</th>
                <th className="px-5 py-3">Expected Value</th>
                <th className="px-5 py-3">Observed Value</th>
                <th className="px-5 py-3">Evaluation Status</th>
                <th className="px-5 py-3">Reason / Details</th>
                <th className="px-5 py-3 text-right">Evidence</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/60">
              {filteredMatrix.map((row) => (
                <tr key={row.requirement_id} className="hover:bg-zinc-800/30 transition-colors">
                  <td className="px-5 py-3.5 font-medium text-zinc-200">
                    <div>{row.clause}</div>
                    <div className="text-xs text-zinc-500 font-mono mt-0.5">{row.field}</div>
                  </td>
                  <td className="px-5 py-3.5">
                    <span className="px-2 py-0.5 rounded text-xs font-mono bg-zinc-800 text-zinc-300 border border-zinc-700">
                      {row.requirement_type}
                    </span>
                  </td>
                  <td className="px-5 py-3.5 font-mono text-xs text-zinc-300">
                    {row.operator} {String(row.expected_value ?? "")}
                  </td>
                  <td className="px-5 py-3.5 font-mono text-xs text-blue-300">
                    {row.observed_value !== null && row.observed_value !== undefined ? String(row.observed_value) : "—"}
                  </td>
                  <td className="px-5 py-3.5">
                    {getStatusBadge(row.status)}
                  </td>
                  <td className="px-5 py-3.5 text-xs text-zinc-400 max-w-xs truncate">
                    {row.reason_code || (row.review_required ? "Officer Review Required" : "Evaluated successfully")}
                  </td>
                  <td className="px-5 py-3.5 text-right">
                    <button
                      onClick={() => setSelectedEvidenceRow(row)}
                      className="inline-flex items-center gap-1.5 px-3 py-1 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 rounded text-xs font-medium transition-colors"
                    >
                      <Eye className="w-3.5 h-3.5" /> Inspect
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Evidence Drawer */}
      {selectedEvidenceRow && (
        <EvidenceDrawer
          row={selectedEvidenceRow}
          isOpen={!!selectedEvidenceRow}
          onClose={() => setSelectedEvidenceRow(null)}
        />
      )}
    </div>
  );
}
