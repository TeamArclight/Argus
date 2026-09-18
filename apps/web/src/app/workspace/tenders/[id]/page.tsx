"use client";

import React, { useState, useEffect, useCallback, useRef } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { 
  FileText, Sparkles, Plus, CheckCircle, AlertTriangle, Clock, 
  ArrowLeft, RefreshCw, Users, AlertCircle, XCircle, Trash2, Upload
} from "lucide-react";
import { api } from "@/services/api";
import { TenderRead, BidderRead, TenderRequirementRead } from "@/services/types";
import { DocumentRead } from "@/types/api";
import { RequirementAddModal } from "@/components/ui/RequirementAddModal";
import { JobProgressDrawer } from "@/components/ui/JobProgressDrawer";
import { SessionRequired } from "@/components/ui/SessionRequired";
import { ClauseIntelligencePanel } from "@/components/ui/ClauseIntelligencePanel";
import { useAuth } from "@/hooks/useAuth";
import { formatExpectedCondition } from "@/lib/formatters";

interface TenderWithFile extends TenderRead {
  attached_file?: {
    id?: string;
    filename?: string;
    size_bytes?: number | null;
    content_type?: string | null;
    uploaded_at?: number | string | null;
  };
}

function deduplicateRequirements(reqs: TenderRequirementRead[]): TenderRequirementRead[] {
  const sorted = [...reqs].sort((a, b) => (b.is_approved ? 1 : 0) - (a.is_approved ? 1 : 0));
  const seenSigs = new Set<string>();
  const canonical: TenderRequirementRead[] = [];

  for (const r of sorted) {
    const clauseKey = (r.clause || "").trim().toLowerCase();
    const typeKey = (r.requirement_type || "").trim().toUpperCase();
    const fieldKey = (r.field || "").trim().toLowerCase();
    const opKey = (r.operator || "").trim().toUpperCase();
    const valKey = String(r.expected_value ?? "").trim().toLowerCase();

    const clauseTypeSig = clauseKey ? `${clauseKey}::${typeKey}` : null;
    const semanticSig = `${typeKey}::${fieldKey}::${opKey}::${valKey}`;

    if (clauseTypeSig && seenSigs.has(clauseTypeSig)) {
      continue;
    }
    if (seenSigs.has(semanticSig)) {
      continue;
    }

    if (clauseTypeSig) seenSigs.add(clauseTypeSig);
    seenSigs.add(semanticSig);
    canonical.push(r);
  }

  return canonical;
}

const CANONICAL_DEMO_IDS = ["tender_gem_2026_01", "tender_gem_2026_02", "tender_gem_2026_03"];

export default function TenderDetailPage() {
  const params = useParams();
  const id = params?.id as string;
  const { isAuthenticated, isDemoPreview } = useAuth();

  const isCanonicalDemo = Boolean(isDemoPreview && CANONICAL_DEMO_IDS.includes(id));

  const [tender, setTender] = useState<TenderWithFile | null>(null);
  const [bidders, setBidders] = useState<BidderRead[]>([]);
  const [requirements, setRequirements] = useState<TenderRequirementRead[]>([]);
  const [activeTab, setActiveTab] = useState<"bidders" | "requirements" | "intelligence">("bidders");
  const [initialLoading, setInitialLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  const hasLoadedInitialTenderRef = useRef(false);
  const terminalRefreshPerformedRef = useRef<string | null>(null);

  // Modals & Drawers
  const [showAddReq, setShowAddReq] = useState(false);
  const [showAddBidder, setShowAddBidder] = useState(false);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [latestJobFailed, setLatestJobFailed] = useState(false);
  const [extractingReqs, setExtractingReqs] = useState(false);
  const [tenderDocs, setTenderDocs] = useState<DocumentRead[]>([]);
  const [showDeleteDocModal, setShowDeleteDocModal] = useState(false);
  const [deletingDoc, setDeletingDoc] = useState(false);
  const [uploadingDoc, setUploadingDoc] = useState(false);

  // New Bidder Form State
  const [newBidderName, setNewBidderName] = useState("");
  const [newGstin, setNewGstin] = useState("");
  const [newCin, setNewCin] = useState("");
  const [newPan, setNewPan] = useState("");
  const [newUdyam, setNewUdyam] = useState("");
  const [bidderSubmitting, setBidderSubmitting] = useState(false);
  const [bidderError, setBidderError] = useState<string | null>(null);

  const loadTenderData = useCallback(async (options?: { silent?: boolean }) => {
    if (!id) return;
    const isInitial = !hasLoadedInitialTenderRef.current;
    if (isInitial) {
      setInitialLoading(true);
    } else if (!options?.silent) {
      setRefreshing(true);
    }
    setError(null);

    if (!isAuthenticated && !isDemoPreview) {
      setInitialLoading(false);
      setRefreshing(false);
      return;
    }

    try {
      if (isDemoPreview) {
        try {
          const demoStatus = await api.getDemoStatus();
          if (!demoStatus.seeded) {
            await api.seedDemo();
          }
        } catch (seedErr) {
          console.warn('Demo status/seed check error:', seedErr);
        }
      }

      const [tData, bData, rData, dData] = await Promise.all([
        api.getTender(id),
        api.getBidders(id),
        api.getRequirements(id),
        api.getTenderDocuments(id).catch(() => [] as DocumentRead[]),
      ]);
      setTenderDocs(dData || []);
      const firstDoc = dData && dData.length > 0 ? dData[0] : null;
      const typedTData = tData as TenderWithFile;
      const meta = (tData.metadata_json && typeof tData.metadata_json === "object")
        ? (tData.metadata_json as Record<string, unknown>)
        : null;
      const metaAttached = (meta?.attached_file && typeof meta.attached_file === "object")
        ? (meta.attached_file as Record<string, unknown>)
        : null;

      const attachedFile = typedTData.attached_file
        ? { id: firstDoc?.id, ...typedTData.attached_file }
        : firstDoc
        ? {
            id: firstDoc.id,
            filename: firstDoc.filename,
            size_bytes: firstDoc.size_bytes,
            content_type: firstDoc.content_type,
            uploaded_at: firstDoc.created_at,
          }
        : metaAttached
        ? {
            id: typeof meta?.document_id === "string" ? meta.document_id : undefined,
            filename: typeof metaAttached.filename === "string" ? metaAttached.filename : undefined,
            size_bytes: typeof metaAttached.size_bytes === "number" ? metaAttached.size_bytes : undefined,
            content_type: typeof metaAttached.content_type === "string" ? metaAttached.content_type : undefined,
            uploaded_at: typeof metaAttached.uploaded_at === "string" || typeof metaAttached.uploaded_at === "number" ? metaAttached.uploaded_at : undefined,
          }
        : undefined;

      setTender({ ...tData, attached_file: attachedFile });
      setBidders(bData);
      setRequirements(deduplicateRequirements(rData));
      hasLoadedInitialTenderRef.current = true;
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load tender details from backend.";
      setError(msg);
    } finally {
      setInitialLoading(false);
      setRefreshing(false);
    }
  }, [id, isDemoPreview, isAuthenticated]);

  useEffect(() => {
    loadTenderData();
  }, [loadTenderData]);

  useEffect(() => {
    if (typeof window !== "undefined") {
      const hash = window.location.hash;
      if (hash === "#criteria" || hash === "#requirements") {
        setActiveTab("requirements");
        setTimeout(() => {
          document.getElementById("criteria")?.scrollIntoView({ behavior: "smooth" });
        }, 150);
      }
    }
  }, []);

  const handleExtractRequirements = async () => {
    if (!id) return;
    setExtractingReqs(true);
    setLatestJobFailed(false);
    setError(null);

    try {
      const res = await api.extractRequirements(id);
      if (res.id) {
        terminalRefreshPerformedRef.current = null;
        setActiveJobId(res.id);
      } else {
        await loadTenderData({ silent: true });
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Requirement extraction trigger failed.");
    } finally {
      setExtractingReqs(false);
    }
  };

  const handleCreateBidder = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!id || !newBidderName.trim()) return;
    setBidderSubmitting(true);
    setBidderError(null);

    try {
      const created = await api.createBidder(id, {
        bidder_name: newBidderName.trim(),
        gstin: newGstin.trim() || undefined,
        cin: newCin.trim() || undefined,
        pan: newPan.trim() || undefined,
        udyam_number: newUdyam.trim() || undefined,
      });
      setBidders(prev => [...prev, created]);
      setShowAddBidder(false);
      setNewBidderName("");
      setNewGstin("");
      setNewCin("");
      setNewPan("");
      setNewUdyam("");
    } catch (err: unknown) {
      setBidderError(err instanceof Error ? err.message : "Failed to register bidder on backend.");
    } finally {
      setBidderSubmitting(false);
    }
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "COMPLETED":
      case "QUALIFIED":
        return <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"><CheckCircle className="w-3 h-3" /> {status}</span>;
      case "RUNNING":
      case "PENDING":
        return <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-blue-500/10 text-blue-400 border border-blue-500/20"><Clock className="w-3 h-3 animate-spin" /> {status}</span>;
      case "REVIEW_REQUIRED":
      case "MANUAL_REVIEW":
        return <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-amber-500/10 text-amber-400 border border-amber-500/20"><AlertTriangle className="w-3 h-3" /> {status}</span>;
      case "FAILED":
      case "DISQUALIFIED":
        return <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-rose-500/10 text-rose-400 border border-rose-500/20"><XCircle className="w-3 h-3" /> {status}</span>;
      default:
        return <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-zinc-800 text-zinc-400 border border-zinc-700">{status}</span>;
    }
  };

  const handleJobComplete = useCallback(() => {
    if (activeJobId && terminalRefreshPerformedRef.current !== activeJobId) {
      terminalRefreshPerformedRef.current = activeJobId;
      loadTenderData({ silent: true });
    }
  }, [activeJobId, loadTenderData]);

  const handleDeleteDocument = async () => {
    if (!id) return;
    if (isCanonicalDemo) {
      setError("Canonical demo documents cannot be deleted.");
      setShowDeleteDocModal(false);
      return;
    }
    const docId = tender?.attached_file?.id || tenderDocs[0]?.id;
    if (!docId) {
      setError("Cannot find document ID to delete.");
      setShowDeleteDocModal(false);
      return;
    }
    setDeletingDoc(true);
    setError(null);
    try {
      await api.deleteTenderDocument(id, docId);
      setShowDeleteDocModal(false);
      setLatestJobFailed(false);
      setTender(prev => prev ? { ...prev, attached_file: undefined, raw_document_uri: undefined, status: "QUEUED" } : null);
      setTenderDocs([]);
      await loadTenderData({ silent: true });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to delete tender document.");
      setShowDeleteDocModal(false);
    } finally {
      setDeletingDoc(false);
    }
  };

  const handleUploadReplacementDoc = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !id) return;
    setUploadingDoc(true);
    setError(null);
    try {
      await api.uploadTenderDocument(id, file);
      await loadTenderData({ silent: true });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to upload replacement document.");
    } finally {
      setUploadingDoc(false);
      e.target.value = "";
    }
  };

  if (!mounted || (initialLoading && !tender)) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="flex flex-col items-center gap-3">
          <RefreshCw className="w-8 h-8 text-blue-500 animate-spin" />
          <p className="text-zinc-400 text-sm">Retrieving authoritative tender state...</p>
        </div>
      </div>
    );
  }

  if (!isAuthenticated && !isDemoPreview) {
    return (
      <SessionRequired
        title="Session Required"
        description="To inspect this procurement tender and execute live deterministic evaluations, connect an authorized Bearer token or explore in the Demo Workspace."
      />
    );
  }

  if (error && !tender) {
    return (
      <div className="max-w-4xl mx-auto p-6">
        <div className="p-6 rounded-xl bg-rose-950/20 border border-rose-800/40 text-rose-300">
          <div className="flex items-center gap-3 mb-2">
            <AlertCircle className="w-6 h-6 text-rose-400" />
            <h2 className="text-lg font-semibold">Tender Retrieval Failed</h2>
          </div>
          <p className="text-sm text-zinc-400 mb-4">{error}</p>
          <div className="flex gap-3">
            <button onClick={() => loadTenderData()} className="px-4 py-2 bg-rose-600 hover:bg-rose-500 text-white rounded-lg text-sm font-medium">
              Retry Query
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
      {/* Back link & Title */}
      <div>
        <Link href="/workspace/tenders" className="inline-flex items-center gap-2 text-sm text-zinc-400 hover:text-zinc-200 mb-4 transition-colors">
          <ArrowLeft className="w-4 h-4" /> Back to Tenders
        </Link>
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-bold text-zinc-100">{tender?.title || "Tender Detail"}</h1>
              {tender && getStatusBadge(tender.status)}
              {refreshing && (
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-blue-500/10 text-blue-400 border border-blue-500/20 animate-pulse">
                  <RefreshCw className="w-3 h-3 animate-spin" /> Syncing pipeline state...
                </span>
              )}
            </div>
            <p className="text-sm text-zinc-400 mt-1">
              Ref: <span className="font-mono text-zinc-300">{tender?.tender_number}</span> • Authority: <span className="text-zinc-300">{tender?.authority || "Unspecified"}</span>
            </p>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={handleExtractRequirements}
              disabled={extractingReqs}
              className="inline-flex items-center gap-2 px-4 py-2 bg-purple-600 hover:bg-purple-500 text-white text-sm font-medium rounded-lg shadow-sm transition-colors disabled:opacity-50"
            >
              <Sparkles className="w-4 h-4" />
              {extractingReqs
                ? "Extracting..."
                : requirements.length > 0
                ? "Re-run AI Extraction"
                : "Extract AI Criteria"}
            </button>
            <button
              onClick={() => setShowAddBidder(true)}
              className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded-lg shadow-sm transition-colors"
            >
              <Plus className="w-4 h-4" />
              Register Bidder
            </button>
          </div>
        </div>
      </div>

      {error && (
        <div className="p-4 rounded-xl bg-rose-950/20 border border-rose-800/40 text-rose-300 text-sm flex items-center justify-between">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="text-xs underline hover:text-rose-200">Dismiss</button>
        </div>
      )}

      {/* Meta cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <div className="p-4 rounded-xl bg-zinc-900/60 border border-zinc-800/80">
          <p className="text-xs text-zinc-500 font-medium">Estimated Budget</p>
          <p className="text-lg font-semibold text-zinc-200 mt-1">
            {tender?.budget ? `₹ ${Number(tender.budget).toLocaleString("en-IN")}` : "N/A"}
          </p>
        </div>
        <div className="p-4 rounded-xl bg-zinc-900/60 border border-zinc-800/80">
          <p className="text-xs text-zinc-500 font-medium">Submission Deadline</p>
          <p className="text-lg font-semibold text-zinc-200 mt-1">
            {tender?.deadline ? new Date(tender.deadline).toLocaleDateString() : "Open"}
          </p>
        </div>
        <div className="p-4 rounded-xl bg-zinc-900/60 border border-zinc-800/80">
          <p className="text-xs text-zinc-500 font-medium">Registered Bidders</p>
          <p className="text-lg font-semibold text-zinc-200 mt-1">{bidders.length}</p>
        </div>
        <div className="p-4 rounded-xl bg-zinc-900/60 border border-zinc-800/80">
          <p className="text-xs text-zinc-500 font-medium">Extracted Criteria</p>
          <p className="text-lg font-semibold text-zinc-200 mt-1">{requirements.length}</p>
        </div>
      </div>

      {/* Attached RFP Document Banner / Replacement Dropzone */}
      {tender?.attached_file ? (
        <div className="p-4 rounded-xl bg-slate-900/80 border border-slate-800 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <FileText className="w-8 h-8 text-indigo-400 flex-shrink-0" />
            <div>
              <div className="flex items-center gap-2">
                <h4 className="text-sm font-semibold text-white font-mono">{tender.attached_file.filename}</h4>
                {isDemoPreview && (
                  <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-amber-950 text-amber-300 border border-amber-800/60 font-bold">
                    SYNTHETIC DEMO DATA
                  </span>
                )}
              </div>
              <p className="text-xs text-slate-400 font-mono mt-0.5">
                {(((tender.attached_file.size_bytes ?? 0) / 1024 / 1024)).toFixed(2)} MB • {tender.attached_file.content_type} • Uploaded {new Date(tender.attached_file.uploaded_at ?? 0).toLocaleDateString()}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {(tender.status === "FAILED" || latestJobFailed) && (
              <div className="relative group">
                <button
                  type="button"
                  onClick={() => setShowDeleteDocModal(true)}
                  disabled={isCanonicalDemo}
                  className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg transition-colors border ${
                    isCanonicalDemo
                      ? "bg-zinc-800/50 text-zinc-500 border-zinc-700/50 cursor-not-allowed"
                      : "bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 hover:text-rose-300 border-rose-500/30"
                  }`}
                  title={isCanonicalDemo ? "Canonical demo documents cannot be deleted." : "Delete failed document"}
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  Delete Failed Document
                </button>
                {isCanonicalDemo && (
                  <div className="absolute right-0 bottom-full mb-1 hidden group-hover:block z-20 px-2 py-1 text-[11px] font-sans text-amber-300 bg-zinc-900 border border-zinc-700 rounded shadow-lg whitespace-nowrap">
                    Canonical demo documents cannot be deleted.
                  </div>
                )}
              </div>
            )}
            <span className="text-xs text-indigo-400 font-mono font-medium">RFP Document Attached</span>
          </div>
        </div>
      ) : (
        <div className="p-4 rounded-xl bg-zinc-900/60 border border-dashed border-zinc-700 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-zinc-800 flex items-center justify-center text-zinc-400">
              <Upload className="w-4 h-4" />
            </div>
            <div>
              <h4 className="text-sm font-semibold text-zinc-300">No RFP Document Attached</h4>
              <p className="text-xs text-zinc-500 mt-0.5">
                Upload a replacement tender document (PDF) to extract clauses and requirements.
              </p>
            </div>
          </div>
          <div>
            <label className={`inline-flex items-center gap-2 px-3.5 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-xs font-medium rounded-lg cursor-pointer transition-colors shadow-sm ${uploadingDoc ? "opacity-50 pointer-events-none" : ""}`}>
              <Upload className="w-3.5 h-3.5" />
              {uploadingDoc ? "Uploading..." : "Upload Replacement Document"}
              <input
                type="file"
                accept=".pdf"
                className="hidden"
                disabled={uploadingDoc}
                onChange={handleUploadReplacementDoc}
              />
            </label>
          </div>
        </div>
      )}

      {/* Tabs */}
      <div className="border-b border-zinc-800 flex gap-6">
        <button
          onClick={() => setActiveTab("bidders")}
          className={`pb-3 text-sm font-medium border-b-2 transition-colors flex items-center gap-2 ${
            activeTab === "bidders"
              ? "border-blue-500 text-blue-400"
              : "border-transparent text-zinc-400 hover:text-zinc-200"
          }`}
        >
          <Users className="w-4 h-4" />
          Bidders ({bidders.length})
        </button>
        <button
          onClick={() => setActiveTab("requirements")}
          className={`pb-3 text-sm font-medium border-b-2 transition-colors flex items-center gap-2 ${
            activeTab === "requirements"
              ? "border-blue-500 text-blue-400"
              : "border-transparent text-zinc-400 hover:text-zinc-200"
          }`}
        >
          <FileText className="w-4 h-4" />
          Evaluation Criteria ({requirements.length})
        </button>
        <button
          onClick={() => setActiveTab("intelligence")}
          className={`pb-3 text-sm font-medium border-b-2 transition-colors flex items-center gap-2 ${
            activeTab === "intelligence"
              ? "border-purple-500 text-purple-400"
              : "border-transparent text-zinc-400 hover:text-zinc-200"
          }`}
        >
          <Sparkles className="w-4 h-4 text-purple-400" />
          Policy & Clause Intelligence (RAG)
        </button>
      </div>

      {/* Tab Content: Bidders */}
      {activeTab === "bidders" && (
        <div className="space-y-4">
          {bidders.length === 0 ? (
            <div className="p-12 text-center rounded-xl bg-zinc-900/40 border border-zinc-800/80">
              <Users className="w-12 h-12 text-zinc-600 mx-auto mb-3" />
              <h3 className="text-base font-semibold text-zinc-300">No Bidders Registered</h3>
              <p className="text-sm text-zinc-500 max-w-md mx-auto mt-1 mb-4">
                Register participating vendor profiles to begin statutory verification and compliance matrix evaluation.
              </p>
              <button
                onClick={() => setShowAddBidder(true)}
                className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded-lg"
              >
                Register First Bidder
              </button>
            </div>
          ) : (
            <div className="overflow-x-auto rounded-xl border border-zinc-800/80 bg-zinc-900/40">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-zinc-800 text-xs text-zinc-400 font-semibold bg-zinc-950/40">
                    <th className="px-5 py-3">Bidder Organization</th>
                    <th className="px-5 py-3">Statutory Identifiers</th>
                    <th className="px-5 py-3">Evaluation Status</th>
                    <th className="px-5 py-3 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-800/60">
                  {bidders.map((b) => (
                    <tr key={b.id} className="hover:bg-zinc-800/30 transition-colors">
                      <td className="px-5 py-4 font-medium text-zinc-100">
                        <Link href={`/workspace/bidders/${b.id}`} className="hover:text-blue-400 underline-offset-2 hover:underline">
                          {b.bidder_name}
                        </Link>
                      </td>
                      <td className="px-5 py-4 font-mono text-xs text-zinc-400">
                        {b.gstin && <div>GSTIN: {b.gstin}</div>}
                        {b.cin && <div>CIN: {b.cin}</div>}
                        {b.pan && <div>PAN: {b.pan}</div>}
                        {!b.gstin && !b.cin && !b.pan && <span className="text-zinc-600 italic">None registered</span>}
                      </td>
                      <td className="px-5 py-4">
                        {getStatusBadge(b.status)}
                      </td>
                      <td className="px-5 py-4 text-right">
                        <Link
                          href={`/workspace/bidders/${b.id}`}
                          className="px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 rounded-lg text-xs font-medium transition-colors"
                        >
                          View Evaluation →
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Tab Content: Requirements */}
      {activeTab === "requirements" && (
        <div id="criteria" className="space-y-4">
          <div className="flex justify-between items-center">
            <p className="text-sm text-zinc-400">Statutory and technical clauses extracted from tender documentation.</p>
            <button
              onClick={() => setShowAddReq(true)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 rounded-lg text-xs font-medium"
            >
              <Plus className="w-3.5 h-3.5" /> Add Clause
            </button>
          </div>

          {requirements.length === 0 ? (
            <div className="p-8 text-center rounded-xl bg-zinc-900/40 border border-zinc-800/80">
              <FileText className="w-10 h-10 text-zinc-600 mx-auto mb-2" />
              <p className="text-sm text-zinc-400">No evaluation criteria registered.</p>
              <p className="text-xs text-zinc-500 mt-1">Upload tender documents and trigger AI extraction or add clauses manually.</p>
            </div>
          ) : (
            <div className="overflow-x-auto rounded-xl border border-zinc-800/80 bg-zinc-900/40">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-zinc-800 text-xs text-zinc-400 font-semibold bg-zinc-950/40">
                    <th className="px-5 py-3">Clause Reference</th>
                    <th className="px-5 py-3">Type</th>
                    <th className="px-5 py-3">Field / Metric</th>
                    <th className="px-5 py-3">Rule / Expected</th>
                    <th className="px-5 py-3">Mandatory</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-800/60">
                  {requirements.map((r) => (
                    <tr key={r.id} className="hover:bg-zinc-800/30 transition-colors">
                      <td className="px-5 py-3.5 font-medium text-zinc-200">{r.clause}</td>
                      <td className="px-5 py-3.5">
                        <span className="px-2 py-0.5 rounded text-xs font-mono bg-zinc-800 text-zinc-300 border border-zinc-700">
                          {r.requirement_type}
                        </span>
                      </td>
                      <td className="px-5 py-3.5 font-mono text-xs text-zinc-400">{r.field}</td>
                      <td className="px-5 py-3.5 text-xs font-mono text-zinc-300">
                        <span className="text-amber-300">
                          {formatExpectedCondition(r.operator, r.expected_value, {
                            unit: r.unit,
                            requirementType: r.requirement_type,
                            field: r.field,
                          })}
                        </span>
                      </td>
                      <td className="px-5 py-3.5 text-xs">
                        {r.mandatory ? (
                          <span className="text-rose-400 font-semibold">MANDATORY</span>
                        ) : (
                          <span className="text-zinc-500">OPTIONAL</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Tab Content: Policy & Clause Intelligence */}
      {activeTab === "intelligence" && (
        <ClauseIntelligencePanel tenderId={id} isDemo={isDemoPreview} />
      )}

      {/* Add Bidder Modal */}
      {showAddBidder && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
          <div className="w-full max-w-md rounded-2xl bg-zinc-900 border border-zinc-800 p-6 shadow-2xl space-y-4">
            <h3 className="text-lg font-semibold text-zinc-100">Register Bidder for Tender</h3>
            {bidderError && (
              <div className="p-3 rounded-lg bg-rose-950/30 border border-rose-800/50 text-rose-300 text-xs">
                {bidderError}
              </div>
            )}
            <form onSubmit={handleCreateBidder} className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-zinc-400 mb-1">Bidder Organization Name *</label>
                <input
                  type="text"
                  required
                  value={newBidderName}
                  onChange={(e) => setNewBidderName(e.target.value)}
                  placeholder="e.g. Apex Infra Solutions Ltd"
                  className="w-full px-3 py-2 rounded-lg bg-zinc-950 border border-zinc-800 text-zinc-100 text-sm focus:outline-none focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-zinc-400 mb-1">GSTIN</label>
                <input
                  type="text"
                  value={newGstin}
                  onChange={(e) => setNewGstin(e.target.value)}
                  placeholder="e.g. 27AAACA1234A1Z5"
                  className="w-full px-3 py-2 rounded-lg bg-zinc-950 border border-zinc-800 text-zinc-100 text-sm font-mono focus:outline-none focus:border-blue-500"
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-zinc-400 mb-1">CIN</label>
                  <input
                    type="text"
                    value={newCin}
                    onChange={(e) => setNewCin(e.target.value)}
                    placeholder="U12345MH2010PTC123456"
                    className="w-full px-3 py-2 rounded-lg bg-zinc-950 border border-zinc-800 text-zinc-100 text-sm font-mono focus:outline-none focus:border-blue-500"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-zinc-400 mb-1">PAN</label>
                  <input
                    type="text"
                    value={newPan}
                    onChange={(e) => setNewPan(e.target.value)}
                    placeholder="AAACA1234A"
                    className="w-full px-3 py-2 rounded-lg bg-zinc-950 border border-zinc-800 text-zinc-100 text-sm font-mono focus:outline-none focus:border-blue-500"
                  />
                </div>
              </div>
              <div>
                <label className="block text-xs font-medium text-zinc-400 mb-1">UDYAM Number (Optional / MSME)</label>
                <input
                  type="text"
                  value={newUdyam}
                  onChange={(e) => setNewUdyam(e.target.value)}
                  placeholder="e.g. UDYAM-WB-01-0012345 (leave blank if Not Registered)"
                  className="w-full px-3 py-2 rounded-lg bg-zinc-950 border border-zinc-800 text-zinc-100 text-sm font-mono focus:outline-none focus:border-blue-500"
                />
              </div>
              <div className="flex justify-end gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => setShowAddBidder(false)}
                  className="px-4 py-2 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-sm font-medium"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={bidderSubmitting}
                  className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium disabled:opacity-50"
                >
                  {bidderSubmitting ? "Registering..." : "Register Bidder"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Add Criteria Modal */}
      {id && (
        <RequirementAddModal
          tenderId={id}
          isOpen={showAddReq}
          onClose={() => setShowAddReq(false)}
          onAdded={() => loadTenderData({ silent: true })}
        />
      )}

      {/* Job Progress Drawer */}
      {activeJobId && (
        <JobProgressDrawer
          jobId={activeJobId}
          isOpen={!!activeJobId}
          onClose={() => {
            setActiveJobId(null);
            terminalRefreshPerformedRef.current = null;
            loadTenderData({ silent: true });
          }}
          onComplete={handleJobComplete}
          onFailed={() => {
            setLatestJobFailed(true);
            handleJobComplete();
          }}
        />
      )}

      {/* Delete Failed Document Modal */}
      {showDeleteDocModal && (
        <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-6 max-w-md w-full shadow-2xl">
            <div className="flex items-center gap-3 text-rose-400 mb-3">
              <div className="p-2 rounded-lg bg-rose-500/10 border border-rose-500/20">
                <Trash2 className="w-5 h-5" />
              </div>
              <h3 className="text-lg font-semibold text-zinc-100">Delete failed tender document?</h3>
            </div>
            <p className="text-sm text-zinc-400 mb-6 leading-relaxed">
              This will remove the uploaded source document and any unapproved extracted criteria. Processing history and audit records will be preserved.
            </p>
            <div className="flex justify-end gap-3">
              <button
                type="button"
                onClick={() => setShowDeleteDocModal(false)}
                disabled={deletingDoc}
                className="px-4 py-2 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-sm font-medium transition-colors"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleDeleteDocument}
                disabled={deletingDoc}
                className="px-4 py-2 rounded-lg bg-rose-600 hover:bg-rose-500 text-white text-sm font-medium transition-colors disabled:opacity-50 inline-flex items-center gap-2"
              >
                {deletingDoc ? (
                  <>
                    <RefreshCw className="w-4 h-4 animate-spin" /> Deleting...
                  </>
                ) : (
                  <>
                    <Trash2 className="w-4 h-4" /> Delete Document
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
