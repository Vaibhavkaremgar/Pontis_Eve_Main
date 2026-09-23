import React from "react";
import axios from "axios";
import { BriefcaseBusiness, CheckCircle2, X } from "lucide-react";
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

function markEveLeft(candidateId) {
  const pending = readPending(candidateId);
  if (!pending?.id) return;
  try {
    sessionStorage.setItem(storageKey(candidateId), JSON.stringify({ ...pending, leftEve: true }));
  } catch { /* storage is optional */ }
}

/**
 * Keeps the external application navigation unchanged, then asks for a
 * candidate-confirmed outcome when the Eve tab is visible again.
 */
export function useApplicationFollowUp(candidateId, onApplied) {
  const [pendingJob, setPendingJob] = React.useState(null);
  const [saving, setSaving] = React.useState(false);
  const eveWasInactive = React.useRef(false);

  const revealPending = React.useCallback(() => {
    const pending = readPending(candidateId);
    if (pending?.id && pending.leftEve) setPendingJob(pending);
  }, [candidateId]);

  React.useEffect(() => {
    const handleVisibilityChange = () => {
      if (document.visibilityState === "hidden") {
        eveWasInactive.current = true;
        markEveLeft(candidateId);
      } else if (eveWasInactive.current) {
        revealPending();
      }
    };
    const handleFocus = () => {
      // A focus event can occur while the external tab is opening. Only show
      // the question after Eve has first been inactive.
      if (eveWasInactive.current && document.visibilityState === "visible") {
        revealPending();
      }
    };
    window.addEventListener("focus", handleFocus);
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      window.removeEventListener("focus", handleFocus);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [candidateId, revealPending]);

  const openApplication = React.useCallback((job) => {
    if (!job?.job_url) {
      toast.error("Application link is not available for this job.");
      return;
    }
    try {
      sessionStorage.setItem(storageKey(candidateId), JSON.stringify({
        id: job.id, title: job.title, company: job.company, leftEve: false,
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
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-[#172033]/55 p-4 backdrop-blur-[2px] sm:p-6" role="dialog" aria-modal="true" aria-labelledby="application-follow-up-title" data-testid="application-follow-up-modal">
      <div className="relative w-full max-w-2xl overflow-hidden rounded-[28px] border border-white/70 bg-[#FBFBF9] shadow-[0_24px_70px_rgba(22,31,52,0.28)]">
        <button type="button" onClick={() => answer(false)} disabled={saving} aria-label="Close application follow-up" className="absolute right-4 top-4 z-10 flex h-10 w-10 items-center justify-center rounded-full text-[#5D5D5A] transition-colors hover:bg-black/[0.06] hover:text-[#1F1F1F] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#62578F] disabled:opacity-50 sm:right-5 sm:top-5"><X className="h-5 w-5" strokeWidth={2.25} /></button>
        <div className="grid min-h-[460px] md:grid-cols-[0.78fr_1.22fr]">
          <aside className="hidden flex-col justify-between bg-[linear-gradient(145deg,#EEF3FF_0%,#E7E3F0_100%)] p-9 md:flex">
            <div>
              <span className="flex h-14 w-14 items-center justify-center rounded-2xl bg-white/70 text-[#62578F] shadow-sm"><BriefcaseBusiness className="h-7 w-7" /></span>
              <h3 className="mt-7 text-[27px] font-semibold leading-tight tracking-[-0.03em] text-[#292342]">Keep track of your progress</h3>
            </div>
            <p className="max-w-[220px] text-[15px] leading-6 text-[#5B5870]">Confirming an application helps keep your job search organized in one place.</p>
          </aside>
          <div className="flex min-h-[460px] flex-col p-6 sm:p-9 md:p-10">
            <div className="pr-10 sm:pr-12">
              <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-[#E7E3F0] text-[#62578F]"><BriefcaseBusiness className="h-6 w-6" /></span>
              <h2 id="application-follow-up-title" className="mt-5 text-[28px] font-semibold leading-[1.14] tracking-[-0.035em] text-[#1F1F1F] sm:text-[34px]">Have you applied for this job?</h2>
              <p className="mt-3 max-w-lg text-[15px] leading-6 text-[#5D5D5A] sm:text-[16px]">Let us know if you have applied. Selecting <span className="font-medium text-[#302D3D]">Yes, I Applied</span> will move this job to Tracked Jobs.</p>
            </div>

            <div className="mt-7 rounded-2xl border border-black/[0.07] bg-white p-5 shadow-[0_3px_12px_rgba(31,31,31,0.035)] sm:p-6">
              <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[#7B6FB8]">Application</p>
              <h3 className="mt-2 text-[18px] font-semibold leading-snug text-[#1F1F1F] sm:text-[20px]">{pendingJob.title || "This job"}</h3>
              {pendingJob.company && <p className="mt-1 text-[15px] font-medium text-[#5D5D5A]">{pendingJob.company}</p>}
            </div>

            <div className="mt-auto grid gap-3 pt-7 sm:grid-cols-2">
              <button type="button" disabled={saving} onClick={() => answer(false)} className="min-h-[76px] rounded-2xl border border-black/[0.10] bg-white px-5 py-3 text-left transition-colors hover:bg-black/[0.025] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#62578F] disabled:opacity-50">
                <span className="block text-[15px] font-semibold text-[#30302E]">Not Applied Yet</span>
                <span className="mt-1 block text-[12.5px] text-[#777572]">Keep it in Jobs for You</span>
              </button>
              <button type="button" disabled={saving} onClick={() => answer(true)} className="min-h-[76px] rounded-2xl bg-[#62578F] px-5 py-3 text-left text-white shadow-[0_8px_18px_rgba(98,87,143,0.25)] transition-colors hover:bg-[#514875] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#62578F] disabled:opacity-50">
                <span className="flex items-center gap-2 text-[15px] font-semibold"><CheckCircle2 className="h-5 w-5" />{saving ? "Saving..." : "Yes, I Applied"}</span>
                <span className="mt-1 block text-[12.5px] text-white/80">Move to Tracked Jobs</span>
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  ) : null;

  return { openApplication, applicationFollowUpModal: modal };
}
