import React from "react";
import axios from "axios";
import DOMPurify from "dompurify";
import { motion, useMotionValue, useTransform, animate } from "framer-motion";
import { MapPin, X, Heart, ExternalLink, ChevronLeft, Check } from "lucide-react";
import { toast } from "sonner";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;
const SWIPE_THRESHOLD = 100;

export const NOT_INTERESTED_REASONS = [
  "Salary is too low",
  "Location is not suitable",
  "Remote/onsite preference",
  "Experience requirement mismatch",
  "Skills/technology mismatch",
  "Role/title not suitable",
  "Company not preferred",
  "Industry/domain not preferred",
  "Not looking for a job now",
  "Other",
];

function sanitizeHtml(str) {
  if (!str || typeof str !== "string") return "";
  return DOMPurify.sanitize(str, { USE_PROFILES: { html: true } });
}

// For plain-text preview snippets (swipe card summary, job list card)
function cleanText(str) {
  if (!str || typeof str !== "string") return "";
  const stripped = str.replace(/<[^>]+>/g, " ").replace(/&[a-z]+;/gi, " ");
  return stripped
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l !== "{" && l !== "}" && l !== "-" && l !== "- {" && l !== "- }")
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

// Extract 2–3 meaningful bullet points from responsibilities, requirements, or description
function extractBullets(job) {
  const source = job.responsibilities || job.requirements || job.description || "";
  const text = cleanText(source);
  if (!text) return [];

  // Try splitting on list-like patterns first
  const lines = text
    .split(/\n|(?<=\.)\s+(?=[A-Z•\-])|[•·]\s*/)
    .map((l) => l.replace(/^[-–—*•·\d.]+\s*/, "").trim())
    .filter((l) => l.length > 20 && l.length < 160);

  if (lines.length >= 2) return lines.slice(0, 3);

  // Fallback: split into sentences
  const sentences = text
    .split(/(?<=[.!?])\s+/)
    .map((s) => s.trim())
    .filter((s) => s.length > 20 && s.length < 160);

  return sentences.slice(0, 3);
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

function scorePercent(score) {
  if (score == null || !Number.isFinite(Number(score))) return null;
  const value = Number(score);
  return Math.round(value * (value <= 1 ? 100 : 1));
}

function MatchScoreOdometer({ from, to }) {
  const start = scorePercent(from);
  const finish = scorePercent(to);
  const [display, setDisplay] = React.useState(start ?? finish ?? 0);

  React.useEffect(() => {
    if (finish == null) return undefined;
    const initial = start ?? finish;
    let frame;
    const startedAt = performance.now();
    const tick = (now) => {
      const progress = Math.min((now - startedAt) / 950, 1);
      setDisplay(Math.round(initial + (finish - initial) * progress));
      if (progress < 1) frame = requestAnimationFrame(tick);
    };
    setDisplay(initial);
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [start, finish]);

  return <span aria-live="polite" data-testid="match-score-odometer" className="tabular-nums text-[36px] font-semibold tracking-tight text-[#2E7538]">{finish == null ? "—" : `${display}%`}</span>;
}

export function NotInterestedReasonModal({ open, job, busy = false, onClose, onConfirm }) {
  const [selectedReason, setSelectedReason] = React.useState(NOT_INTERESTED_REASONS[0]);

  React.useEffect(() => {
    if (open) {
      setSelectedReason(NOT_INTERESTED_REASONS[0]);
    }
  }, [open, job?.id]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 bg-black/35 flex items-center justify-center px-4">
      <div className="w-full max-w-md rounded-2xl bg-[#FBFBF9] border border-black/[0.06] shadow-2xl overflow-hidden">
        <div className="flex items-start justify-between gap-3 px-5 pt-5 pb-4 border-b border-black/[0.05]">
          <div className="min-w-0">
            <h3 className="text-[15px] font-medium text-[#1F1F1F]">Why are you passing on this role?</h3>
            <p className="text-[12px] text-[#9A9A98] mt-1 truncate">
              {job?.title || "Role"}{job?.company ? ` · ${job.company}` : ""}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-lg text-[#4A4A48] hover:bg-black/[0.04] transition-colors"
            aria-label="Close"
          >
            <X className="w-4 h-4" strokeWidth={1.75} />
          </button>
        </div>

        <div className="px-5 py-4 max-h-[52vh] overflow-y-auto eve-scroll">
          <div className="grid gap-2">
            {NOT_INTERESTED_REASONS.map((reason) => {
              const checked = selectedReason === reason;
              return (
                <button
                  key={reason}
                  type="button"
                  onClick={() => setSelectedReason(reason)}
                  className={`flex items-center justify-between gap-3 rounded-xl border px-4 py-3 text-left transition-colors ${
                    checked
                      ? "border-[#1F1F1F] bg-black/[0.03]"
                      : "border-black/[0.06] bg-white hover:bg-black/[0.02]"
                  }`}
                >
                  <span className="text-[13px] text-[#1F1F1F] font-normal leading-tight">{reason}</span>
                  <span
                    className={`flex h-4 w-4 shrink-0 items-center justify-center rounded-full border ${
                      checked ? "border-[#1F1F1F] bg-[#1F1F1F] text-white" : "border-black/[0.14] bg-white"
                    }`}
                    aria-hidden="true"
                  >
                    {checked && <Check className="h-2.5 w-2.5" strokeWidth={3} />}
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        <div className="px-5 py-4 border-t border-black/[0.05] flex gap-3">
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="flex-1 py-2.5 rounded-xl bg-black/[0.04] text-[#4A4A48] text-[13px] font-normal hover:bg-black/[0.08] transition-colors disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => onConfirm?.(selectedReason)}
            disabled={busy}
            className="flex-1 py-2.5 rounded-xl bg-[#1F1F1F] text-white text-[13px] font-medium hover:bg-black transition-colors disabled:opacity-50"
          >
            OK
          </button>
        </div>
      </div>
    </div>
  );
}

function gaugePoint(value, radius = 84) {
  const angle = ((180 + value * 1.8) * Math.PI) / 180;
  return { x: 100 + radius * Math.cos(angle), y: 100 + radius * Math.sin(angle) };
}

function GaugeArc({ from, to, color }) {
  const start = gaugePoint(from);
  const end = gaugePoint(to);
  return <path d={`M ${start.x} ${start.y} A 84 84 0 0 1 ${end.x} ${end.y}`} fill="none" stroke={color} strokeWidth="13" />;
}

function MatchScoreGauge({ score }) {
  const rawScore = scorePercent(score);
  const value = rawScore == null ? null : Math.max(0, Math.min(100, rawScore));
  const needle = value == null ? null : gaugePoint(value, 66);
  return <section className="rounded-2xl border border-black/[.06] bg-white px-3 pb-2 pt-4 text-center" aria-label={value == null ? "Match score unavailable" : `Match score ${value} out of 100`} data-testid="match-score-gauge">
    <p className="text-[11px] font-medium uppercase tracking-[0.12em] text-[#7B927B]">Match score</p>
    <div className="relative mx-auto mt-1 w-full max-w-[300px]">
      <svg viewBox="0 0 200 116" className="block h-auto w-full" role="img" aria-hidden="true">
        <GaugeArc from={0} to={40} color="#D9826B" /><GaugeArc from={41} to={69} color="#D8A84C" /><GaugeArc from={70} to={100} color="#6E9B72" />
        {[0, 25, 50, 75, 100].map((tick) => { const outer = gaugePoint(tick, 94); const inner = gaugePoint(tick, 80); return <line key={tick} x1={outer.x} y1={outer.y} x2={inner.x} y2={inner.y} stroke="#FBFBF9" strokeWidth="2" />; })}
        {needle && <><line x1="100" y1="100" x2={needle.x} y2={needle.y} stroke="#1F1F1F" strokeWidth="3" strokeLinecap="round" /><circle cx="100" cy="100" r="5" fill="#1F1F1F" /></>}
      </svg>
      <div className="pointer-events-none absolute inset-x-0 bottom-0 flex justify-between px-[4%] text-[10px] font-medium text-[#7A7A78]"><span>0</span><span>25</span><span>50</span><span>75</span><span>100</span></div>
      <p className="pointer-events-none absolute inset-x-0 bottom-4 tabular-nums text-[32px] font-semibold tracking-tight text-[#1F1F1F]" data-testid="match-score-gauge-value">{value == null ? "—" : `${value}%`}</p>
    </div>
    <div className="mx-auto mt-1 flex max-w-[300px] justify-between text-[10px] text-[#7A7A78]"><span>Needs work</span><span>Developing</span><span>Strong match</span></div>
  </section>;
}

function ImproveMatchModal({ job, data, onClose, onApplyCurrent, onFixResume }) {
  if (!job) return null;
  const score = data?.match_score ?? job.match_score;
  return <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/35 px-4" data-testid="improve-match-modal">
    <div className="w-full max-w-md overflow-hidden rounded-2xl bg-[#FBFBF9] shadow-2xl">
      <div className="flex items-start justify-between gap-3 border-b border-black/[.06] px-5 py-4"><h3 className="text-[16px] font-medium">Improve Your Match</h3><button aria-label="Close improve match" onClick={onClose}><X className="h-4 w-4" /></button></div>
      <div className="max-h-[55vh] space-y-4 overflow-y-auto px-5 py-4">
        <MatchScoreGauge score={score} />
        <div><p className="text-[12px] font-medium">Missing skills</p>{data ? (data.missing_skills?.length ? <div className="mt-2 flex flex-wrap gap-1.5">{data.missing_skills.map((skill) => <SkillPill key={skill} label={skill} />)}</div> : <p className="mt-1 text-[12px] text-[#4A4A48]">No specific skill gap was identified.</p>) : <p className="mt-1 text-[12px] text-[#9A9A98]">Checking your profile…</p>}</div>
      </div>
      <div className="flex gap-3 border-t border-black/[.06] px-5 py-4"><button onClick={onApplyCurrent} className="flex-1 rounded-xl bg-black/[.05] py-2.5 text-[13px]">Apply with Current Resume</button><button data-testid="fix-my-resume" onClick={onFixResume} className="flex-1 rounded-xl bg-[#1F1F1F] py-2.5 text-[13px] text-white">Fix My Resume</button></div>
    </div>
  </div>;
}

/* The full resume editor lives on /resume-editor so it can be opened in its own tab. */
function LegacyResumeImprovementEditor({ job, data, busy, onClose, onSave }) {
  const [resume, setResume] = React.useState({});
  const [error, setError] = React.useState("");
  React.useEffect(() => { if (data?.resume) setResume(data.resume); }, [data]);
  if (!job) return null;
  const set = (key, value) => setResume((old) => ({ ...old, [key]: value }));
  const listText = (key) => Array.isArray(resume[key]) ? resume[key].join(", ") : "";
  const save = () => {
    try {
      onSave({ ...resume,
        skills: listText("skills").split(",").map((x) => x.trim()).filter(Boolean),
        certifications: listText("certifications").split(",").map((x) => x.trim()).filter(Boolean),
        work_experience: JSON.parse(resume.work_experience_text ?? JSON.stringify(resume.work_experience || [])),
        education: JSON.parse(resume.education_text ?? JSON.stringify(resume.education || [])), projects: JSON.parse(resume.projects_text ?? JSON.stringify(resume.projects || [])),
      });
    } catch { setError("Experience, education, and projects must contain valid JSON."); }
  };
  const jsonText = (key) => resume[`${key}_text`] ?? JSON.stringify(resume[key] || [], null, 2);
  return <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/35 px-4" data-testid="resume-improvement-editor"><div className="w-full max-w-2xl rounded-2xl bg-[#FBFBF9] shadow-2xl"><div className="flex items-start justify-between gap-3 border-b border-black/[.06] px-5 py-4"><div><h3 className="text-[16px] font-medium">Edit Your Resume</h3><p className="mt-1 text-[13px] text-[#4A4A48]">Editing the resume you uploaded for {job.title}.</p></div><button aria-label="Close resume editor" onClick={onClose}><X className="h-4 w-4" /></button></div><div className="max-h-[60vh] space-y-4 overflow-y-auto px-5 py-4">{data?.missing_skills?.length > 0 && <div><p className="text-[12px] font-medium">Job gaps to consider</p><div className="mt-2 flex flex-wrap gap-1.5">{data.missing_skills.map((skill) => <SkillPill key={skill} label={skill} />)}</div></div>}<label className="block text-[12px] font-medium">Name<input value={resume.name || ""} onChange={(e) => set("name", e.target.value)} className="mt-1 w-full rounded-lg border p-2 text-[13px]" /></label><label className="block text-[12px] font-medium">Professional summary<textarea value={resume.bio || ""} onChange={(e) => set("bio", e.target.value)} className="mt-1 w-full rounded-lg border p-2 text-[13px]" rows="3" /></label><label className="block text-[12px] font-medium">Skills (comma-separated)<input data-testid="resume-skills" value={listText("skills")} onChange={(e) => set("skills", e.target.value.split(",").map((x) => x.trim()).filter(Boolean))} className="mt-1 w-full rounded-lg border p-2 text-[13px]" /></label>{[["work_experience", "Work experience"], ["education", "Education"], ["projects", "Projects"]].map(([key, label]) => <label key={key} className="block text-[12px] font-medium">{label}<textarea value={jsonText(key)} onChange={(e) => set(`${key}_text`, e.target.value)} className="mt-1 w-full rounded-lg border p-2 font-mono text-[12px]" rows="5" /></label>)}<label className="block text-[12px] font-medium">Certifications (comma-separated)<input value={listText("certifications")} onChange={(e) => set("certifications", e.target.value.split(",").map((x) => x.trim()).filter(Boolean))} className="mt-1 w-full rounded-lg border p-2 text-[13px]" /></label>{error && <p className="text-[12px] text-red-600">{error}</p>}</div><div className="flex gap-3 border-t border-black/[.06] px-5 py-4"><button onClick={onClose} className="flex-1 rounded-xl bg-black/[.05] py-2.5 text-[13px]">Cancel</button><button data-testid="save-resume-improvements" disabled={busy} onClick={save} className="flex-1 rounded-xl bg-[#1F1F1F] py-2.5 text-[13px] text-white disabled:opacity-50">{busy ? "Saving…" : "Save Changes"}</button></div></div></div>;
}

function UpdatedResumeModal({ job, result, onDownload, onApply, onClose }) {
  const previous = scorePercent(result.previous_match_score);
  const current = scorePercent(result.match_score);
  const improved = previous != null && current != null && current > previous;
  return <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/35 px-4" data-testid="updated-resume-modal"><div className="w-full max-w-md rounded-2xl bg-[#FBFBF9] p-5 shadow-2xl"><h3 className="text-[16px] font-medium">Your resume is updated</h3><div className="mt-4 rounded-xl bg-[#E7F2E4] px-4 py-3 text-center"><p className="text-[11px] font-medium uppercase tracking-wide text-[#4A4A48]">Match recalculated</p><MatchScoreOdometer from={result.previous_match_score} to={result.match_score} /><p className="text-[12px] text-[#4A4A48]">Previous match: {previous == null ? "—" : `${previous}%`}</p></div><p className="mt-3 text-[13px] text-[#4A4A48]">{improved ? "Your confirmed details improved this job's match." : "The score did not improve. More relevant, confirmed information may be needed."}</p>{result.changed_skills?.length > 0 && <div className="mt-4"><p className="text-[12px] font-medium">What changed</p><div className="mt-2 flex flex-wrap gap-1.5">{result.changed_skills.map((skill) => <SkillPill key={skill} label={skill} />)}</div></div>}{result.remaining_missing_skills?.length > 0 && <div className="mt-4"><p className="text-[12px] font-medium">Still missing for this role</p><div className="mt-2 flex flex-wrap gap-1.5">{result.remaining_missing_skills.map((skill) => <SkillPill key={skill} label={skill} />)}</div></div>}{result.remaining_requirements?.length > 0 && <div className="mt-4"><p className="text-[12px] font-medium">Remaining job requirements</p><ul className="mt-2 space-y-1 text-[12px] text-[#4A4A48]">{result.remaining_requirements.slice(0, 4).map((requirement, index) => <li key={index}>• {requirement}</li>)}</ul></div>}<div className="mt-5 flex gap-3"><button data-testid="download-updated-resume" onClick={onDownload} className="flex-1 rounded-xl bg-black/[.05] py-2.5 text-[13px]">Download updated resume</button><button onClick={onApply} className="flex-1 rounded-xl bg-[#1F1F1F] py-2.5 text-[13px] text-white">Apply Now</button></div><button onClick={onClose} className="mt-3 w-full text-[12px] text-[#4A4A48]">Close</button></div></div>;
}

// ─── Job Detail Modal ────────────────────────────────────────────────────────

export function JobDetailModal({ job, onClose, onApply, onNotInterested, applying }) {
  const skills = normalizeSkills(job.skills);
  const matchPct = job.match_score != null
    ? Math.round(job.match_score * (job.match_score <= 1 ? 100 : 1))
    : null;

  const MATCH_REASON_LABELS = {
    hybrid_match: "Your skills and experience are a strong fit for this role.",
    skill_match: "Your skills closely match what this role requires.",
    experience_match: "Your experience aligns well with this position.",
    location_match: "This role matches your preferred location.",
    title_match: "Your target roles align with this position.",
  };

  const getMatchReason = () => {
    const r = job.match_reason;
    if (!r) return "";
    if (typeof r === "string") {
      return MATCH_REASON_LABELS[r] ?? r;
    }
    if (typeof r === "object") {
      const key = r.type || r.reason || "";
      return MATCH_REASON_LABELS[key] ?? r.explanation ?? r.description ?? key;
    }
    return String(r);
  };
  const matchReason = getMatchReason();
  const handleBackdropInteract = (e) => {
    if (e.target === e.currentTarget) {
      onClose?.();
    }
  };

  return (
    <div
      className="absolute inset-0 z-20 bg-[#FBFBF9] p-3 sm:p-6"
      data-testid="job-detail-backdrop"
      onMouseDown={handleBackdropInteract}
      onTouchStart={handleBackdropInteract}
    >
      <div
        className="flex h-full w-full flex-col overflow-hidden rounded-2xl border border-black/[0.05] bg-[#FBFBF9] shadow-2xl"
        data-testid="job-detail-panel"
      >
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
          {job.description && (
            <div>
              <p className="text-[12px] font-medium text-[#1F1F1F] mb-1.5">Job Description</p>
              <div
                className="job-description-html text-[13px] text-[#4A4A48] leading-relaxed font-normal"
                dangerouslySetInnerHTML={{ __html: sanitizeHtml(job.description) }}
              />
            </div>
          )}

          {/* Requirements */}
          {job.requirements && (
            <div>
              <p className="text-[12px] font-medium text-[#1F1F1F] mb-1.5">Requirements</p>
              <div
                className="job-description-html text-[13px] text-[#4A4A48] leading-relaxed font-normal"
                dangerouslySetInnerHTML={{ __html: sanitizeHtml(job.requirements) }}
              />
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
            onClick={onClose}
            className="flex-1 py-2.5 rounded-xl bg-black/[0.04] text-[#4A4A48] text-[13px] font-normal hover:bg-black/[0.08] transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={onNotInterested}
            className="py-2.5 px-3 rounded-xl bg-black/[0.04] text-[#4A4A48] text-[13px] font-normal hover:bg-black/[0.08] transition-colors"
          >
            Not Interested
          </button>
          <button
            onClick={() => onApply?.()}
            disabled={!job.job_url}
            className="flex-1 py-2.5 rounded-xl bg-[#1F1F1F] text-white text-[13px] font-medium hover:bg-black transition-colors disabled:opacity-50 flex items-center justify-center gap-1.5"
            title={job.job_url ? "Apply on the company's website" : "Application link not available"}
          >
            <ExternalLink className="w-3.5 h-3.5" strokeWidth={2} />
            Apply Now
          </button>
        </div>
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

  const skills = normalizeSkills(job.skills).slice(0, 4);
  const bullets = extractBullets(job);

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

          {bullets.length > 0 && (
            <ul className="mb-3 space-y-1.5">
              {bullets.map((b, i) => (
                <li key={i} className="flex items-start gap-2 text-[12.5px] text-[#4A4A48] leading-snug font-normal">
                  <span className="mt-[5px] w-1 h-1 rounded-full bg-[#9A9A98] shrink-0" />
                  <span className="line-clamp-2">{b}</span>
                </li>
              ))}
            </ul>
          )}

          {skills.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {skills.map((sk) => <SkillPill key={sk} label={sk} />)}
            </div>
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

function HorizontalJobCard({ job, onOpenDetails, onNotInterested, onApply, onTrack }) {
  const matchPct = job.match_score != null ? Math.round(job.match_score * (job.match_score <= 1 ? 100 : 1)) : null;
  const skills = normalizeSkills(job.skills).slice(0, 4);
  const description = cleanText(job.description || job.responsibilities || job.requirements);

  return (
    <article data-testid={`job-card-${job.id}`} role="button" tabIndex={0} onClick={onOpenDetails}
      onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onOpenDetails(); } }}
      className="w-full cursor-pointer rounded-2xl border border-black/[0.07] bg-white px-4 py-4 shadow-[0_2px_12px_rgba(0,0,0,0.04)] transition-all hover:border-black/[0.12] sm:px-5">
      <div className="flex min-w-0 items-start gap-3 sm:gap-4">
        {job.logo ? <img src={job.logo} alt="" className="h-10 w-10 shrink-0 rounded-xl object-cover" /> : <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[#E7E3F0]"><span className="text-[14px] font-medium text-[#7B6FB8]">{(job.company || "?")[0].toUpperCase()}</span></div>}
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-start justify-between gap-3"><div className="min-w-0"><h3 className="truncate text-[14px] font-semibold leading-tight text-[#1F1F1F]">{job.title}</h3><p className="mt-0.5 truncate text-[12.5px] text-[#9A9A98]">{job.company}</p></div>
          {matchPct != null && <div data-testid={`match-score-${job.id}`} className="shrink-0 text-right"><p className="text-[18px] font-semibold leading-none text-[#2E7538]">{matchPct}%</p><p className="mt-1 text-[10px] font-medium uppercase tracking-wide text-[#7B927B]">Match</p></div>}</div>
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-[#4A4A48]">{job.location && <span className="flex items-center gap-1"><MapPin className="h-3.5 w-3.5 text-[#9A9A98]" strokeWidth={1.5} />{job.location}</span>}{job.salary && <span className="font-medium text-[#1F1F1F]">{job.salary}</span>}</div>
          {description && <p className="mt-2 line-clamp-2 text-[12px] leading-relaxed text-[#4A4A48]">{description}</p>}
          {skills.length > 0 && <div className="mt-2 flex flex-wrap gap-1.5">{skills.map((skill) => <SkillPill key={skill} label={skill} />)}</div>}
          <div className="mt-3 flex flex-wrap gap-2" onClick={(event) => event.stopPropagation()}>
            <button type="button" data-testid={`not-interested-${job.id}`} onClick={onNotInterested} className="rounded-xl border border-black/[0.08] bg-white px-3 py-2 text-[12px] text-[#4A4A48] hover:bg-black/[0.03]">Not Interested</button>
            <button type="button" data-testid={`apply-${job.id}`} onClick={onApply} disabled={!job.job_url} className="rounded-xl bg-[#1F1F1F] px-3 py-2 text-[12px] font-medium text-white hover:bg-black disabled:opacity-50">Apply Now</button>
            <button type="button" data-testid={`track-${job.id}`} onClick={onTrack} className="rounded-xl bg-black/[0.04] px-3 py-2 text-[12px] font-medium text-[#1F1F1F] hover:bg-black/[0.08]">Track</button>
          </div>
        </div>
      </div>
    </article>
  );
}

export default function SwipeJobDeck({ jobs, candidateId, onJobsChange, onDismissJob, onExhausted }) {
  const [index, setIndex] = React.useState(0);
  const [detailJob, setDetailJob] = React.useState(null);
  const [pendingDismissJob, setPendingDismissJob] = React.useState(null);
  const [applying, setApplying] = React.useState(false);
  const [dismissing, setDismissing] = React.useState(false);
  const [improvementJob, setImprovementJob] = React.useState(null);
  const [improvementData, setImprovementData] = React.useState(null);
  // actioned: ids removed from deck this session (dismissed or tracked)
  const [actioned, setActioned] = React.useState(new Set());
  const exhaustionRequestedRef = React.useRef(false);

  // Only show jobs that haven't been actioned this session AND aren't already tracked
  const pending = jobs.filter((j) => !actioned.has(j.id) && !j.tracked);
  const current = pending[index] ?? null;
  const total = pending.length;

  React.useEffect(() => {
    if (total > 0) exhaustionRequestedRef.current = false;
  }, [total]);

  const advance = React.useCallback(() => setIndex((i) => i + 1), []);

  // LEFT SWIPE → dismiss immediately
  const handleSwipeLeft = React.useCallback(async () => {
    if (!current) return;
    const id = current.id;
    setActioned((s) => new Set(s).add(id));
    advance();
    try {
      await onDismissJob?.(id);
      onJobsChange?.();
    } catch {
      // silent — local state already updated
    }
  }, [current, onJobsChange, advance, onDismissJob]);

  // RIGHT SWIPE → track (persist to backend before removing card)
  const handleSwipeRight = React.useCallback(async (job = current) => {
    if (!job) return;
    const id = job.id;
    try {
      await axios.post(`${API}/candidate/${candidateId}/jobs/${id}/track`);
      setActioned((s) => new Set(s).add(id));
      onJobsChange?.();
    } catch {
      toast.error("Couldn't save this job. Please try again.");
      // Card stays — do NOT advance
    }
  }, [current, candidateId, onJobsChange]);

  // APPLY → open the company's careers page directly in a new tab
  const openJobUrl = React.useCallback((job) => {
    if (!job?.job_url) { toast.error("Application link is not available for this job."); return; }
    window.open(job.job_url, "_blank", "noopener,noreferrer");
  }, []);
  const handleApply = React.useCallback(async (job = detailJob) => {
    if (!job || applying) return;
    const score = Number(job.match_score);
    if (job.job_url && Number.isFinite(score) && score * (score <= 1 ? 100 : 1) < 90) {
      setImprovementJob(job); setImprovementData(null);
      try { const response = await axios.get(`${API}/candidate/${candidateId}/jobs/${job.id}/match-improvement`); setImprovementData(response.data); }
      catch { toast.error("Couldn't load match details. Please try again."); }
      return;
    }
    openJobUrl(job);
  }, [detailJob, applying, candidateId, openJobUrl]);
  const openResumeEditor = React.useCallback(() => {
    if (!improvementJob) return;
    const params = new URLSearchParams({
      candidate_id: candidateId,
      recommendation_id: improvementJob.id,
      previous_match_score: String(improvementData?.match_score ?? improvementJob.match_score ?? ""),
    });
    if (improvementJob.job_id) params.set("job_id", improvementJob.job_id);
    window.open(`/resume-editor?${params.toString()}`, "_blank", "noopener,noreferrer");
    setImprovementJob(null);
  }, [candidateId, improvementData?.match_score, improvementJob]);

  const handleRequestDismiss = React.useCallback((job) => {
    if (!job) return;
    setDetailJob(null);
    setPendingDismissJob(job);
  }, []);

  const handleConfirmDismiss = React.useCallback(async (reason) => {
    if (!pendingDismissJob || dismissing) return;
    const id = pendingDismissJob.id;
    setDismissing(true);
    try {
      await onDismissJob?.(id, reason);
      setActioned((s) => new Set(s).add(id));
      setPendingDismissJob(null);
      onJobsChange?.();
    } catch {
      toast.error("Couldn't save this choice. Please try again.");
    } finally {
      setDismissing(false);
    }
  }, [pendingDismissJob, dismissing, onDismissJob, onJobsChange]);

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

  if (total === 0) {
    if (!exhaustionRequestedRef.current && jobs.length > 0) {
      exhaustionRequestedRef.current = true;
      Promise.resolve().then(() => onExhausted?.());
    }
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-2 px-8 text-center">
        <p className="text-[15px] font-medium text-[#1F1F1F]">You're all caught up.</p>
        <p className="text-[13px] text-[#9A9A98] font-normal">No more recommended jobs right now.</p>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col min-h-0 relative">
      <NotInterestedReasonModal
        open={Boolean(pendingDismissJob)}
        job={pendingDismissJob}
        busy={dismissing}
        onClose={() => setPendingDismissJob(null)}
        onConfirm={handleConfirmDismiss}
      />
      <ImproveMatchModal job={improvementJob} data={improvementData} onClose={() => setImprovementJob(null)} onApplyCurrent={() => { openJobUrl(improvementJob); setImprovementJob(null); }} onFixResume={openResumeEditor} />
      {/* Detail modal overlay */}
      {detailJob && (
        <JobDetailModal
          job={detailJob}
          onClose={() => setDetailJob(null)}
          onApply={handleApply}
          onNotInterested={() => handleRequestDismiss(detailJob)}
        />
      )}

      {/* Header */}
      <div className="shrink-0 px-6 pt-5 pb-3 flex items-center justify-between">
        <h2 className="text-[14px] font-medium text-[#1F1F1F]">Recommended for you</h2>
        <span className="text-[12px] text-[#9A9A98] font-normal">{total} {total === 1 ? "role" : "roles"}</span>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto eve-scroll px-4 pb-5 sm:px-6">
        <div className="space-y-3">
          {pending.map((job) => (
            <HorizontalJobCard key={job.id} job={job} onOpenDetails={() => setDetailJob(job)} onNotInterested={() => handleRequestDismiss(job)}
              onApply={() => handleApply(job)}
              onTrack={() => handleSwipeRight(job)} />
          ))}
        </div>
      </div>
    </div>
  );
}
