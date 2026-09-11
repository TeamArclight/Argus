"use client";

import React, { useState, useEffect } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { 
  Building2, ShieldCheck, CheckCircle, AlertTriangle, XCircle, 
  Clock, ArrowLeft, RefreshCw, Sparkles, FileText, CheckSquare, Upload, AlertCircle
} from "lucide-react";
import { api } from "@/services/api";
import { demoStore } from "@/services/demo-store";
import { BidderRead, VerificationResultRead, ComplianceMatrixRow } from "@/services/types";
import { JobProgressDrawer } from "@/components/ui/JobProgressDrawer";
import { SessionRequired } from "@/components/ui/SessionRequired";
import { useAuth } from "@/hooks/useAuth";

export default function BidderDetailPage() {
  const params = useParams();
  const bidderId = params?.bidderId as string;
  const { isAuthenticated, isDemoPreview } = useAuth();

  const [bidder, setBidder] = useState<BidderRead | null>(null);
  const [verifications, setVerifications] = useState<VerificationResultRead[]>([]);
  const [matrix, setMatrix] = useState<ComplianceMatrixRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Async Jobs
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [triggeringVerify, setTriggeringVerify] = useState(false);
  const [triggeringCompliance, setTriggeringCompliance] = useState(false);

  // Upload file state
  const [uploadingDoc, setUploadingDoc] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const storedDemoBidder = bidderId ? demoStore.getBidder(bidderId) : null;
  const isDemo = isDemoPreview || Boolean(storedDemoBidder);

  const loadBidderData = async () => {
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
      const demoVerifications = demoStore.getDemoState().verifications[bidderId] || [];
      const demoMatrix = demoStore.getComplianceMatrix(bidderId);
      setVerifications(demoVerifications);
      setMatrix(demoMatrix?.rows || []);
      setLoading(false);
      return;
    }

    if (!isAuthenticated) {
      setLoading(false);
      return;
    }

    try {
      const [bData, vData, mData] = await Promise.all([
        api.getBidder(bidderId),
        api.getVerificationResults(bidderId),
        api.getComplianceMatrix(bidderId)
      ]);
      setBidder(bData);
      setVerifications(vData);
      setMatrix(mData);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load bidder records.";
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadBidderData();
  }, [bidderId, isDemo, isAuthenticated]);

  const handleRunVerification = async () => {
    if (!bidderId) return;
    setTriggeringVerify(true);
    setError(null);

    if (isDemo) {
      setTimeout(async () => {
        setTriggeringVerify(false);
        await loadBidderData();
      }, 500);
      return;
    }

    try {
      const res = await api.runVerification(bidderId);
      if (res.id) {
        setActiveJobId(res.id);
      } else {
        await loadBidderData();
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to trigger statutory verification.");
    } finally {
      setTriggeringVerify(false);
    }
  };

  const handleRunCompliance = async () => {
    if (!bidderId) return;
    setTriggeringCompliance(true);
    setError(null);

    if (isDemo) {
      setTimeout(async () => {
        setTriggeringCompliance(false);
        await loadBidderData();
      }, 500);
      return;
    }

    try {
      const res = await api.runCompliance(bidderId);
      if (res.id) {
        setActiveJobId(res.id);
      } else {
        await loadBidderData();
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to trigger compliance evaluation.");
    } finally {
      setTriggeringCompliance(false);
    }
  };

  const handleDocUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !bidderId) return;
    setUploadingDoc(true);
    setError(null);

    if (isDemo) {
      setTimeout(async () => {
        setUploadingDoc(false);
        await loadBidderData();
      }, 500);
      return;
    }

    try {
      await api.uploadBidderDocument(bidderId, file, 'FINANCIAL_STATEMENT');
      await loadBidderData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to upload document.');
    } finally {
      setUploadingDoc(false);
    }
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "QUALIFIED":
      case "VERIFIED":
      case "PASS":
        return <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"><CheckCircle className="w-3 h-3" /> {status}</span>;
      case "PENDING":
      case "RUNNING":
        return <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-blue-500/10 text-blue-400 border border-blue-500/20"><Clock className="w-3 h-3 animate-spin" /> {status}</span>;
      case "MANUAL_REVIEW":
      case "REVIEW_REQUIRED":
        return <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-amber-500/10 text-amber-400 border border-amber-500/20"><AlertTriangle className="w-3 h-3" /> {status}</span>;
      case "DISQUALIFIED":
      case "FAILED":
      case "FAIL":
        return <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-rose-500/10 text-rose-400 border border-rose-500/20"><XCircle className="w-3 h-3" /> {status}</span>;
      default:
        return <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-zinc-800 text-zinc-400 border border-zinc-700">{status}</span>;
    }
  };

  if (!isAuthenticated && !isDemo) {
    return (
      <SessionRequired
        title="Session Required"
        description="To inspect this bidder and run deterministic qualification rules, connect an authorized Bearer token or explore in the Demo Workspace."
      />
    );
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="flex flex-col items-center gap-3">
          <RefreshCw className="w-8 h-8 text-blue-500 animate-spin" />
          <p className="text-zinc-400 text-sm">Loading bidder evaluation record...</p>
        </div>
      </div>
    );
  }

  if (error && !bidder) {
    return (
      <div className="max-w-4xl mx-auto p-6">
        <div className="p-6 rounded-xl bg-rose-950/20 border border-rose-800/40 text-rose-300">
          <div className="flex items-center gap-3 mb-2">
            <AlertCircle className="w-6 h-6 text-rose-400" />
            <h2 className="text-lg font-semibold">Bidder Evaluation Failed to Load</h2>
          </div>
          <p className="text-sm text-zinc-400 mb-4">{error}</p>
          <div className="flex gap-3">
            <button onClick={loadBidderData} className="px-4 py-2 bg-rose-600 hover:bg-rose-500 text-white rounded-lg text-sm font-medium">
              Retry Load
            </button>
            <Link href="/workspace/tenders" className="px-4 py-2 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg text-sm font-medium">
              Back to Tenders
            </Link>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Back Link & Header */}
      <div>
        <Link href={bidder?.tender_id ? `/workspace/tenders/${bidder.tender_id}` : "/workspace/tenders"} className="inline-flex items-center gap-2 text-sm text-zinc-400 hover:text-zinc-200 mb-4 transition-colors">
          <ArrowLeft className="w-4 h-4" /> Back to Tender
        </Link>
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-bold text-zinc-100">{bidder?.bidder_name}</h1>
              {bidder && getStatusBadge(bidder.status)}
            </div>
            <p className="text-sm text-zinc-400 mt-1">
              Registered ID: <span className="font-mono text-zinc-300">{bidderId}</span>
            </p>
          </div>
          <div className="flex items-center gap-3 flex-wrap">
            <label className="inline-flex items-center gap-2 px-3.5 py-2 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-sm font-medium rounded-lg cursor-pointer transition-colors border border-zinc-700">
              <Upload className="w-4 h-4" />
              {uploadingDoc ? "Uploading..." : "Upload Document"}
              <input type="file" onChange={handleDocUpload} disabled={uploadingDoc} className="hidden" />
            </label>
            <button
              onClick={handleRunVerification}
              disabled={triggeringVerify}
              className="inline-flex items-center gap-2 px-3.5 py-2 bg-emerald-700 hover:bg-emerald-600 text-white text-sm font-medium rounded-lg transition-colors disabled:opacity-50"
            >
              <ShieldCheck className="w-4 h-4" />
              {triggeringVerify ? "Verifying..." : "Run Statutory Checks"}
            </button>
            <button
              onClick={handleRunCompliance}
              disabled={triggeringCompliance}
              className="inline-flex items-center gap-2 px-3.5 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded-lg transition-colors disabled:opacity-50"
            >
              <Sparkles className="w-4 h-4" />
              {triggeringCompliance ? "Evaluating..." : "Evaluate Compliance"}
            </button>
          </div>
        </div>
      </div>

      {(error || uploadError) && (
        <div className="p-4 rounded-xl bg-rose-950/20 border border-rose-800/40 text-rose-300 text-sm flex items-center justify-between">
          <span>{error || uploadError}</span>
          <button onClick={() => { setError(null); setUploadError(null); }} className="text-xs underline hover:text-rose-200">Dismiss</button>
        </div>
      )}

      {/* Navigation Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Link
          href={`/workspace/bidders/${bidderId}/matrix`}
          className="p-5 rounded-xl bg-zinc-900/60 border border-zinc-800 hover:border-blue-500/50 transition-colors group block"
        >
          <div className="flex items-center justify-between mb-2">
            <h3 className="font-semibold text-zinc-200 group-hover:text-blue-400 flex items-center gap-2">
              <FileText className="w-4 h-4" /> Compliance Matrix
            </h3>
            <span className="text-xs text-zinc-500">{matrix.length} Rules</span>
          </div>
          <p className="text-xs text-zinc-400">
            View line-by-line statutory and technical evaluation criteria with cited document evidence.
          </p>
        </Link>

        <Link
          href={`/workspace/bidders/${bidderId}/review`}
          className="p-5 rounded-xl bg-zinc-900/60 border border-zinc-800 hover:border-amber-500/50 transition-colors group block"
        >
          <div className="flex items-center justify-between mb-2">
            <h3 className="font-semibold text-zinc-200 group-hover:text-amber-400 flex items-center gap-2">
              <CheckSquare className="w-4 h-4" /> Human Review
            </h3>
            <span className="text-xs text-amber-400 font-medium">Officer Decision</span>
          </div>
          <p className="text-xs text-zinc-400">
            Override flags, review borderline evaluation clauses, and submit authoritative determination.
          </p>
        </Link>

        <Link
          href={`/workspace/bidders/${bidderId}/report`}
          className="p-5 rounded-xl bg-zinc-900/60 border border-zinc-800 hover:border-emerald-500/50 transition-colors group block"
        >
          <div className="flex items-center justify-between mb-2">
            <h3 className="font-semibold text-zinc-200 group-hover:text-emerald-400 flex items-center gap-2">
              <ShieldCheck className="w-4 h-4" /> Audit Report
            </h3>
            <span className="text-xs text-emerald-400 font-medium">Authoritative</span>
          </div>
          <p className="text-xs text-zinc-400">
            Export audit-ready evaluation report containing verification history and compliance decisions.
          </p>
        </Link>
      </div>

      {/* Statutory Registry Overview */}
      <div className="rounded-xl border border-zinc-800/80 bg-zinc-900/40 p-6 space-y-4">
        <h2 className="text-base font-semibold text-zinc-200 flex items-center gap-2">
          <Building2 className="w-4 h-4 text-blue-400" />
          Statutory Identifier Registry
        </h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="p-3.5 rounded-lg bg-zinc-950/60 border border-zinc-800/60">
            <span className="text-xs text-zinc-500 font-medium">GSTIN</span>
            <p className="font-mono text-sm text-zinc-200 mt-1">{bidder?.gstin || "Not Registered"}</p>
          </div>
          <div className="p-3.5 rounded-lg bg-zinc-950/60 border border-zinc-800/60">
            <span className="text-xs text-zinc-500 font-medium">CIN</span>
            <p className="font-mono text-sm text-zinc-200 mt-1">{bidder?.cin || "Not Registered"}</p>
          </div>
          <div className="p-3.5 rounded-lg bg-zinc-950/60 border border-zinc-800/60">
            <span className="text-xs text-zinc-500 font-medium">PAN</span>
            <p className="font-mono text-sm text-zinc-200 mt-1">{bidder?.pan || "Not Registered"}</p>
          </div>
          <div className="p-3.5 rounded-lg bg-zinc-950/60 border border-zinc-800/60">
            <span className="text-xs text-zinc-500 font-medium">UDYAM Number</span>
            <p className="font-mono text-sm text-zinc-200 mt-1">{bidder?.udyam_number || "Not Registered"}</p>
          </div>
        </div>
      </div>

      {/* Statutory Verification Results */}
      <div className="space-y-3">
        <h2 className="text-base font-semibold text-zinc-200 flex items-center gap-2">
          <ShieldCheck className="w-4 h-4 text-emerald-400" />
          Statutory Registry Verification Checks ({verifications.length})
        </h2>
        {verifications.length === 0 ? (
          <div className="p-8 text-center rounded-xl bg-zinc-900/40 border border-zinc-800/80">
            <ShieldCheck className="w-8 h-8 text-zinc-600 mx-auto mb-2" />
            <p className="text-sm text-zinc-400">No registry verification results recorded.</p>
            <p className="text-xs text-zinc-500 mt-1">Click &quot;Run Statutory Checks&quot; to verify GSTIN/MCA/PAN against registry adapters.</p>
          </div>
        ) : (
          <div className="overflow-x-auto rounded-xl border border-zinc-800/80 bg-zinc-900/40">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-zinc-800 text-xs text-zinc-400 font-semibold bg-zinc-950/40">
                  <th className="px-5 py-3">Verification Field</th>
                  <th className="px-5 py-3">Provenance Source</th>
                  <th className="px-5 py-3">Claimed vs Verified</th>
                  <th className="px-5 py-3">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-800/60">
                {verifications.map((v, i) => (
                  <tr key={v.id || i} className="hover:bg-zinc-800/30 transition-colors">
                    <td className="px-5 py-3 font-medium text-zinc-200 font-mono text-xs">{v.field}</td>
                    <td className="px-5 py-3 font-mono text-xs text-zinc-400">{v.source}</td>
                    <td className="px-5 py-3 text-xs text-zinc-300">
                      <span className="text-zinc-500">Claimed:</span> {String(v.claimed_value ?? "—")} | <span className="text-zinc-500">Verified:</span> {String(v.verified_value ?? "—")}
                    </td>
                    <td className="px-5 py-3">{getStatusBadge(v.status)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Job Progress Drawer */}
      {activeJobId && (
        <JobProgressDrawer
          jobId={activeJobId}
          isOpen={!!activeJobId}
          onClose={() => {
            setActiveJobId(null);
            loadBidderData();
          }}
          onComplete={loadBidderData}
        />
      )}
    </div>
  );
}
