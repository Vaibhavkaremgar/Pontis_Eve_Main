import React from "react";
import axios from "axios";
import { CheckCircle2 } from "lucide-react";
import { toast } from "sonner";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const storageKey = (candidateId) => `eve:application-follow-up:${candidateId}`;

function readPending(candidateId) {
  if (!candidateId) return null;
  try { return JSON.parse(sessionStorage.getItem(storageKey(candidateId)) || "null"); }
  catch { return null; }
}

function clearPending(candidateId) {
  try { sessionStorage.removeItem(storageKey(candidateId)); } catch { /* storage is optional */ }
}

/**
 * Keeps the external application navigation unchanged, then asks for a
 * candidate-confirmed outcome when the Eve tab is visible again.
 */
export function useApplicationFollowUp(candidateId, onApplied) {
  const [pendingJob, setPendingJob] = React.useState(null);
  const [saving, setSaving] = React.useState(false);

  const revealPending = React.useCallback(() => {
    const pending = readPending(candidateId);
    if (pending?.id) setPendingJob(pending);
  }, [candidateId]);

  React.useEffect(() => {
    const handleReturn = () => {
      if (document.visibilityState === "visible") revealPending();
    };
    window.addEventListener("focus", handleReturn);
    document.addEventListener("visibilitychange", handleReturn);
    // A refresh after completing an application is also a return to Eve.
    if (document.visibilityState === "visible") revealPending();
    return () => {
      window.removeEventListener("focus", handleReturn);
      document.removeEventListener("visibilitychange", handleReturn);
    };
  }, [revealPending]);

  const openApplication = React.useCallback((job) => {
    if (!job?.job_url) {
      toast.error("Application link is not available for this job.");
      return;
    }
    try {
      sessionStorage.setItem(storageKey(candidateId), JSON.stringify({
        id: job.id, title: job.title, company: job.company,
      }));
    } catch { /* The application page should still open if storage is unavailable. */ }
    window.open(job.job_url, "_blank", "noopener,noreferrer");
  }, [candidateId]);

  const answer = React.useCallback(async (applied) => {
    if (!pendingJob || saving) return;
    if (!applied) {
      clearPending(candidateId);
      setPendingJob(null);
      return;
    }
    setSaving(true);
    try {
      // The endpoint uses COALESCE and a single recommendation row, so a
      // repeated confirmation cannot create duplicate tracked/apply records.
      await axios.post(`${API}/candidate/${candidateId}/jobs/${pendingJob.id}/apply`);
      clearPending(candidateId);
      setPendingJob(null);
      await onApplied?.(pendingJob.id);
      toast.success("Job moved to Tracked Jobs.");
    } catch {
      toast.error("Couldn't save your application. Please try again.");
    } finally {
      setSaving(false);
    }
  }, [candidateId, onApplied, pendingJob, saving]);

  const modal = pendingJob ? (
    <div className="fixed inset-0 z-[70] flex items-end justify-center bg-black/35 p-4 sm:items-center" role="dialog" aria-modal="true" aria-labelledby="application-follow-up-title" data-testid="application-follow-up-modal">
      <div className="w-full max-w-md rounded-2xl bg-[#FBFBF9] p-5 shadow-2xl sm:p-6">
        <div className="flex items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[#E7F2E4] text-[#2E7538]"><CheckCircle2 className="h-5 w-5" /></span>
          <div>
            <h2 id="application-follow-up-title" className="text-[16px] font-medium text-[#1F1F1F]">Have you applied for this job?</h2>
            {(pendingJob.title || pendingJob.company) && <p className="mt-1 text-[13px] text-[#5D5D5A]">{[pendingJob.title, pendingJob.company].filter(Boolean).join(" at ")}</p>}
          </div>
        </div>
        <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button type="button" disabled={saving} onClick={() => answer(false)} className="rounded-xl bg-black/[.05] px-4 py-2.5 text-[13px] font-medium text-[#30302E] hover:bg-black/[.08] disabled:opacity-50">No</button>
          <button type="button" disabled={saving} onClick={() => answer(true)} className="rounded-xl bg-[#1F1F1F] px-4 py-2.5 text-[13px] font-medium text-white hover:bg-black disabled:opacity-50">{saving ? "Saving…" : "Yes, I Applied"}</button>
        </div>
      </div>
    </div>
  ) : null;

  return { openApplication, applicationFollowUpModal: modal };
}
