"use client";

import React, { useState, useEffect } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { 
  FileText, Users, CheckCircle, AlertTriangle, XCircle, 
  Clock, Plus, ArrowLeft, RefreshCw, Sparkles, AlertCircle
} from "lucide-react";
import { api } from "@/services/api";
import { demoStore, DemoTender } from "@/services/demo-store";
import { BidderRead, RequirementRead } from "@/services/types";
import { RequirementAddModal } from "@/components/ui/RequirementAddModal";
import { JobProgressDrawer } from "@/components/ui/JobProgressDrawer";
import { SessionRequired } from "@/components/ui/SessionRequired";
import { useAuth } from "@/hooks/useAuth";

export default function TenderDetailPage() {
  const params = useParams();
  const id = params?.id as string;
  const { isAuthenticated, isDemoPreview } = useAuth();

  const [tender, setTender] = useState<DemoTender | null>(null);
  const [bidders, setBidders] = useState<BidderRead[]>([]);
  const [requirements, setRequirements] = useState<RequirementRead[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"bidders" | "requirements">("bidders");

  // Modals & Drawers
  const [showAddReq, setShowAddReq] = useState(false);
  const [showAddBidder, setShowAddBidder] = useState(false);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [extractingReqs, setExtractingReqs] = useState(false);

  // New Bidder Form State
  const [newBidderName, setNewBidderName] = useState("");
  const [newGstin, setNewGstin] = useState("");
  const [newCin, setNewCin] = useState("");
  const [newPan, setNewPan] = useState("");
  const [bidderSubmitting, setBidderSubmitting] = useState(false);
  const [bidderError, setBidderError] = useState<string | null>(null);

  const storedDemoTender = id ? demoStore.getTender(id) : null;
  const isDemo = isDemoPreview || Boolean(storedDemoTender);

  const loadTenderData = async () => {
    if (!id) return;
    setLoading(true);
    setError(null);

    if (isDemo) {
      const match = demoStore.getTender(id);
      if (!match) {
        setTender(null);
        setError("Tender Not Found");
        setLoading(false);
        return;
      }
      setTender(match);
      setBidders(demoStore.getBidders(id));
      setRequirements(demoStore.getRequirements(id));
      setLoading(false);
      return;
    }

    if (!isAuthenticated) {
      setLoading(false);
      return;
    }

    try {
      const [tData, bData, rData] = await Promise.all([
        api.getTender(id),
        api.getBidders(id),
        api.getRequirements(id)
      ]);
      setTender(tData);
      setBidders(bData);
      setRequirements(rData);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to load tender details from backend.";
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadTenderData();
  }, [id, isDemo, isAuthenticated]);

  const handleExtractRequirements = async () => {
    if (!id) return;
    setExtractingReqs(true);
    setError(null);

    if (isDemo) {
      const demoJobId = `job_extract_${Date.now()}`;
      setActiveJobId(demoJobId);

      const timeoutPromise = new Promise<never>((_, reject) => {
        setTimeout(() => {
          reject(new Error("Synthetic extraction timed out (8s limit exceeded). Please retry."));
        }, 8000);
      });

      try {
        await Promise.race([
          demoStore.extractCriteria(id, demoJobId),
          timeoutPromise,
        ]);
        await loadTenderData();
        setActiveTab("requirements");
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : "Failed to extract criteria.";
        setError(msg);
        demoStore.updateJob(demoJobId, {
          status: 'FAILED',
          error_message: msg,
        });
      } finally {
        setExtractingReqs(false);
      }
      return;
    }

    try {
      const res = await api.extractRequirements(id);
      if (res.id) {
        setActiveJobId(res.id);
      } else {
        await loadTenderData();
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

    if (isDemo) {
      demoStore.createBidder(id, {
        bidder_name: newBidderName.trim(),
        gstin: newGstin.trim() || undefined,
        cin: newCin.trim() || undefined,
        pan: newPan.trim() || undefined,
      });
      await loadTenderData();
      setShowAddBidder(false);
      setNewBidderName("");
      setNewGstin("");
      setNewCin("");
      setNewPan("");
      setBidderSubmitting(false);
      return;
    }

    try {
      const created = await api.createBidder(id, {
        bidder_name: newBidderName.trim(),
        gstin: newGstin.trim() || undefined,
        cin: newCin.trim() || undefined,
        pan: newPan.trim() || undefined
      });
      setBidders(prev => [...prev, created]);
      setShowAddBidder(false);
      setNewBidderName("");
      setNewGstin("");
      setNewCin("");
      setNewPan("");
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

  if (!isAuthenticated && !isDemo) {
    return (
      <SessionRequired
        title="Session Required"
        description="To inspect this procurement tender and execute live deterministic evaluations, connect an authorized Bearer token or explore in the Demo Workspace."
      />
    );
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="flex flex-col items-center gap-3">
          <RefreshCw className="w-8 h-8 text-blue-500 animate-spin" />
          <p className="text-zinc-400 text-sm">Retrieving authoritative tender state...</p>
        </div>
      </div>
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
            <button onClick={loadTenderData} className="px-4 py-2 bg-rose-600 hover:bg-rose-500 text-white rounded-lg text-sm font-medium">
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

      {/* Attached RFP Document Banner */}
      {tender?.attached_file && (
        <div className="p-4 rounded-xl bg-slate-900/80 border border-slate-800 flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <FileText className="w-8 h-8 text-indigo-400 flex-shrink-0" />
            <div>
              <div className="flex items-center gap-2">
                <h4 className="text-sm font-semibold text-white font-mono">{tender.attached_file.filename}</h4>
                <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-amber-950 text-amber-300 border border-amber-800/60 font-bold">
                  SYNTHETIC DEMO DATA
                </span>
              </div>
              <p className="text-xs text-slate-400 font-mono mt-0.5">
                {(tender.attached_file.size_bytes / 1024 / 1024).toFixed(2)} MB • {tender.attached_file.content_type} • Uploaded {new Date(tender.attached_file.uploaded_at).toLocaleDateString()}
              </p>
            </div>
          </div>
          <span className="text-xs text-indigo-400 font-mono font-medium">RFP Document Attached</span>
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
        <div className="space-y-4">
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
                      <td className="px-5 py-3.5 text-xs text-zinc-300">
                        {r.operator} <span className="font-mono text-amber-300">{String(r.expected_value ?? "")}</span>
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
          onAdded={loadTenderData}
        />
      )}

      {/* Job Progress Drawer */}
      {activeJobId && (
        <JobProgressDrawer
          jobId={activeJobId}
          isOpen={!!activeJobId}
          onClose={() => {
            setActiveJobId(null);
            loadTenderData();
          }}
          onComplete={loadTenderData}
        />
      )}
    </div>
  );
}
