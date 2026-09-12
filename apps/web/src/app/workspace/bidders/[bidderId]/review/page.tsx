"use client";

import React, { useState, useEffect, useCallback } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { 
  CheckSquare, ArrowLeft, RefreshCw, CheckCircle, XCircle, 
  AlertTriangle, AlertCircle
} from "lucide-react";
import { api } from "@/services/api";
import { demoStore } from "@/services/demo-store";
import { BidderRead, ComplianceMatrixRow, HumanDecisionCreate, HumanDecisionRead } from "@/services/types";
import { SessionRequired } from "@/components/ui/SessionRequired";
import { useAuth } from "@/hooks/useAuth";

export default function HumanReviewPage() {
  const params = useParams();
  const bidderId = params?.bidderId as string;
  const { isAuthenticated, isDemoPreview, role } = useAuth();
  const storedDemoBidder = bidderId ? demoStore.getBidder(bidderId) : null;
  const isDemo = isDemoPreview || Boolean(storedDemoBidder);
  const canSubmitDecision = isDemo || role === 'ADMIN' || role === 'PROCUREMENT_OFFICER';

  const [bidder, setBidder] = useState<BidderRead | null>(null);
  const [matrix, setMatrix] = useState<ComplianceMatrixRow[]>([]);
  const [existingDecision, setExistingDecision] = useState<HumanDecisionRead | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [status, setStatus] = useState<"QUALIFIED" | "DISQUALIFIED">("QUALIFIED");
  const [reasonCode, setReasonCode] = useState<string>("MANUAL_APPROVAL_COMPLIANT");
  const [remarks, setRemarks] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitSuccess, setSubmitSuccess] = useState(false);

  const loadData = useCallback(async () => {
    if (!bidderId) return;
    setLoading(true);
    setError(null);

    if (isDemo) {
      const match = demoStore.getBidder(bidderId);
      if (!match) {
        setBidder(null);
        setError("Bidder Not Found");
        setLoading(false);
        return;
      }
      setBidder(match);
      const demoMatrix = demoStore.getComplianceMatrix(bidderId);
      setMatrix(demoMatrix?.rows || []);

      const savedDecision = demoStore.getDemoState().humanDecisions[bidderId];
      if (savedDecision) {
        setExistingDecision({
          id: `dec_demo_${bidderId}`,
          bidder_id: bidderId,
          officer_id: 'usr_proc_officer_01',
          officer_name: 'Rajesh Kumar (Senior Procurement Officer)',
          status: savedDecision as "QUALIFIED" | "DISQUALIFIED",
          reason_code: savedDecision === 'QUALIFIED' ? 'MANUAL_APPROVAL_COMPLIANT' : 'STATUTORY_NON_COMPLIANCE',
          remarks: savedDecision === 'QUALIFIED' ? 'All tender criteria satisfied and statutory facts validated.' : 'Eligibility criteria not satisfied.',
          decided_at: new Date().toISOString(),
        });
      } else {
        setExistingDecision(null);
      }
      setLoading(false);
      return;
    }

    if (!isAuthenticated) {
      setLoading(false);
      return;
    }

    try {
      const [bData, mData, rData] = await Promise.all([
        api.getBidder(bidderId),
        api.getComplianceMatrix(bidderId),
        api.getReport(bidderId).catch(() => null)
      ]);
      setBidder(bData);
      setMatrix(mData);
      if (rData && rData.human_decision) {
        setExistingDecision(rData.human_decision);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load review data.");
    } finally {
      setLoading(false);
    }
  }, [bidderId, isDemo, isAuthenticated]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleSubmitDecision = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!bidderId) return;
    setSubmitting(true);
    setSubmitError(null);
    setSubmitSuccess(false);

    if (isDemo) {
      demoStore.recordHumanDecision(bidderId, status);
      setExistingDecision({
        id: `dec_demo_${Date.now()}`,
        bidder_id: bidderId,
        officer_id: 'usr_proc_officer_01',
        officer_name: 'Rajesh Kumar (Senior Procurement Officer)',
        status,
        reason_code: reasonCode,
        remarks: remarks.trim() || undefined,
        decided_at: new Date().toISOString(),
      });
      setSubmitSuccess(true);
      await loadData();
      setSubmitting(false);
      return;
    }

    try {
      const payload: HumanDecisionCreate = {
        status,
        reason_code: reasonCode,
        remarks: remarks.trim() || undefined
      };
      const res = await api.submitHumanDecision(bidderId, payload);
      setExistingDecision(res);
      setSubmitSuccess(true);
      await loadData();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to record human decision on backend.";
      setSubmitError(msg);
    } finally {
      setSubmitting(false);
    }
  };

  const flaggedRows = matrix.filter((r) => r.status === "REVIEW_REQUIRED" || r.status === "FAIL" || r.review_required);

  if (!isAuthenticated && !isDemo) {
    return (
      <SessionRequired
        title="Session Required"
        description="To authorize human qualification decisions and sign off audit records, connect an authorized Bearer token or explore in the Demo Workspace."
      />
    );
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="flex flex-col items-center gap-3">
          <RefreshCw className="w-8 h-8 text-blue-500 animate-spin" />
          <p className="text-zinc-400 text-sm">Loading evaluation decisions...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      {/* Back Link */}
      <div>
        <Link href={`/workspace/bidders/${bidderId}`} className="inline-flex items-center gap-2 text-sm text-zinc-400 hover:text-zinc-200 mb-4 transition-colors">
          <ArrowLeft className="w-4 h-4" /> Back to Bidder Evaluation
        </Link>
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-zinc-100 flex items-center gap-2.5">
              <CheckSquare className="w-6 h-6 text-amber-400" />
              Statutory Officer Review & Determination
            </h1>
            <p className="text-sm text-zinc-400 mt-1">
              Bidder: <span className="font-semibold text-zinc-200">{bidder?.bidder_name}</span>
            </p>
          </div>
        </div>
      </div>

      {/* Bidder Sub-Navigation Tabs */}
      <div className="border-b border-zinc-800 flex gap-4 text-xs font-mono">
        <Link
          href={`/workspace/bidders/${bidderId}/matrix`}
          className="pb-2.5 font-medium border-b-2 border-transparent text-zinc-400 hover:text-zinc-200 flex items-center gap-1.5"
        >
          Compliance Matrix
        </Link>
        <Link
          href={`/workspace/bidders/${bidderId}/review`}
          className="pb-2.5 font-semibold border-b-2 border-amber-500 text-amber-400 flex items-center gap-1.5"
        >
          <CheckSquare className="w-3.5 h-3.5" /> Human Officer Review
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

      {/* Existing Decision Alert */}
      {existingDecision && (
        <div className="p-5 rounded-xl bg-zinc-900/80 border border-blue-500/30 text-zinc-200 space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wider text-blue-400">Current Authoritative Decision</span>
            <span className="text-xs font-mono text-zinc-400">{new Date(existingDecision.decided_at).toLocaleString()}</span>
          </div>
          <div className="flex items-center gap-3 text-lg font-bold">
            {existingDecision.status === "QUALIFIED" ? (
              <span className="text-emerald-400 flex items-center gap-2"><CheckCircle className="w-5 h-5" /> QUALIFIED</span>
            ) : (
              <span className="text-rose-400 flex items-center gap-2"><XCircle className="w-5 h-5" /> DISQUALIFIED</span>
            )}
            <span className="text-sm font-normal text-zinc-400">({existingDecision.reason_code})</span>
          </div>
          {existingDecision.remarks && (
            <p className="text-sm text-zinc-300 bg-zinc-950/60 p-3 rounded-lg border border-zinc-800 font-sans">
              &quot;{existingDecision.remarks}&quot;
            </p>
          )}
          <p className="text-xs text-zinc-500 pt-1">
            Decided By Officer: <span className="font-mono text-zinc-400">{existingDecision.officer_name || existingDecision.officer_id}</span>
          </p>
        </div>
      )}

      {/* Flagged Clauses Requiring Review */}
      <div className="p-5 rounded-xl bg-zinc-900/60 border border-zinc-800/80 space-y-4">
        <h2 className="text-base font-semibold text-zinc-200 flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-amber-400" />
          Flagged Evaluation Items ({flaggedRows.length})
        </h2>
        {flaggedRows.length === 0 ? (
          <p className="text-sm text-zinc-400">No clauses flagged for manual officer intervention.</p>
        ) : (
          <div className="space-y-3">
            {flaggedRows.map((r) => (
              <div key={r.requirement_id} className="p-3.5 rounded-lg bg-zinc-950 border border-zinc-800/80 space-y-1.5">
                <div className="flex items-center justify-between">
                  <span className="font-medium text-sm text-zinc-200">{r.clause}</span>
                  <span className={`px-2 py-0.5 rounded text-xs font-semibold ${
                    r.status === "FAIL" ? "bg-rose-500/10 text-rose-400" : "bg-amber-500/10 text-amber-400"
                  }`}>
                    {r.status}
                  </span>
                </div>
                <div className="flex gap-4 text-xs font-mono text-zinc-400">
                  <span>Field: {r.field}</span>
                  <span>Expected: {r.operator} {String(r.expected_value ?? "")}</span>
                  <span>Observed: {String(r.observed_value ?? "None")}</span>
                </div>
                {r.reason_code && (
                  <p className="text-xs text-zinc-400 italic">Flag rationale: {r.reason_code}</p>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Decision Submission Form */}
      <div className="p-6 rounded-xl bg-zinc-900/60 border border-zinc-800/80 space-y-4">
        <h2 className="text-base font-semibold text-zinc-200">
          {existingDecision ? "Update Authoritative Determination" : "Submit Authoritative Determination"}
        </h2>

        {submitError && (
          <div className="p-4 rounded-xl bg-rose-950/30 border border-rose-800/50 text-rose-300 text-sm">
            {submitError}
          </div>
        )}

        {submitSuccess && (
          <div className="p-4 rounded-xl bg-emerald-950/30 border border-emerald-800/50 text-emerald-300 text-sm flex items-center justify-between">
            <span>Decision recorded successfully on authoritative backend.</span>
            <Link href={`/workspace/bidders/${bidderId}/report`} className="underline font-semibold">
              View Audit Report →
            </Link>
          </div>
        )}

        {!canSubmitDecision && (
          <div className="p-4 rounded-xl bg-amber-950/30 border border-amber-800/50 text-amber-300 text-xs font-mono flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0" />
            <span>
              Access Denied / Role Not Authorized: Your active role ({role || 'AUDITOR/REVIEWER'}) is read-only.
              Only ADMIN and PROCUREMENT_OFFICER roles may record authoritative procurement determinations.
            </span>
          </div>
        )}

        <form onSubmit={handleSubmitDecision} className="space-y-5">
          <div>
            <label className="block text-xs font-medium text-zinc-400 mb-2">Determination Status *</label>
            <div className="grid grid-cols-2 gap-4">
              <button
                type="button"
                onClick={() => {
                  setStatus("QUALIFIED");
                  setReasonCode("MANUAL_APPROVAL_COMPLIANT");
                }}
                className={`p-4 rounded-xl border flex items-center justify-center gap-3 font-medium text-sm transition-colors ${
                  status === "QUALIFIED"
                    ? "bg-emerald-500/10 border-emerald-500 text-emerald-300 ring-1 ring-emerald-500"
                    : "bg-zinc-950 border-zinc-800 text-zinc-400 hover:border-zinc-700"
                }`}
              >
                <CheckCircle className="w-5 h-5 text-emerald-400" />
                QUALIFY BIDDER
              </button>
              <button
                type="button"
                onClick={() => {
                  setStatus("DISQUALIFIED");
                  setReasonCode("STATUTORY_NON_COMPLIANCE");
                }}
                className={`p-4 rounded-xl border flex items-center justify-center gap-3 font-medium text-sm transition-colors ${
                  status === "DISQUALIFIED"
                    ? "bg-rose-500/10 border-rose-500 text-rose-300 ring-1 ring-rose-500"
                    : "bg-zinc-950 border-zinc-800 text-zinc-400 hover:border-zinc-700"
                }`}
              >
                <XCircle className="w-5 h-5 text-rose-400" />
                DISQUALIFY BIDDER
              </button>
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-zinc-400 mb-1">Reason Code *</label>
            <input
              type="text"
              required
              value={reasonCode}
              onChange={(e) => setReasonCode(e.target.value)}
              placeholder="e.g. MANUAL_APPROVAL_COMPLIANT or STATUTORY_NON_COMPLIANCE"
              className="w-full px-3.5 py-2.5 rounded-lg bg-zinc-950 border border-zinc-800 text-zinc-100 text-sm font-mono focus:outline-none focus:border-blue-500"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-zinc-400 mb-1">Officer Justification & Remarks</label>
            <textarea
              rows={4}
              value={remarks}
              onChange={(e) => setRemarks(e.target.value)}
              placeholder="Provide statutory justification or reference specific clauses reviewed..."
              className="w-full px-3.5 py-2.5 rounded-lg bg-zinc-950 border border-zinc-800 text-zinc-100 text-sm focus:outline-none focus:border-blue-500"
            />
          </div>

          <div className="flex justify-end gap-3 pt-2">
            <Link
              href={`/workspace/bidders/${bidderId}`}
              className="px-4 py-2 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-sm font-medium transition-colors"
            >
              Cancel
            </Link>
            <button
              type="submit"
              disabled={submitting || !canSubmitDecision}
              className="px-6 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {submitting ? "Recording..." : !canSubmitDecision ? "Action Prohibited (Read-Only)" : "Submit Authoritative Decision"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
