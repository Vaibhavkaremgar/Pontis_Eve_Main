import React from "react";
import axios from "axios";
import { motion, useMotionValue, useTransform, animate } from "framer-motion";
import { MapPin, X, Heart, ExternalLink, ChevronLeft } from "lucide-react";
import { toast } from "sonner";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;
const SWIPE_THRESHOLD = 100;

// Normalize raw job text: strip leading/trailing whitespace, collapse blank lines,
// remove lines that are just "{" or "}" (raw object artifacts).
function cleanText(str) {
  if (!str || typeof str !== "string") return "";
  return str
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l !== "{" && l !== "}" && l !== "-" && l !== "- {" && l !== "- }")
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function normalizeSkills(skills) {
  if (!Array.isArray(skills)) return [];
  return skills
    .map((s) => (typeof s === "string" ? s : s?.name ?? ""))
    .filter(Boolean);
}

function MatchBadge({ score }) {
  if (score == null) return null;
  const pct = Math.round(score * (score <= 1 ? 100 : 1));
  return (
    <span className="text-[11px] font-medium text-[#2E7538] bg-[#E7F2E4] rounded-full px-2.5 py-1 shrink-0">
      {pct}% match
    </span>
  );
}

function SkillPill({ label }) {
  return (
    <span className="bg-black/[0.04] text-[#4A4A48] text-[11.5px] px-2.5 py-1 rounded-full font-normal">
      {label}
    </span>
  );
}

// ─── Job Detail Modal ────────────────────────────────────────────────────────

function JobDetailModal({ job, onClose, onApply, onNotInterested, applying }) {
  const skills = normalizeSkills(job.skills);
  const matchPct = job.match_score != null
    ? Math.round(job.match_score * (job.match_score <= 1 ? 100 : 1))
    : null;

  const description = cleanText(job.description);
  const requirements = cleanText(job.requirements);

  const getMatchReason = () => {
    const r = job.match_reason;
    if (!r) return "";
    if (typeof r === "string") return r;
    if (typeof r === "object") return r.type || r.reason || "";
    return String(r);
  };
  const matchReason = getMatchReason();

  return (
    <div className="absolute inset-0 z-20 bg-[#FBFBF9] flex flex-col overflow-hidden">
      {/* Header */}
      <div className="shrink-0 flex items-center gap-3 px-6 pt-5 pb-4 border-b border-black/[0.05]">
        <button
          onClick={onClose}
          className="p-1.5 rounded-lg text-[#4A4A48] hover:bg-black/[0.04] transition-colors"
          aria-label="Back"
        >
          <ChevronLeft className="w-4 h-4" strokeWidth={1.75} />
        </button>
        <div className="flex-1 min-w-0">
          <h2 className="text-[15px] font-medium text-[#1F1F1F] truncate">{job.title}</h2>
          <p className="text-[12px] text-[#9A9A98] font-normal truncate">{job.company}</p>
        </div>
        {matchPct != null && <MatchBadge score={job.match_score} />}
      </div>

      {/* Scrollable body */}
      <div className="flex-1 overflow-y-auto eve-scroll px-6 py-5 space-y-5">
        {/* Overview */}
        <div className="space-y-1.5">
          {job.location && (
            <p className="flex items-center gap-1.5 text-[12.5px] text-[#4A4A48] font-normal">
              <MapPin className="w-3.5 h-3.5 text-[#9A9A98] shrink-0" strokeWidth={1.5} />
              {job.location}
            </p>
          )}
          {job.salary && (
            <p className="text-[12.5px] font-medium text-[#1F1F1F]">{job.salary}</p>
          )}
          {job.experience_required && (
            <p className="text-[12.5px] text-[#4A4A48] font-normal">{job.experience_required}</p>
          )}
        </div>

        <div className="border-t border-black/[0.05]" />

        {/* Skills */}
        {skills.length > 0 && (
          <div>
            <p className="text-[12px] font-medium text-[#1F1F1F] mb-2">Skills</p>
            <div className="flex flex-wrap gap-1.5">
              {skills.map((sk) => (
                <span key={sk} className="bg-black/[0.04] text-[#4A4A48] text-[12px] px-2.5 py-1 rounded-full font-normal">
                  {sk}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Description */}
        {description && (
          <div>
            <p className="text-[12px] font-medium text-[#1F1F1F] mb-1.5">Job Description</p>
            <p className="text-[13px] text-[#4A4A48] leading-relaxed whitespace-pre-line font-normal">
              {description}
            </p>
          </div>
        )}

        {/* Requirements */}
        {requirements && (
          <div>
            <p className="text-[12px] font-medium text-[#1F1F1F] mb-1.5">Requirements</p>
            <p className="text-[13px] text-[#4A4A48] leading-relaxed whitespace-pre-line font-normal">
              {requirements}
            </p>
          </div>
        )}

        {/* Why you match */}
        {matchReason && (
          <div className="bg-[#F4F4F2] rounded-xl px-4 py-3">
            <p className="text-[11.5px] font-medium text-[#1F1F1F] mb-1">Why you match</p>
            <p className="text-[12.5px] text-[#4A4A48] leading-relaxed font-normal">{matchReason}</p>
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="shrink-0 px-6 py-4 border-t border-black/[0.05] flex gap-3">
        <button
          onClick={onNotInterested}
          className="flex-1 py-2.5 rounded-xl bg-black/[0.04] text-[#4A4A48] text-[13px] font-normal hover:bg-black/[0.08] transition-colors"
        >
          Not Interested
        </button>
        {job.applied ? (
          <span className="flex-1 py-2.5 rounded-xl bg-[#E7F2E4] text-[#2E7538] text-[13px] font-medium text-center flex items-center justify-center">
            Applied ✓
          </span>
        ) : (
          <button
            onClick={onApply}
            disabled={applying}
            className="flex-1 py-2.5 rounded-xl bg-[#1F1F1F] text-white text-[13px] font-medium hover:bg-black transition-colors disabled:opacity-50 flex items-center justify-center gap-1.5"
          >
            <ExternalLink className="w-3.5 h-3.5" strokeWidth={2} />
            {applying ? "Opening…" : "Apply"}
          </button>
        )}
      </div>
    </div>
  );
}

// ─── Swipe Card ──────────────────────────────────────────────────────────────

function SwipeCard({ job, onSwipeLeft, onSwipeRight, onViewDetail }) {
  const x = useMotionValue(0);
  const rotate = useTransform(x, [-200, 200], [-18, 18]);
  const leftOpacity = useTransform(x, [-SWIPE_THRESHOLD, 0], [1, 0]);
  const rightOpacity = useTransform(x, [0, SWIPE_THRESHOLD], [0, 1]);

  const handleDragEnd = (_, info) => {
    if (info.offset.x > SWIPE_THRESHOLD) {
      animate(x, 500, { duration: 0.3 }).then(onSwipeRight);
    } else if (info.offset.x < -SWIPE_THRESHOLD) {
      animate(x, -500, { duration: 0.3 }).then(onSwipeLeft);
    } else {
      animate(x, 0, { type: "spring", stiffness: 300, damping: 25 });
    }
  };

  const matchPct = job.match_score != null
    ? Math.round(job.match_score * (job.match_score <= 1 ? 100 : 1))
    : null;

  const skills = normalizeSkills(job.skills).slice(0, 5);
  const summary = cleanText(job.description);

  return (
    <motion.div
      style={{ x, rotate }}
      drag="x"
      dragConstraints={{ left: 0, right: 0 }}
      dragElastic={0.8}
      onDragEnd={handleDragEnd}
      className="absolute inset-0 cursor-grab active:cursor-grabbing select-none"
    >
      {/* Left indicator */}
      <motion.div
        style={{ opacity: leftOpacity }}
        className="absolute top-5 left-5 z-10 border-2 border-red-400 text-red-400 rounded-lg px-3 py-1 text-[12px] font-semibold rotate-[-15deg]"
      >
        NOT INTERESTED
      </motion.div>
      {/* Right indicator */}
      <motion.div
        style={{ opacity: rightOpacity }}
        className="absolute top-5 right-5 z-10 border-2 border-[#2E7538] text-[#2E7538] rounded-lg px-3 py-1 text-[12px] font-semibold rotate-[15deg]"
      >
        INTERESTED
      </motion.div>

      {/* Card body */}
      <div
        className="h-full rounded-2xl border border-black/[0.07] bg-white shadow-[0_4px_24px_rgba(0,0,0,0.07)] overflow-hidden flex flex-col"
        onClick={onViewDetail}
      >
        <div className="px-6 pt-6 pb-4 flex-1 overflow-y-auto eve-scroll">
          {/* Title + company + match */}
          <div className="flex items-start justify-between gap-3 mb-4">
            <div className="flex items-center gap-3 min-w-0">
              {job.logo ? (
                <img src={job.logo} alt={job.company} className="w-11 h-11 rounded-xl object-cover shrink-0" />
              ) : (
                <div className="w-11 h-11 rounded-xl bg-[#E7E3F0] flex items-center justify-center shrink-0">
                  <span className="text-[15px] font-medium text-[#7B6FB8]">
                    {(job.company || "?")[0].toUpperCase()}
                  </span>
                </div>
              )}
              <div className="min-w-0">
                <h3 className="text-[15px] font-semibold text-[#1F1F1F] leading-tight truncate">{job.title}</h3>
                <p className="text-[12.5px] text-[#9A9A98] mt-0.5 font-normal truncate">{job.company}</p>
              </div>
            </div>
            {matchPct != null && (
              <span className="shrink-0 text-[12px] font-semibold text-[#2E7538] bg-[#E7F2E4] rounded-full px-2.5 py-1">
                {matchPct}%
              </span>
            )}
          </div>

          {job.location && (
            <p className="flex items-center gap-1.5 text-[12.5px] text-[#4A4A48] mb-2 font-normal">
              <MapPin className="w-3.5 h-3.5 text-[#9A9A98] shrink-0" strokeWidth={1.5} />
              {job.location}
            </p>
          )}

          {job.salary && (
            <p className="text-[12.5px] font-medium text-[#1F1F1F] mb-3">{job.salary}</p>
          )}

          {skills.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mb-4">
              {skills.map((sk) => <SkillPill key={sk} label={sk} />)}
            </div>
          )}

          {summary && (
            <p className="text-[12.5px] text-[#4A4A48] leading-relaxed line-clamp-4 font-normal">
              {summary}
            </p>
          )}
        </div>

        <div className="shrink-0 px-6 py-3 border-t border-black/[0.04]">
          <p className="text-[11.5px] text-[#B5B5B3] text-center font-normal">Tap for full details</p>
        </div>
      </div>
    </motion.div>
  );
}

// ─── Deck ────────────────────────────────────────────────────────────────────

export default function SwipeJobDeck({ jobs, candidateId, onJobsChange }) {
  const [index, setIndex] = React.useState(0);
  const [detailJob, setDetailJob] = React.useState(null);
  const [applying, setApplying] = React.useState(false);
  // actioned: ids removed from deck this session (dismissed or tracked)
  const [actioned, setActioned] = React.useState(new Set());

  // Only show jobs that haven't been actioned this session AND aren't already tracked
  const pending = jobs.filter((j) => !actioned.has(j.id) && !j.tracked);
  const current = pending[index] ?? null;
  const total = pending.length;

  const advance = React.useCallback(() => setIndex((i) => i + 1), []);

  // LEFT SWIPE → dismiss
  const handleSwipeLeft = React.useCallback(async () => {
    if (!current) return;
    const id = current.id;
    setActioned((s) => new Set(s).add(id));
    advance();
    try {
      await axios.post(`${API}/candidate/${candidateId}/jobs/${id}/dismiss`);
      onJobsChange?.();
    } catch {
      // silent — local state already updated
    }
  }, [current, candidateId, onJobsChange, advance]);

  // RIGHT SWIPE → track (persist to backend before removing card)
  const handleSwipeRight = React.useCallback(async () => {
    if (!current) return;
    const id = current.id;
    try {
      await axios.post(`${API}/candidate/${candidateId}/jobs/${id}/track`);
      setActioned((s) => new Set(s).add(id));
      advance();
      onJobsChange?.();
    } catch {
      toast.error("Couldn't save this job. Please try again.");
      // Card stays — do NOT advance
    }
  }, [current, candidateId, onJobsChange, advance]);

  // APPLY → validate job_url → call apply endpoint → open URL
  const handleApply = React.useCallback(async () => {
    if (!detailJob || applying) return;

    if (!detailJob.job_url) {
      toast.error("Application link is not available for this job.");
      return;
    }

    setApplying(true);
    try {
      await axios.post(`${API}/candidate/${candidateId}/jobs/${detailJob.id}/apply`);
      onJobsChange?.();
      window.open(detailJob.job_url, "_blank", "noopener,noreferrer");
      // Update local detail view to show Applied
      setDetailJob((j) => ({ ...j, applied: true }));
    } catch {
      toast.error("Couldn't record your application. Please try again.");
    } finally {
      setApplying(false);
    }
  }, [detailJob, applying, candidateId, onJobsChange]);

  // NOT INTERESTED from detail modal → dismiss
  const handleNotInterestedFromDetail = React.useCallback(async () => {
    if (!detailJob) return;
    const id = detailJob.id;
    setDetailJob(null);
    setActioned((s) => new Set(s).add(id));
    advance();
    try {
      await axios.post(`${API}/candidate/${candidateId}/jobs/${id}/dismiss`);
      onJobsChange?.();
    } catch {
      // silent
    }
  }, [detailJob, candidateId, onJobsChange, advance]);

  if (!jobs) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-[#B5B5B3] animate-pulse" />
          <span className="w-2 h-2 rounded-full bg-[#B5B5B3] animate-pulse" style={{ animationDelay: "120ms" }} />
          <span className="w-2 h-2 rounded-full bg-[#B5B5B3] animate-pulse" style={{ animationDelay: "240ms" }} />
        </div>
      </div>
    );
  }

  if (total === 0 || index >= total) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-2 px-8 text-center">
        <p className="text-[15px] font-medium text-[#1F1F1F]">You're all caught up.</p>
        <p className="text-[13px] text-[#9A9A98] font-normal">No more recommended jobs right now.</p>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col min-h-0 relative">
      {/* Detail modal overlay */}
      {detailJob && (
        <JobDetailModal
          job={detailJob}
          onClose={() => setDetailJob(null)}
          onApply={handleApply}
          onNotInterested={handleNotInterestedFromDetail}
          applying={applying}
        />
      )}

      {/* Header */}
      <div className="shrink-0 px-6 pt-5 pb-3 flex items-center justify-between">
        <h2 className="text-[14px] font-medium text-[#1F1F1F]">Recommended for you</h2>
        <span className="text-[12px] text-[#9A9A98] font-normal">{index + 1} of {total}</span>
      </div>

      {/* Card stack */}
      <div className="flex-1 relative mx-6 mb-4 min-h-0">
        {index + 1 < total && (
          <div className="absolute inset-x-3 inset-y-2 rounded-2xl border border-black/[0.05] bg-white shadow-sm" />
        )}
        <SwipeCard
          key={current.id}
          job={current}
          onSwipeLeft={handleSwipeLeft}
          onSwipeRight={handleSwipeRight}
          onViewDetail={() => setDetailJob(current)}
        />
      </div>

      {/* Action buttons */}
      <div className="shrink-0 px-6 pb-6 flex gap-3">
        <button
          onClick={handleSwipeLeft}
          className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-xl border border-black/[0.08] bg-white text-[#4A4A48] text-[13px] font-normal hover:bg-black/[0.03] transition-colors"
        >
          <X className="w-4 h-4" strokeWidth={2} />
          Not Interested
        </button>
        <button
          onClick={handleSwipeRight}
          className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-xl bg-[#1F1F1F] text-white text-[13px] font-medium hover:bg-black transition-colors"
        >
          <Heart className="w-4 h-4" strokeWidth={2} />
          Interested
        </button>
      </div>
    </div>
  );
}
