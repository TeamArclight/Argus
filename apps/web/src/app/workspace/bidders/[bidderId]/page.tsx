"use client";

import React, { useState, useEffect, useCallback } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { 
  Building2, ShieldCheck, CheckCircle, AlertTriangle, XCircle, 
  Clock, ArrowLeft, RefreshCw, Sparkles, FileText, CheckSquare, Upload, AlertCircle
} from "lucide-react";
import { api } from "@/services/api";
import { demoStore, DemoBidderDocument } from "@/services/demo-store";
import { BidderRead, VerificationResultRead, ComplianceMatrixRow } from "@/services/types";
import { DocumentRead } from "@/types/api";
import { JobProgressDrawer } from "@/components/ui/JobProgressDrawer";
import { SessionRequired } from "@/components/ui/SessionRequired";
import { MismatchDetailModal, MismatchDetailItem } from "@/components/ui/MismatchDetailModal";
import { useAuth } from "@/hooks/useAuth";

export default function BidderDetailPage() {
  const params = useParams();
  const bidderId = params?.bidderId as string;
  const { isAuthenticated, isDemoPreview } = useAuth();

  const [bidder, setBidder] = useState<BidderRead | null>(null);
  const [verifications, setVerifications] = useState<VerificationResultRead[]>([]);
  const [matrix, setMatrix] = useState<ComplianceMatrixRow[]>([]);
  const [documents, setDocuments] = useState<DemoBidderDocument[]>([]);
  const [complianceStale, setComplianceStale] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Async Jobs
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [triggeringVerify, setTriggeringVerify] = useState(false);
  const [triggeringCompliance, setTriggeringCompliance] = useState(false);

  // Upload file state & UX stages
  const [uploadingDoc, setUploadingDoc] = useState(false);
  const [uploadStage, setUploadStage] = useState<'IDLE' | 'UPLOADING' | 'PROCESSING' | 'PROCESSED'>('IDLE');
  const [hasUploadedOnce, setHasUploadedOnce] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadSuccessMessage, setUploadSuccessMessage] = useState<string | null>(null);

  // Mismatch & Risk Explainability Modal State
  const [mismatchModalOpen, setMismatchModalOpen] = useState(false);
  const [selectedDocForMismatch, setSelectedDocForMismatch] = useState<DemoBidderDocument | null>(null);

  const storedDemoBidder = bidderId ? demoStore.getBidder(bidderId) : null;
  const isDemo = isDemoPreview || Boolean(storedDemoBidder);

  const loadBidderData = useCallback(async () => {
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
      const demoDocs = demoStore.getBidderDocuments(bidderId);
      const isStale = demoStore.isComplianceStale(bidderId);

      setVerifications(demoVerifications);
      setMatrix(demoMatrix?.rows || []);
      setDocuments(demoDocs);
      setComplianceStale(isStale);
      setLoading(false);
      return;
    }

    if (!isAuthenticated) {
      setLoading(false);
      return;
    }

    try {
      const [bData, vData, mData, dData, reportData] = await Promise.all([
        api.getBidder(bidderId),
        api.getVerificationResults(bidderId),
        api.getComplianceMatrix(bidderId),
        api.getBidderDocuments(bidderId).catch(() => []),
        api.getReport(bidderId).catch(() => null),
      ]);
      setBidder(bData);
      setVerifications(vData);
      setMatrix(mData);

      // Correlate authentic documents with direct facts and report evidence
      const authenticDocs: DemoBidderDocument[] = (dData || []).map((doc: DocumentRead) => {
        const directFacts = doc.facts || [];
        const reportEvidence = (reportData?.evidence || []).filter(
          (ev: { document_id?: string | null }) => ev.document_id === doc.id
        );
        const totalFactsCount = directFacts.length > 0 ? directFacts.length : reportEvidence.length;
        const docStatus: DemoBidderDocument['status'] = totalFactsCount > 0 ? 'PROCESSED' : 'PENDING';

        return {
          ...doc,
          status: docStatus,
          facts_count: totalFactsCount,
          extracted_facts: directFacts,
        };
      });

      setDocuments(authenticDocs);
      setComplianceStale(false);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load bidder records.";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [bidderId, isDemo, isAuthenticated]);

  useEffect(() => {
    loadBidderData();
  }, [loadBidderData]);

  const handleRunVerification = async () => {
    if (!bidderId) return;
    setTriggeringVerify(true);
    setError(null);

    if (isDemo) {
      try {
        const results = await demoStore.runStatutoryChecks(bidderId);
        setVerifications(results);
        await loadBidderData();
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : "Failed to trigger statutory verification.");
      } finally {
        setTriggeringVerify(false);
      }
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
      try {
        const matrixResult = await demoStore.evaluateCompliance(bidderId);
        setMatrix(matrixResult.rows || []);
        setComplianceStale(false);
        await loadBidderData();
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : "Failed to trigger compliance evaluation.");
      } finally {
        setTriggeringCompliance(false);
      }
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
    setUploadStage('UPLOADING');
    setError(null);
    setUploadError(null);
    setUploadSuccessMessage(null);

    if (isDemo) {
      try {
        setUploadStage('PROCESSING');
        const result = await demoStore.uploadBidderDocument(bidderId, file);
        setUploadStage('PROCESSED');
        setHasUploadedOnce(true);

        let msg = `Document processed successfully. ${result.factsCount} bidder facts extracted.`;
        if (result.enrichedCount > 0) {
          msg += ` ${result.enrichedCount} bidder identifier updated from document evidence.`;
        }
        setUploadSuccessMessage(msg);
        await loadBidderData();
      } catch (err: unknown) {
        setUploadError(err instanceof Error ? err.message : 'Failed to process document.');
        setUploadStage('IDLE');
      } finally {
        setUploadingDoc(false);
        if (e.target) e.target.value = '';
        setTimeout(() => {
          setUploadStage('IDLE');
        }, 3000);
      }
      return;
    }

    try {
      await api.uploadBidderDocument(bidderId, file, 'FINANCIAL_STATEMENT');
      setUploadStage('PROCESSED');
      setHasUploadedOnce(true);
      await loadBidderData();
    } catch (err: unknown) {
      setUploadError(err instanceof Error ? err.message : 'Failed to upload document.');
    } finally {
      setUploadingDoc(false);
      setUploadStage('IDLE');
      if (e.target) e.target.value = '';
    }
  };

  const getUploadButtonText = () => {
    if (uploadStage === 'UPLOADING') return "Uploading...";
    if (uploadStage === 'PROCESSING') return "Processing Document...";
    if (uploadStage === 'PROCESSED') return "Processed";
    if (hasUploadedOnce || documents.length > 0) return "Upload Another Document";
    return "Upload Document";
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "QUALIFIED":
      case "VERIFIED":
      case "PASS":
      case "PROCESSED":
        return <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"><CheckCircle className="w-3 h-3" /> {status}</span>;
      case "PENDING":
      case "RUNNING":
      case "PROCESSING":
        return <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-blue-500/10 text-blue-400 border border-blue-500/20"><Clock className="w-3 h-3 animate-spin" /> {status}</span>;
      case "UPLOADED":
        return <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-zinc-800 text-zinc-300 border border-zinc-700"><Clock className="w-3 h-3 text-zinc-400" /> {status}</span>;
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
              {getUploadButtonText()}
              <input type="file" onChange={handleDocUpload} disabled={uploadingDoc} className="hidden" />
            </label>
            <button
              onClick={handleRunVerification}
              disabled={triggeringVerify}
              className="inline-flex items-center gap-2 px-3.5 py-2 bg-emerald-700 hover:bg-emerald-600 text-white text-sm font-medium rounded-lg transition-colors disabled:opacity-50"
            >
              <ShieldCheck className="w-4 h-4" />
              {triggeringVerify
                ? "Verifying..."
                : verifications.length > 0
                ? "Re-run Statutory Checks"
                : "Run Statutory Checks"}
            </button>
            <button
              onClick={handleRunCompliance}
              disabled={triggeringCompliance}
              className="inline-flex items-center gap-2 px-3.5 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded-lg transition-colors disabled:opacity-50"
            >
              <Sparkles className="w-4 h-4" />
              {triggeringCompliance
                ? "Evaluating..."
                : matrix.length > 0 && matrix.some((r) => r.observed_value !== 'PENDING_EVALUATION')
                ? "Re-evaluate Compliance"
                : "Evaluate Compliance"}
            </button>
          </div>
        </div>
      </div>

      {/* Compliance Stale Banner */}
      {complianceStale && (
        <div className="p-4 rounded-xl bg-amber-950/30 border border-amber-500/40 text-amber-200 flex flex-col sm:flex-row sm:items-center justify-between gap-3 shadow-lg shadow-amber-950/20">
          <div className="flex items-start sm:items-center gap-3">
            <AlertTriangle className="w-5 h-5 text-amber-400 flex-shrink-0 mt-0.5 sm:mt-0" />
            <div>
              <p className="font-semibold text-amber-100 text-sm">Compliance Outdated</p>
              <p className="text-xs text-amber-300/90 mt-0.5">
                New evidence has been uploaded since the last compliance evaluation. Re-evaluate compliance to include the latest evidence.
              </p>
            </div>
          </div>
          <button
            onClick={handleRunCompliance}
            disabled={triggeringCompliance}
            className="inline-flex items-center gap-2 px-3.5 py-1.5 bg-amber-600 hover:bg-amber-500 text-white text-xs font-semibold rounded-lg transition-colors whitespace-nowrap self-end sm:self-center disabled:opacity-50"
          >
            <Sparkles className="w-3.5 h-3.5" />
            {triggeringCompliance ? "Evaluating..." : "Re-evaluate Compliance"}
          </button>
        </div>
      )}

      {/* Upload Success Feedback */}
      {uploadSuccessMessage && (
        <div className="p-4 rounded-xl bg-emerald-950/30 border border-emerald-500/40 text-emerald-200 text-sm flex items-center justify-between">
          <div className="flex items-center gap-2">
            <CheckCircle className="w-4 h-4 text-emerald-400 flex-shrink-0" />
            <span>{uploadSuccessMessage}</span>
          </div>
          <button onClick={() => setUploadSuccessMessage(null)} className="text-xs underline hover:text-emerald-100 ml-3">Dismiss</button>
        </div>
      )}

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

      {/* Uploaded Bidder Documents */}
      <div className="rounded-xl border border-zinc-800/80 bg-zinc-900/40 p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold text-zinc-200 flex items-center gap-2">
            <FileText className="w-4 h-4 text-blue-400" />
            Uploaded Bidder Documents ({documents.length})
          </h2>
          <label className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-xs font-medium rounded-lg cursor-pointer transition-colors border border-zinc-700">
            <Upload className="w-3.5 h-3.5" />
            {getUploadButtonText()}
            <input type="file" onChange={handleDocUpload} disabled={uploadingDoc} className="hidden" />
          </label>
        </div>

        {documents.length === 0 ? (
          <div className="p-8 text-center rounded-xl bg-zinc-950/40 border border-zinc-800/60">
            <FileText className="w-8 h-8 text-zinc-600 mx-auto mb-2" />
            <p className="text-sm text-zinc-400">No documents uploaded for this bidder yet.</p>
            <p className="text-xs text-zinc-500 mt-1">Upload a bidder dossier or PDF document to extract statutory facts and verify compliance.</p>
          </div>
        ) : (
          <div className="overflow-x-auto rounded-xl border border-zinc-800/80 bg-zinc-950/40">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-zinc-800 text-xs text-zinc-400 font-semibold bg-zinc-950/60">
                  <th className="px-5 py-3">Document Filename</th>
                  <th className="px-5 py-3">Document Type</th>
                  <th className="px-5 py-3">File Size</th>
                  <th className="px-5 py-3">Status</th>
                  <th className="px-5 py-3">Facts Extracted</th>
                  <th className="px-5 py-3">Uploaded</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-800/60">
                {documents.map((doc, idx) => {
                  const factsCount = doc.facts_count ?? (doc.extracted_facts ? doc.extracted_facts.length : (doc.facts ? doc.facts.length : 0));
                  const formattedSize = doc.size_bytes ? (doc.size_bytes > 1048576 ? `${(doc.size_bytes / 1048576).toFixed(1)} MB` : `${Math.round(doc.size_bytes / 1024)} KB`) : '—';
                  const formattedDate = doc.created_at ? new Date(doc.created_at).toLocaleString() : '—';
                  return (
                    <tr key={doc.id || idx} className="hover:bg-zinc-800/30 transition-colors">
                      <td className="px-5 py-3 font-medium text-zinc-200">
                        <div className="flex items-center gap-2">
                          <FileText className="w-4 h-4 text-blue-400 flex-shrink-0" />
                          <span className="font-mono text-xs truncate max-w-[240px]" title={doc.filename}>{doc.filename}</span>
                        </div>
                      </td>
                      <td className="px-5 py-3">
                        <span className="px-2 py-0.5 rounded text-[11px] font-mono bg-zinc-800 text-zinc-300 border border-zinc-700">
                          {doc.document_type || 'FINANCIAL_STATEMENT'}
                        </span>
                      </td>
                      <td className="px-5 py-3 text-xs text-zinc-400">{formattedSize}</td>
                      <td className="px-5 py-3">
                        {getStatusBadge(doc.status || 'PROCESSED')}
                      </td>
                      <td className="px-5 py-3 text-xs">
                        {factsCount > 0 ? (
                          <span className="inline-flex items-center gap-1 text-emerald-400 font-medium">
                            <CheckCircle className="w-3.5 h-3.5" />
                            {factsCount} Facts Extracted
                          </span>
                        ) : doc.status === 'PENDING' ? (
                          <span className="inline-flex items-center gap-1 text-zinc-400 font-medium">
                            <Clock className="w-3.5 h-3.5 text-zinc-500" />
                            Facts unavailable
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-zinc-400 font-medium">
                            0 Facts
                          </span>
                        )}
                        {doc.mismatches && doc.mismatches.length > 0 && (
                          <button
                            onClick={() => {
                              setSelectedDocForMismatch(doc);
                              setMismatchModalOpen(true);
                            }}
                            className="mt-1 text-[11px] text-amber-400 hover:text-amber-300 underline font-semibold flex items-center gap-1 cursor-pointer transition-colors"
                            title="Inspect field, claimed vs verified values, and explanation"
                          >
                            <AlertTriangle className="w-3 h-3 text-amber-400 flex-shrink-0" />
                            <span>{doc.mismatches.length} mismatch(es) detected → View Details</span>
                          </button>
                        )}
                      </td>
                      <td className="px-5 py-3 text-xs text-zinc-400 whitespace-nowrap">{formattedDate}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
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

      {/* Mismatch & Risk Signal Detail Modal */}
      <MismatchDetailModal
        isOpen={mismatchModalOpen}
        onClose={() => {
          setMismatchModalOpen(false);
          setSelectedDocForMismatch(null);
        }}
        documentFilename={selectedDocForMismatch?.filename}
        mismatches={selectedDocForMismatch?.mismatches as unknown as MismatchDetailItem[] || []}
      />

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
