import React from "react";
import axios from "axios";
import DOMPurify from "dompurify";
import { Info, MapPin, Bookmark, BookmarkCheck, Bell, Download, Camera, Trash2, UserCircle2, LockKeyhole } from "lucide-react";
import { ImproveMatchModal, JobDetailModal, NotInterestedReasonModal } from "./SwipeJobCard";
import { formatExperienceDuration, normalizeProfileForDisplay } from "../lib/profileNormalization";
import { buildProfileBio } from "../lib/profileBio";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

function sanitizeHtml(str) {
  if (!str || typeof str !== "string") return "";
  return DOMPurify.sanitize(str, { USE_PROFILES: { html: true } });
}

function stripHtml(str) {
  if (!str || typeof str !== "string") return "";
  const clean = DOMPurify.sanitize(str, { ALLOWED_TAGS: [], ALLOWED_ATTR: [] });
  return clean.replace(/\s+/g, " ").trim();
}

function ProfileStrengthBar({ label, percent }) {
  const previousPercent = React.useRef(percent);
  const [isImproving, setIsImproving] = React.useState(false);

  React.useEffect(() => {
    const hasImproved = percent > previousPercent.current;
    previousPercent.current = percent;

    if (!hasImproved) return undefined;

    setIsImproving(true);
    const timeoutId = window.setTimeout(() => setIsImproving(false), 1400);
    return () => window.clearTimeout(timeoutId);
  }, [percent]);

  return (
    <div
      data-testid="profile-strength-bar"
      className={`profile-strength-meter flex items-center gap-2.5 ${isImproving ? "profile-strength-meter--improving" : ""}`}
    >
      <span aria-live="polite" className="profile-strength-meter__label text-[11.5px] text-[#62578F] font-medium">
        Profile Meter:{" "}
        <span className="text-[#1F1F1F] font-medium">{label} {percent}%</span>
      </span>
      <div
        role="progressbar"
        aria-label="Profile Meter"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        className="profile-strength-meter__track w-[90px] h-1.5 rounded-full bg-[#DED8F0] overflow-hidden"
      >
        <div
          className="profile-strength-meter__fill h-full bg-[#6D60AB] rounded-full transition-all duration-700 ease-out"
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  );
}

function OpenToMatchesBadge({ isOpen, onToggle }) {
  return (
    <div className="inline-flex items-center gap-2">
      <span className="text-[12px] font-medium text-[#4A4A48]">Open to opportunities</span>
      <button
        role="switch"
        aria-checked={isOpen}
        aria-label="Open to opportunities"
        data-testid="open-to-matches-badge"
        onClick={onToggle}
        className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#2E7538] ${
          isOpen ? "bg-[#2E7538]" : "bg-[#C7C7C5]"
        }`}
      >
        <span
          className={`pointer-events-none inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform ${
            isOpen ? "translate-x-4" : "translate-x-0"
          }`}
        />
      </button>
      <span className={`text-[11px] font-medium ${isOpen ? "text-[#2E7538]" : "text-[#9A9A98]"}`}>
        {isOpen ? "On" : "Off"}
      </span>
    </div>
  );
}

function SectionLabel({ children }) {
  return (
    <div className="flex items-center gap-3 mb-4">
      <h3 className="shrink-0 text-[11px] font-semibold uppercase tracking-[0.14em] text-[#4A4A48]">{children}</h3>
      <div className="h-px flex-1 bg-black/[0.08]" />
    </div>
  );
}

export function generateBio(profile) {
  return buildProfileBio(profile);
}

/* --- Hover-only rows (no card background by default) --- */

function ExperienceRow({ exp }) {
  return (
    <div
      data-testid={`experience-row-${exp.id}`}
      className="eve-hover-row px-3 py-3 -mx-3"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h4 className="text-[13.5px] font-medium text-[#1F1F1F]">
            {exp.title}
          </h4>
          <p className="text-[12px] text-[#9A9A98] mt-0.5 font-normal">
            {exp.company} · {exp.dates}
          </p>
        </div>
      </div>
      <p className="text-[12.5px] text-[#4A4A48] mt-2 leading-relaxed font-normal">
        {exp.description}
      </p>
    </div>
  );
}

function EducationRow({ edu }) {
  return (
    <div
      data-testid={`education-row-${edu.id}`}
      className="eve-hover-row px-3 py-3 -mx-3"
    >
      <h4 className="text-[13px] font-medium text-[#1F1F1F]">{edu.degree}</h4>
      <p className="text-[12px] text-[#9A9A98] mt-0.5 font-normal">
        {edu.institution} · {edu.dates}
      </p>
    </div>
  );
}

function groupExperienceByEmployment(experience) {
  const groups = new Map();

  (experience || []).forEach((exp, index) => {
    const title = exp?.title || exp?.role || "Role not provided";
    const company = exp?.company || exp?.company_name || "";
    // An employment entry can arrive as one record per project.  Group those
    // records by employer so the employment context is rendered once, rather
    // than once for every project title.
    const key = company.trim().toLowerCase() || `unassigned-employment-${index}`;
    const projects = Array.isArray(exp?.projects) && exp.projects.length
      ? exp.projects.map((project, projectIndex) => ({
          id: project?.id || `${exp?.id || index}-project-${projectIndex}`,
          title: project?.title || project?.name || title,
          description: project?.description || project?.summary || "",
        }))
      : [{
          id: exp?.id || `project-${index}`,
          title: exp?.project_title || exp?.projectTitle || exp?.project_name || exp?.projectName || title,
          description: exp?.project_description || exp?.projectDescription || exp?.description || exp?.summary || "",
        }];

    if (!groups.has(key)) groups.set(key, { ...exp, title, company, projects });
    else groups.get(key).projects.push(...projects);
  });

  return [...groups.values()];
}

function formatEducationDates(education) {
  const year = (value) => String(value ?? "").match(/(?:18|19|20|21)\d{2}/)?.[0];
  const startYear = year(education?.start_date ?? education?.startDate);
  const endYear = year(education?.end_date ?? education?.endDate);

  if (startYear && endYear) return `${startYear} – ${endYear}`;
  return education?.dates || "";
}

function ResumeExperienceEntry({ exp }) {
  const highlights = String(exp.description || exp.summary || "")
    .split(/\n+|(?<=\.)\s+(?=[A-Z])/)
    .map((item) => item.replace(/^[-•]\s*/, "").trim())
    .filter(Boolean);

  return (
    <div data-testid={`experience-row-${exp.id}`} className="relative border-l border-[#D8D5E3] pl-5 pb-7 last:pb-0">
      <span className="absolute -left-[4.5px] top-1.5 h-2 w-2 rounded-full border-2 border-[#7B6FB8] bg-[#FDFDFC]" />
      <div className="flex flex-col gap-1 sm:flex-row sm:items-baseline sm:justify-between sm:gap-4">
        <h4 className="text-[14px] font-semibold leading-snug text-[#1F1F1F]">{exp.title || "Role not provided"}</h4>
        {exp.dates && <p className="shrink-0 text-[11.5px] font-medium text-[#777774]">{exp.dates}</p>}
      </div>
      {exp.company && <p className="mt-0.5 text-[12.5px] font-medium text-[#6A5E9D]">{exp.company}</p>}
      {exp.projects?.length > 0 && (
        <div className="mt-3 space-y-3">
          {exp.projects.map((project, projectIndex) => (
            <div key={project.id} data-testid={`experience-project-${project.id}`}>
              {!(projectIndex === 0 && project.title === exp.title) && (
                <h5 className="text-[12.5px] font-semibold text-[#1F1F1F]">{project.title}</h5>
              )}
              {project.description && <p className="mt-1 text-[12.5px] leading-relaxed text-[#4A4A48]">{project.description}</p>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ResumeEducationEntry({ edu }) {
  const dates = formatEducationDates(edu);

  return (
    <div data-testid={`education-row-${edu.id}`} className="border-b border-black/[0.06] py-3 first:pt-0 last:border-b-0 last:pb-0">
      <div className="flex flex-col gap-1 sm:flex-row sm:items-baseline sm:justify-between sm:gap-4">
        <h4 className="text-[13px] font-semibold text-[#1F1F1F]">{edu.degree || "Education not provided"}</h4>
        {dates && <p className="shrink-0 text-[11.5px] text-[#777774]">{dates}</p>}
      </div>
      {edu.institution && <p className="mt-0.5 text-[12px] text-[#4A4A48]">{edu.institution}</p>}
    </div>
  );
}

const ALLOWED_PHOTO_TYPES = ["image/jpeg", "image/png", "image/webp"];
const MAX_PHOTO_BYTES = 5 * 1024 * 1024; // 5 MB
const DEFAULT_VISIBLE_EXPERIENCES = 3;

function resolvePhotoSrc(photoUrl) {
  if (!photoUrl) return null;
  if (/^https?:\/\//i.test(photoUrl)) return photoUrl;
  if (!BACKEND_URL) return photoUrl;

  try {
    return new URL(photoUrl, BACKEND_URL).toString();
  } catch {
    return photoUrl;
  }
}

export function ProfilePhotoUpload({ user, candidateId, onPhotoChange }) {
  const inputRef = React.useRef(null);
  const [uploading, setUploading] = React.useState(false);
  const [photoUrl, setPhotoUrl] = React.useState(user.avatar || null);
  const [deleting, setDeleting] = React.useState(false);
  const resolvedPhotoSrc = React.useMemo(() => resolvePhotoSrc(photoUrl), [photoUrl]);
  const resolvedCandidateId = candidateId ?? user.candidate_id ?? user.candidateId ?? user.id ?? null;

  React.useEffect(() => { setPhotoUrl(user.avatar || null); }, [user.avatar]);

  React.useEffect(() => {
    if (!resolvedPhotoSrc) return;
    console.debug("[ProfilePhotoUpload] resolved image src:", resolvedPhotoSrc);
  }, [resolvedPhotoSrc]);

  const clearInput = () => {
    if (inputRef.current) inputRef.current.value = "";
  };

  const handleFile = async (file) => {
    if (!file || !resolvedCandidateId) return;
    if (!ALLOWED_PHOTO_TYPES.includes(file.type)) {
      alert("Please upload a JPG, JPEG, PNG, or WebP image.");
      return;
    }
    if (file.size > MAX_PHOTO_BYTES) {
      alert("Image must be smaller than 5 MB.");
      return;
    }
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await axios.post(`${API}/candidate/${resolvedCandidateId}/photo`, fd);
      const url = res.data?.photo_url;
      setPhotoUrl(url);
      onPhotoChange?.(url);
    } catch {
      alert("Photo upload failed. Please try again.");
    } finally {
      setUploading(false);
      clearInput();
    }
  };

  const handleDelete = async () => {
    if (!resolvedCandidateId || !photoUrl || deleting || uploading) return;
    const confirmDelete = window.confirm("Delete your profile photo?");
    if (!confirmDelete) return;
    setDeleting(true);
    try {
      await axios.delete(`${API}/candidate/${resolvedCandidateId}/photo`);
      setPhotoUrl(null);
      onPhotoChange?.(null);
    } catch {
      alert("Photo delete failed. Please try again.");
    } finally {
      setDeleting(false);
      clearInput();
    }
  };

  return (
    <div className="relative w-14 h-14 shrink-0 group" data-testid="candidate-photo">
      {photoUrl ? (
        <img
          src={resolvedPhotoSrc}
          alt={user.name || "Candidate profile photo"}
          className="w-14 h-14 rounded-full object-cover"
        />
      ) : (
        <div
          data-testid="candidate-photo-placeholder"
          className="w-14 h-14 rounded-full bg-[#E7E3F0] flex items-center justify-center border border-black/[0.04]"
        >
          <UserCircle2 className="w-7 h-7 text-[#7B6FB8]" strokeWidth={1.5} />
        </div>
      )}
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        disabled={uploading || deleting}
        aria-label="Upload profile photo"
        className="absolute inset-0 rounded-full flex items-center justify-center bg-black/0 group-hover:bg-black/30 transition-colors disabled:opacity-50"
      >
        <Camera className="w-5 h-5 text-white opacity-0 group-hover:opacity-100 transition-opacity" strokeWidth={1.75} />
      </button>
      {photoUrl && (
        <button 
          type="button"
          onClick={handleDelete}
          disabled={uploading || deleting}
          aria-label="Delete profile photo"
          className="absolute -bottom-1 -right-1 inline-flex h-6 w-6 items-center justify-center rounded-full border border-black/[0.08] bg-white text-[#4A4A48] shadow-sm opacity-0 transition-opacity group-hover:opacity-100 hover:bg-[#F7F7F5] disabled:opacity-40"
        >
          <Trash2 className="h-3.5 w-3.5" strokeWidth={1.75} />
        </button>
      )}
      <input
        ref={inputRef}
        type="file"
        accept="image/jpeg,image/png,image/webp"
        
        className="hidden"
        onChange={(e) => e.target.files?.[0] && handleFile(e.target.files[0])}
      />
    </div>
  );
}

export function ProfileTab({ user, candidateId, onToggleOpenToMatches, onPhotoChange }) {
  const profile = normalizeProfileForDisplay(user);
  // The active dashboard candidate is authoritative. Profile data can be a
  // stale snapshot while the initial profile request is resolving.
  const profileCandidateId = candidateId ?? profile.candidate_id ?? profile.candidateId ?? profile.id ?? null;
  const [showAllExperiences, setShowAllExperiences] = React.useState(false);
  const groupedExperience = React.useMemo(
    () => groupExperienceByEmployment(profile.experience),
    [profile.experience]
  );
  const experienceCount = groupedExperience.length;
  const visibleExperiences = showAllExperiences
    ? groupedExperience
    : groupedExperience.slice(0, DEFAULT_VISIBLE_EXPERIENCES);
  const hobbies = [profile.hobbies, profile.interests, profile.raw_data?.hobbies, profile.raw_data?.interests]
    .find((value) => Array.isArray(value) && value.length > 0) || [];

  return (
    <div className="space-y-8" data-testid="living-profile-content">
      {/* Header */}
      <div
        data-testid="profile-header-card"
        className={`relative rounded-2xl p-6 border border-black/[0.05] shadow-[0_1px_0_rgba(0,0,0,0.02)] overflow-hidden transition-opacity ${
          profile.isOpenToMatches ? "" : "opacity-60"
        }`}
        style={{
          background: profile.isOpenToMatches
            ? "linear-gradient(180deg, #EFEFED 0%, #F4F4F2 45%, #FAFAF8 100%)"
            : "linear-gradient(180deg, #E8E8E8 0%, #EFEFEF 45%, #F5F5F5 100%)",
        }}
      >
        <div className="flex items-start justify-between gap-5">
          <div className="flex-1 min-w-0">
            <h2 className="text-[19px] font-semibold text-[#1F1F1F] leading-tight tracking-tight">
              {profile.name || "Your Profile"}
            </h2>
            {profile.headline && (
              <p className="text-[13px] text-[#4A4A48] mt-2 leading-relaxed font-normal">
                {profile.headline}
              </p>
            )}
            <div className="mt-5 flex items-center gap-3 flex-wrap">
              {profile.location && (
                <span className="inline-flex items-center gap-1.5 text-[12px] text-[#4A4A48] font-normal">
                  <MapPin className="w-3.5 h-3.5 text-[#9A9A98]" strokeWidth={1.75} />
                  {profile.location}
                </span>
              )}
              {experienceCount > 0 && formatExperienceDuration(profile.experience_years) && (
                <span className="text-[12px] text-[#4A4A48] font-normal">
                  {formatExperienceDuration(profile.experience_years)} exp
                </span>
              )}
              <OpenToMatchesBadge isOpen={profile.isOpenToMatches} onToggle={onToggleOpenToMatches} />
            </div>
            {profile.availability && (
              <p className="mt-2 text-[12px] text-[#2E7538] font-normal">
                Available: {profile.availability}
              </p>
            )}
          </div>
          <ProfilePhotoUpload user={profile} candidateId={profileCandidateId} onPhotoChange={onPhotoChange} />
        </div>
      </div>

      {/* Resume content */}
      <section>
        <SectionLabel>Summary</SectionLabel>
        {(() => {
          // Bio is derived from the latest saved profile so chat and voice
          // updates cannot leave a stale, separately stored bio behind.
          const bioText = generateBio(profile);
          return bioText ? (
            <p className="text-[13.5px] text-[#1F1F1F] leading-[1.7] font-normal">{bioText}</p>
          ) : (
            <p className="text-[13px] text-[#9A9A98] font-normal">Not provided yet</p>
          );
        })()}
      </section>

      <section>
        <div className="flex items-start justify-between gap-4">
          <SectionLabel>
            Work Experience
          </SectionLabel>
          {experienceCount > DEFAULT_VISIBLE_EXPERIENCES && (
            <button
              type="button"
              onClick={() => setShowAllExperiences((current) => !current)}
              aria-expanded={showAllExperiences}
              data-testid="experience-toggle"
              className="text-[11.5px] text-[#4A4A48] hover:text-[#1F1F1F] underline underline-offset-2 font-normal"
            >
              {showAllExperiences ? "Show less" : "See all experiences"}
            </button>
          )}
        </div>
        {experienceCount > 0 ? (
          <div>
            {visibleExperiences.map((exp) => (
              <ResumeExperienceEntry key={exp.id} exp={exp} />
            ))}
          </div>
        ) : (
          <p className="text-[13px] text-[#9A9A98] font-normal">Not provided yet</p>
        )}
      </section>

      <section>
        <SectionLabel>Education</SectionLabel>
        {profile.education?.length > 0 ? (
          <div>
            {profile.education.map((edu) => (
              <ResumeEducationEntry key={edu.id} edu={edu} />
            ))}
          </div>
        ) : (
          <p className="text-[13px] text-[#9A9A98] font-normal">Not provided yet</p>
        )}
      </section>

      <section>
        <SectionLabel>Skills</SectionLabel>
        {profile.keySkills?.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {profile.keySkills.map((sk) => (
              <span key={sk} className="border border-black/[0.07] bg-[#F6F6F4] text-[#343432] text-[12px] px-3 py-1.5 rounded-full font-medium">
                {sk}
              </span>
            ))}
          </div>
        ) : (
          <p className="text-[13px] text-[#9A9A98] font-normal">Not provided yet</p>
        )}
      </section>

      <section>
          <SectionLabel>Certifications</SectionLabel>
          {profile.certifications?.length > 0 ? (
            <div className="flex flex-wrap gap-2">
              {profile.certifications.map((c) => (
                <span key={c} className="border border-[#DDD8EF] bg-[#F1EFF8] text-[#62578F] text-[12px] px-3 py-1.5 rounded-full font-medium">{c}</span>
              ))}
            </div>
          ) : <p className="text-[13px] text-[#9A9A98] font-normal">Not provided yet</p>}
      </section>

      <section>
          <SectionLabel>Preferred Roles</SectionLabel>
          {profile.preferred_roles?.length > 0 ? (
          <div className="flex flex-wrap gap-1.5">
            {profile.preferred_roles.map((r) => (
              <span key={r} className="bg-[#E7E3F0] text-[#62578F] text-[12px] px-3 py-1.5 rounded-full font-medium">
                {r}
              </span>
            ))}
          </div>
          ) : <p className="text-[13px] text-[#9A9A98] font-normal">Not provided yet</p>}
      </section>

      <section>
        <SectionLabel>Additional Information</SectionLabel>
        {profile.additional_information
          ? <p className="text-[13px] text-[#4A4A48] leading-[1.75] font-normal">{profile.additional_information}</p>
          : <p className="text-[13px] text-[#9A9A98] font-normal">Not provided yet</p>}
      </section>

      <section>
        <SectionLabel>Hobbies &amp; Interests</SectionLabel>
        {hobbies.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {hobbies.map((hobby) => <span key={hobby} className="bg-black/[0.03] text-[#4A4A48] text-[12px] px-3 py-1.5 rounded-full">{hobby}</span>)}
          </div>
        ) : <p className="text-[13px] text-[#9A9A98] font-normal">Not provided yet</p>}
      </section>
    </div>
  );
}

export function JobsTab({ jobs, matchingJobsTotal, onTrack, onDismiss, selectedJob, setSelectedJob, candidateId, onJobViewed, onLockedJobClick }) {
  const [detailJob, setDetailJob] = React.useState(null);
  const [pendingDismissJob, setPendingDismissJob] = React.useState(null);
  const [applying, setApplying] = React.useState(false);
  const [dismissing, setDismissing] = React.useState(false);
  const [improvementJob, setImprovementJob] = React.useState(null);
  const [improvementData, setImprovementData] = React.useState(null);
  // Keep backend ranking intact within each access group, while presenting the
  // jobs a candidate can open before the subscription-locked placeholders.
  const accessibleJobs = jobs.filter((job) => !job.locked);
  const lockedJobs = jobs.filter((job) => job.locked);
  const orderedJobs = [...accessibleJobs, ...lockedJobs];

  const openDetail = React.useCallback(async (job) => {
    setDetailJob(job);
    if (!job.viewed && candidateId) {
      try {
        await axios.post(`${API}/candidate/${candidateId}/jobs/${job.id}/view`);
        onJobViewed?.(job.id);
      } catch {
        // silent
      }
    }
  }, [candidateId, onJobViewed]);

  const handleApply = React.useCallback(async () => {
    if (!detailJob?.job_url) return;
    setImprovementJob(detailJob);
    setImprovementData(null);
    try {
      const response = await axios.get(`${API}/candidate/${candidateId}/jobs/${detailJob.id}/match-improvement`);
      setImprovementData(response.data);
    } catch {
      // Keep the modal open with the recommendation's current score.
    }
  }, [candidateId, detailJob]);
  const openResumeEditor = React.useCallback(async () => {
    if (!improvementJob) return;
    let credit;
    try {
      ({ data: credit } = await axios.post(`${API}/candidate/${candidateId}/jobs/${improvementJob.id}/resume-fix-credit-claim`));
    } catch (error) {
      const remainingCredits = error?.response?.data?.detail?.remaining_credits;
      if (remainingCredits != null) {
        window.dispatchEvent(new CustomEvent("eve:resume-fix-credits-updated", { detail: { remainingCredits } }));
      }
      if (error?.response?.status === 403 && error.response.data?.detail?.code === "resume_fix_credits_insufficient") {
        window.dispatchEvent(new CustomEvent("eve:resume-fix-credits-insufficient"));
      }
      return;
    }
    if (credit.remaining_credits != null) {
      window.dispatchEvent(new CustomEvent("eve:resume-fix-credits-updated", { detail: { remainingCredits: credit.remaining_credits } }));
    }
    const params = new URLSearchParams({ candidate_id: candidateId, recommendation_id: improvementJob.id, previous_match_score: String(improvementData?.match_score ?? improvementJob.match_score ?? "") });
    if (credit.claim_id) params.set("fix_credit_claim_id", credit.claim_id);
    if (credit.remaining_credits != null) params.set("remaining_credits", String(credit.remaining_credits));
    if (improvementJob.job_id) params.set("job_id", improvementJob.job_id);
    window.open(`/resume-editor?${params.toString()}`, "_blank", "noopener,noreferrer");
    setImprovementJob(null);
  }, [candidateId, improvementData?.match_score, improvementJob]);

  const handleNotInterested = React.useCallback(async () => {
    if (!detailJob) return;
    setDetailJob(null);
    setPendingDismissJob(detailJob);
  }, [detailJob]);

  const handleConfirmDismiss = React.useCallback(async (reason) => {
    if (!pendingDismissJob || dismissing) return;
    const id = pendingDismissJob.id;
    setDismissing(true);
    try {
      await onDismiss(id, reason);
      setPendingDismissJob(null);
    } catch {
      // parent handles refresh / fallback
    } finally {
      setDismissing(false);
    }
  }, [pendingDismissJob, dismissing, onDismiss]);

  return (
    <div className="relative">
      <NotInterestedReasonModal
        open={Boolean(pendingDismissJob)}
        job={pendingDismissJob}
        busy={dismissing}
        onClose={() => setPendingDismissJob(null)}
        onConfirm={handleConfirmDismiss}
      />
      <ImproveMatchModal job={improvementJob} data={improvementData} onClose={() => setImprovementJob(null)} onApplyCurrent={() => { if (improvementJob?.job_url) window.open(improvementJob.job_url, "_blank", "noopener,noreferrer"); setImprovementJob(null); }} onFixResume={openResumeEditor} />
      {detailJob && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/30"
          data-testid="jobs-detail-backdrop"
          onMouseDown={(e) => { if (e.target === e.currentTarget) setDetailJob(null); }}
        >
          <div className="relative w-full max-w-lg h-[80vh] bg-[#FBFBF9] rounded-2xl overflow-hidden shadow-2xl">
            <JobDetailModal
              job={detailJob}
              onClose={() => setDetailJob(null)}
              onApply={handleApply}
              onNotInterested={handleNotInterested}
              applying={applying}
            />
          </div>
        </div>
      )}
      <div className="space-y-4" data-testid="jobs-tab-content">
        <p className="text-[12px] text-[#9A9A98] font-normal">
          {matchingJobsTotal} matches ranked by fit
        </p>

        {jobs.length === 0 && (
          <div className="py-10 text-center">
            <p className="text-[13px] text-[#9A9A98] font-normal">
              No job recommendations yet. Check back soon.
            </p>
          </div>
        )}

        <div className="flex flex-col gap-3" data-testid="jobs-vertical-list">
          {orderedJobs.map((job) => {
            if (job.locked) {
              return (
                <button
                  key={job.id}
                  type="button"
                  data-testid={`locked-job-card-${job.id}`}
                  aria-label="Locked job match. View plans to unlock."
                  onClick={onLockedJobClick}
                  className="relative min-h-[176px] w-full overflow-hidden rounded-xl border border-black/[0.06] bg-white text-left shadow-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#62578F]"
                >
                  <div className="pointer-events-none select-none p-4 blur-[7px]" aria-hidden="true">
                    <div className="h-10 w-10 rounded-lg bg-[#E7E3F0]" />
                    <div className="mt-3 h-3 w-36 rounded bg-black/[0.12]" />
                    <div className="mt-2 h-3 w-24 rounded bg-black/[0.08]" />
                    <div className="mt-7 h-3 w-full rounded bg-black/[0.07]" />
                    <div className="mt-2 h-3 w-5/6 rounded bg-black/[0.07]" />
                    <div className="mt-6 flex gap-2"><span className="h-8 w-24 rounded-full bg-black/[0.08]" /><span className="h-8 w-24 rounded-full bg-black/[0.08]" /></div>
                  </div>
                  <span className="absolute inset-0 flex flex-col items-center justify-center bg-white/25 text-center">
                    <span className="flex h-9 w-9 items-center justify-center rounded-full bg-white/95 text-[#62578F] shadow-sm"><LockKeyhole className="h-4 w-4" /></span>
                    <span className="mt-2 text-[12px] font-semibold text-[#3E394E]">Unlock this match</span>
                    <span className="mt-1 text-[11px] text-[#62578F]">View plans</span>
                  </span>
                </button>
              );
            }
            const isSelected = selectedJob?.id === job.id;
            const matchPct = job.match_score != null
              ? `${Math.round(job.match_score * (job.match_score <= 1 ? 100 : 1))}%`
              : null;
            return (
              <div
                key={job.id}
                role="button"
                tabIndex={0}
                onClick={() => { setSelectedJob(job); openDetail(job); }}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    setSelectedJob(job);
                    openDetail(job);
                  }
                }}
                data-testid={`job-card-${job.id}`}
                className={`min-h-[176px] w-full overflow-hidden text-left rounded-xl border border-black/[0.06] bg-white px-4 py-4 shadow-sm transition-colors eve-hover-row ${
                  isSelected ? "bg-black/[0.04]" : ""
                }`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-start gap-3 flex-1 min-w-0">
                    {job.logo ? (
                      <img
                        src={job.logo}
                        alt={job.company}
                        className="w-10 h-10 rounded-lg object-cover shrink-0"
                      />
                    ) : (
                      <div className="w-10 h-10 rounded-lg bg-[#E7E3F0] flex items-center justify-center shrink-0">
                        <span className="text-[13px] font-medium text-[#7B6FB8]">
                          {(job.company || "?")[0].toUpperCase()}
                        </span>
                      </div>
                    )}
                    <div className="flex-1 min-w-0">
                      <h4 className="text-[13.5px] font-medium text-[#1F1F1F] truncate">
                        {job.title}
                      </h4>
                      <p className="text-[11.5px] text-[#9A9A98] mt-0.5 truncate font-normal">
                        {job.company} · {job.location}
                      </p>
                      {job.salary && (
                        <p className="text-[12px] text-[#1F1F1F] mt-1.5 font-medium">
                          {job.salary}
                        </p>
                      )}
                    </div>
                  </div>
                  {matchPct && (
                    <span className="text-[11px] font-medium text-[#2E7538] bg-[#E7F2E4] rounded-full px-2 py-1 shrink-0">
                      {matchPct}
                    </span>
                  )}
                </div>
                <p className="text-[12.5px] text-[#4A4A48] mt-3 line-clamp-2 leading-relaxed font-normal">
                  {stripHtml(job.description)}
                </p>
                <div className="flex items-center gap-2 mt-3">
                <button
                    onClick={(e) => {
                      e.stopPropagation();
                      setDetailJob(null);
                      setPendingDismissJob(job);
                    }}
                    data-testid={`job-dismiss-${job.id}`}
                    className="flex-1 text-[12px] font-normal text-[#4A4A48] bg-black/[0.03] hover:bg-black/[0.06] rounded-full py-1.5 transition-colors"
                  >
                    Not for me
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); onTrack(job.id); }}
                    data-testid={`job-track-${job.id}`}
                    className={`flex-1 text-[12px] font-medium rounded-full py-1.5 transition-colors flex items-center justify-center gap-1.5 ${
                      job.tracked
                        ? "bg-[#2E7538] text-white"
                        : "bg-[#1F1F1F] text-white hover:bg-black"
                    }`}
                  >
                    {job.tracked ? (
                      <><BookmarkCheck className="w-3.5 h-3.5" strokeWidth={2} />Tracked</>
                    ) : (
                      <><Bookmark className="w-3.5 h-3.5" strokeWidth={2} />Track</>
                    )}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function TrackedTab({ jobs, onTrack, onDismissJob, candidateId }) {
  const tracked = jobs.filter((j) => j.tracked);
  const [detailJob, setDetailJob] = React.useState(null);
  const [pendingDismissJob, setPendingDismissJob] = React.useState(null);
  const [dismissing, setDismissing] = React.useState(false);
  const [improvementJob, setImprovementJob] = React.useState(null);
  const [improvementData, setImprovementData] = React.useState(null);

  const handleApply = React.useCallback(async () => {
    if (!detailJob?.job_url) return;
    setImprovementJob(detailJob);
    setImprovementData(null);
    try {
      const response = await axios.get(`${API}/candidate/${candidateId}/jobs/${detailJob.id}/match-improvement`);
      setImprovementData(response.data);
    } catch {
      // Keep the modal open with the recommendation's current score.
    }
  }, [candidateId, detailJob]);
  const openResumeEditor = React.useCallback(async () => {
    if (!improvementJob) return;
    let credit;
    try {
      ({ data: credit } = await axios.post(`${API}/candidate/${candidateId}/jobs/${improvementJob.id}/resume-fix-credit-claim`));
    } catch (error) {
      const remainingCredits = error?.response?.data?.detail?.remaining_credits;
      if (remainingCredits != null) {
        window.dispatchEvent(new CustomEvent("eve:resume-fix-credits-updated", { detail: { remainingCredits } }));
      }
      if (error?.response?.status === 403 && error.response.data?.detail?.code === "resume_fix_credits_insufficient") {
        window.dispatchEvent(new CustomEvent("eve:resume-fix-credits-insufficient"));
      }
      return;
    }
    if (credit.remaining_credits != null) {
      window.dispatchEvent(new CustomEvent("eve:resume-fix-credits-updated", { detail: { remainingCredits: credit.remaining_credits } }));
    }
    const params = new URLSearchParams({ candidate_id: candidateId, recommendation_id: improvementJob.id, previous_match_score: String(improvementData?.match_score ?? improvementJob.match_score ?? "") });
    if (credit.claim_id) params.set("fix_credit_claim_id", credit.claim_id);
    if (credit.remaining_credits != null) params.set("remaining_credits", String(credit.remaining_credits));
    if (improvementJob.job_id) params.set("job_id", improvementJob.job_id);
    window.open(`/resume-editor?${params.toString()}`, "_blank", "noopener,noreferrer");
    setImprovementJob(null);
  }, [candidateId, improvementData?.match_score, improvementJob]);

  const handleNotInterested = React.useCallback(() => {
    if (!detailJob) return;
    setDetailJob(null);
    setPendingDismissJob(detailJob);
  }, [detailJob]);

  const handleConfirmDismiss = React.useCallback(async (reason) => {
    if (!pendingDismissJob || dismissing) return;
    setDismissing(true);
    try {
      await onDismissJob?.(pendingDismissJob.id, reason);
      setPendingDismissJob(null);
    } catch {
      // parent handles refresh
    } finally {
      setDismissing(false);
    }
  }, [pendingDismissJob, dismissing, onDismissJob]);

  return (
    <div className="space-y-2" data-testid="tracked-tab-content">
      <NotInterestedReasonModal
        open={Boolean(pendingDismissJob)}
        job={pendingDismissJob}
        busy={dismissing}
        onClose={() => setPendingDismissJob(null)}
        onConfirm={handleConfirmDismiss}
      />
      <ImproveMatchModal job={improvementJob} data={improvementData} onClose={() => setImprovementJob(null)} onApplyCurrent={() => { if (improvementJob?.job_url) window.open(improvementJob.job_url, "_blank", "noopener,noreferrer"); setImprovementJob(null); }} onFixResume={openResumeEditor} />
      {detailJob && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/30"
          data-testid="tracked-detail-backdrop"
          onMouseDown={(e) => { if (e.target === e.currentTarget) setDetailJob(null); }}
        >
          <div className="relative w-full max-w-lg h-[80vh] bg-[#FBFBF9] rounded-2xl overflow-hidden shadow-2xl">
            <JobDetailModal
              job={detailJob}
              onClose={() => setDetailJob(null)}
              onApply={handleApply}
              onNotInterested={handleNotInterested}
              applying={false}
            />
          </div>
        </div>
      )}
      {tracked.length === 0 ? (
        <div className="py-10 text-center">
          <p className="text-[13px] text-[#9A9A98] font-normal">
            You're not tracking any roles yet. Tap "Track" on a job to save it here.
          </p>
        </div>
      ) : (
        tracked.map((job) => (
          <button
            key={job.id}
            data-testid={`tracked-job-card-${job.id}`}
            onClick={() => setDetailJob(job)}
            className="text-left w-full eve-hover-row flex items-center justify-between gap-3 px-3 py-3 -mx-3"
          >
            <div className="flex items-center gap-3 min-w-0">
              {job.logo ? (
                <img src={job.logo} alt={job.company} className="w-9 h-9 rounded-lg object-cover" />
              ) : (
                <div className="w-9 h-9 rounded-lg bg-[#E7E3F0] flex items-center justify-center shrink-0">
                  <span className="text-[12px] font-medium text-[#7B6FB8]">
                    {(job.company || "?")[0].toUpperCase()}
                  </span>
                </div>
              )}
              <div className="min-w-0">
                <p className="text-[13px] font-medium text-[#1F1F1F] truncate">{job.title}</p>
                <p className="text-[11.5px] text-[#9A9A98] truncate font-normal">
                  {job.company} · {job.location}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              {job.applied && (
                <span className="text-[11px] font-medium text-[#2E7538] bg-[#E7F2E4] rounded-full px-2 py-1">
                  Applied
                </span>
              )}
              <span
                onClick={(e) => { e.stopPropagation(); onTrack(job.id); }}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => e.key === "Enter" && (e.stopPropagation(), onTrack(job.id))}
                className="text-[11.5px] font-normal text-[#4A4A48] hover:text-[#1F1F1F] underline underline-offset-2"
              >
                Untrack
              </span>
            </div>
          </button>
        ))
      )}
    </div>
  );
}

function DocumentsTab({ documents, docsLoading, candidateId, candidateToken, onResumeReplaced, onCertUploaded, onCertReplaced, onResumeDeleted, onCertDeleted }) {
  const resumeInputRef = React.useRef(null);
  const certInputRef = React.useRef(null);
  const certReplaceRefs = React.useRef({});
  const [busy, setBusy] = React.useState(false);
  const [deleteError, setDeleteError] = React.useState(null);
  const [confirmDelete, setConfirmDelete] = React.useState(null); // { type: 'resume' } | { type: 'cert', id, filename }

  const authHeaders = candidateToken ? { Authorization: `Bearer ${candidateToken}` } : {};
  const postDocument = (path, formData) => (
    candidateToken
      ? axios.post(`${API}${path}`, formData, { headers: authHeaders })
      : axios.post(`${API}${path}`, formData)
  );

  const openDocument = (event, path) => {
    event.preventDefault();
    const viewWindowName = `eve-document-${Date.now()}`;
    const documentWindow = window.open("", viewWindowName);
    if (documentWindow) documentWindow.opener = null;
    if (!candidateToken || !documentWindow) {
      documentWindow?.close();
      setDeleteError("Document could not be opened. Please try again.");
      return;
    }
    // Submit to the real file endpoint so the browser renders its FileResponse
    // in a new tab. The session token is form data, never part of the URL.
    const form = document.createElement("form");
    form.method = "post";
    form.action = `${API}${path}`;
    form.target = viewWindowName;
    form.style.display = "none";
    const tokenField = document.createElement("input");
    tokenField.type = "hidden";
    tokenField.name = "candidate_token";
    tokenField.value = candidateToken;
    form.appendChild(tokenField);
    document.body.appendChild(form);
    form.submit();
    form.remove();
  };

  const handleResumeReplace = async (file) => {
    if (!file || !candidateId) return;
    setBusy(true);
    try {
      const arrayBuffer = await file.arrayBuffer();
      const hashBuffer = await crypto.subtle.digest("SHA-256", arrayBuffer);
      const fingerprint = Array.from(new Uint8Array(hashBuffer)).map((b) => b.toString(16).padStart(2, "0")).join("");
      if (documents.resume?.fingerprint && documents.resume.fingerprint === fingerprint) {
        setDeleteError("Duplicate Upload");
        return;
      }
      const fd = new FormData();
      fd.append("file", file);
      const res = await postDocument(`/candidate/${candidateId}/resume/replace`, fd);
      onResumeReplaced(file.name, res.data?.profile ?? null);
    } catch {
      onResumeReplaced(file.name, null);
    } finally {
      setBusy(false);
    }
  };

  const handleCertUpload = async (file) => {
    if (!file || !candidateId) return;
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await postDocument(`/candidate/${candidateId}/certificates/upload`, fd);
      onCertUploaded(res.data);
    } catch {
      // silent
    } finally {
      setBusy(false);
    }
  };

  const handleCertReplace = async (certId, file) => {
    if (!file || !candidateId) return;
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      await postDocument(`/candidate/${candidateId}/certificates/${certId}/replace`, fd);
      onCertReplaced(certId, file.name);
    } catch {
      // silent
    } finally {
      setBusy(false);
    }
  };

  const handleConfirmDelete = async () => {
    if (!confirmDelete || !candidateId) return;
    setBusy(true);
    setDeleteError(null);
    try {
      if (confirmDelete.type === "resume") {
        await axios.delete(`${API}/candidate/${candidateId}/resume`, { headers: authHeaders });
        setConfirmDelete(null);
        onResumeDeleted();
      } else {
        await axios.delete(`${API}/candidate/${candidateId}/certificates/${confirmDelete.id}`, { headers: authHeaders });
        setConfirmDelete(null);
        onCertDeleted(confirmDelete.id);
      }
    } catch {
      setDeleteError("Deletion failed. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  if (docsLoading) {
    return <p className="text-[13px] text-[#9A9A98] font-normal">Loading documents…</p>;
  }

  return (
    <div className="space-y-8" data-testid="documents-tab-content">
      {/* Confirmation dialog */}
      {confirmDelete && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
          <div className="bg-white rounded-2xl shadow-xl px-6 py-5 max-w-sm w-full mx-4">
            <p className="text-[14px] font-medium text-[#1F1F1F] mb-1">Delete document?</p>
            <p className="text-[13px] text-[#4A4A48] mb-4">
              Are you sure you want to delete{" "}
              <span className="font-medium">
                {confirmDelete.type === "resume" ? documents.resume?.filename : confirmDelete.filename}
              </span>?
            </p>
            {deleteError && (
              <p className="text-[12px] text-red-500 mb-3">{deleteError}</p>
            )}
            <div className="flex gap-3">
              <button
                onClick={() => { setConfirmDelete(null); setDeleteError(null); }}
                disabled={busy}
                className="flex-1 py-2 rounded-xl bg-black/[0.05] text-[#4A4A48] text-[13px] font-normal hover:bg-black/[0.09] transition-colors disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={handleConfirmDelete}
                disabled={busy}
                className="flex-1 py-2 rounded-xl bg-red-500 text-white text-[13px] font-medium hover:bg-red-600 transition-colors disabled:opacity-50"
              >
                {busy ? "Deleting…" : "Delete"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Resume */}
      <div>
        <h3 className="text-[13px] font-medium text-[#1F1F1F] mb-3">Resume</h3>
        {documents.resume ? (
          <div className="eve-hover-row flex items-center justify-between gap-3 px-3 py-3 -mx-3">
            <a
              href={`${API}/candidate/${candidateId}/resume/view`}
              onClick={(event) => openDocument(event, `/candidate/${candidateId}/resume/view`)}
              target="_blank"
              rel="noreferrer"
              className="text-[13px] font-medium text-[#1F1F1F] truncate hover:underline min-w-0"
            >
              {documents.resume.filename}
            </a>
            <div className="flex items-center gap-2 shrink-0">
              <a
                href={`${API}/candidate/${candidateId}/resume/view`}
                onClick={(event) => openDocument(event, `/candidate/${candidateId}/resume/view`)}
                target="_blank"
                rel="noreferrer"
                className="text-[12px] font-normal text-[#4A4A48] bg-black/[0.03] hover:bg-black/[0.06] rounded-full px-3 py-1.5 transition-colors"
              >
                View
              </a>
              <input
                ref={resumeInputRef}
                type="file"
                accept=".pdf"
                className="hidden"
                onChange={(e) => e.target.files?.[0] && handleResumeReplace(e.target.files[0])}
              />
              <button
                onClick={() => resumeInputRef.current?.click()}
                disabled={busy}
                className="text-[12px] font-normal text-[#4A4A48] bg-black/[0.03] hover:bg-black/[0.06] rounded-full px-3 py-1.5 transition-colors disabled:opacity-50"
              >
                Replace
              </button>
              <button
                onClick={() => setConfirmDelete({ type: "resume" })}
                disabled={busy}
                className="text-[12px] font-normal text-red-500 bg-red-50 hover:bg-red-100 rounded-full px-3 py-1.5 transition-colors disabled:opacity-50"
              >
                Delete
              </button>
            </div>
          </div>
        ) : (
          <div className="flex items-center gap-3">
            <p className="text-[13px] text-[#9A9A98] font-normal">No resume on file.</p>
            <input
              ref={resumeInputRef}
              type="file"
              accept=".pdf"
              className="hidden"
              onChange={(e) => e.target.files?.[0] && handleResumeReplace(e.target.files[0])}
            />
            <button
              data-testid="add-resume-btn"
              onClick={() => resumeInputRef.current?.click()}
              disabled={busy || !candidateId}
              className="text-[12px] font-normal text-[#4A4A48] bg-black/[0.03] hover:bg-black/[0.06] rounded-full px-3 py-1.5 transition-colors disabled:opacity-50"
            >
              Add Resume
            </button>
          </div>
        )}
      </div>

      {/* Certificates */}
      <div>
        <h3 className="text-[13px] font-medium text-[#1F1F1F] mb-3">Certificates</h3>
        {documents.certificates.length > 0 ? (
          <div className="space-y-1">
            {documents.certificates.map((cert) => (
              <div
                key={cert.id}
                className="eve-hover-row flex items-center justify-between gap-3 px-3 py-3 -mx-3"
              >
                <a
                  href={`${API}/candidate/${candidateId}/certificates/${cert.id}/view`}
                  onClick={(event) => openDocument(event, `/candidate/${candidateId}/certificates/${cert.id}/view`)}
                  target="_blank"
                  rel="noreferrer"
                  className="text-[13px] font-medium text-[#1F1F1F] truncate hover:underline min-w-0"
                >
                  {cert.filename}
                </a>
                <div className="flex items-center gap-2 shrink-0">
                  <a
                    href={`${API}/candidate/${candidateId}/certificates/${cert.id}/view`}
                    onClick={(event) => openDocument(event, `/candidate/${candidateId}/certificates/${cert.id}/view`)}
                    target="_blank"
                    rel="noreferrer"
                    className="text-[12px] font-normal text-[#4A4A48] bg-black/[0.03] hover:bg-black/[0.06] rounded-full px-3 py-1.5 transition-colors"
                  >
                    View
                  </a>
                  <input
                    ref={(el) => { certReplaceRefs.current[cert.id] = el; }}
                    type="file"
                    accept=".pdf,.doc,.docx,.png,.jpg,.jpeg"
                    className="hidden"
                    onChange={(e) => e.target.files?.[0] && handleCertReplace(cert.id, e.target.files[0])}
                  />
                  <button
                    onClick={() => certReplaceRefs.current[cert.id]?.click()}
                    disabled={busy}
                    className="text-[12px] font-normal text-[#4A4A48] bg-black/[0.03] hover:bg-black/[0.06] rounded-full px-3 py-1.5 transition-colors disabled:opacity-50"
                  >
                    Replace
                  </button>
                  <button
                    onClick={() => setConfirmDelete({ type: "cert", id: cert.id, filename: cert.filename })}
                    disabled={busy}
                    className="text-[12px] font-normal text-red-500 bg-red-50 hover:bg-red-100 rounded-full px-3 py-1.5 transition-colors disabled:opacity-50"
                  >
                    Delete
                  </button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-[13px] text-[#9A9A98] font-normal">No certificates uploaded yet.</p>
        )}
        <input
          ref={certInputRef}
          type="file"
          accept=".pdf,.doc,.docx,.png,.jpg,.jpeg"
          className="hidden"
          onChange={(e) => e.target.files?.[0] && handleCertUpload(e.target.files[0])}
        />
        <button
          onClick={() => certInputRef.current?.click()}
          disabled={busy || !candidateId}
          className="mt-3 text-[12px] font-normal text-[#4A4A48] bg-black/[0.03] hover:bg-black/[0.06] rounded-full px-3 py-1.5 transition-colors disabled:opacity-50"
        >
          + Add certificate
        </button>
      </div>
    </div>
  );
}

function ActivityNotificationCard({ notif, onRead }) {
  const meta = notif.metadata || {};
  const isSlotBooking = notif.activity_type === "interview_slot_booking";
  const isSecondRound = notif.activity_type === "second_round_invite";

  return (
    <div
      data-testid={`activity-notif-${notif.id}`}
      className={`rounded-xl px-4 py-4 eve-hover-row transition-colors ${
        notif.is_read ? "opacity-60" : ""
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <p className="text-[13.5px] font-medium text-[#1F1F1F] truncate">{notif.title}</p>
          {isSlotBooking && (
            <span className="text-[11px] font-medium text-[#7B6FB8] bg-[#F0EEF9] rounded-full px-2 py-0.5 mt-1 inline-block">
              Interview Slot
            </span>
          )}
          {isSecondRound && (
            <span className="text-[11px] font-medium text-[#2E7538] bg-[#E7F2E4] rounded-full px-2 py-0.5 mt-1 inline-block">
              Second Round
            </span>
          )}
        </div>
        {!notif.is_read && (
          <span className="shrink-0 text-[11px] font-medium text-[#C58B3E] bg-[#FDF3E3] rounded-full px-2 py-1">
            New
          </span>
        )}
      </div>
      {notif.description && (
        <p className="text-[12px] text-[#4A4A48] mt-2 leading-relaxed">{notif.description}</p>
      )}
      {isSlotBooking && meta.booking_url && (
        <div className="mt-3">
          <a
            href={meta.booking_url}
            target="_blank"
            rel="noreferrer"
            data-testid={`slot-booking-link-${notif.id}`}
            className="inline-block text-[12.5px] font-medium text-white bg-[#1F1F1F] hover:bg-black rounded-xl px-4 py-2 transition-colors"
          >
            Book your interview slot
          </a>
          {meta.expires_at && (
            <p className="text-[11px] text-[#9A9A98] mt-1.5">
              Expires: {new Date(meta.expires_at).toLocaleString()}
            </p>
          )}
        </div>
      )}
      {isSecondRound && (
        <div className="mt-3 space-y-1">
          {meta.round_name && (
            <p className="text-[12px] text-[#4A4A48]">
              <span className="font-medium">Round:</span> {meta.round_name}
            </p>
          )}
          {meta.scheduled_at && (
            <p className="text-[12px] text-[#4A4A48]">
              <span className="font-medium">Scheduled:</span> {new Date(meta.scheduled_at).toLocaleString()}
            </p>
          )}
          {meta.location && (
            <p className="text-[12px] text-[#4A4A48]">
              <span className="font-medium">Location:</span> {meta.location}
            </p>
          )}
          {meta.meeting_url && (
            <a
              href={meta.meeting_url}
              target="_blank"
              rel="noreferrer"
              data-testid={`second-round-meeting-link-${notif.id}`}
              className="inline-block text-[12px] font-medium text-[#7B6FB8] underline underline-offset-2"
            >
              Join meeting
            </a>
          )}
          {meta.instructions && (
            <p className="text-[12px] text-[#4A4A48] italic">{meta.instructions}</p>
          )}
        </div>
      )}
      {!notif.is_read && (
        <button
          onClick={() => onRead(notif.id)}
          className="mt-3 text-[11.5px] text-[#9A9A98] hover:text-[#4A4A48] underline underline-offset-2"
        >
          Mark as read
        </button>
      )}
    </div>
  );
}

function OpportunitiesTab({ candidateId, onInterested }) {
  const [opps, setOpps] = React.useState(null);
  const [notifications, setNotifications] = React.useState([]);
  const [selected, setSelected] = React.useState(null);
  const [responding, setResponding] = React.useState(false);
  const [error, setError] = React.useState(null);

  const load = React.useCallback(() => {
    if (!candidateId) return;
    axios
      .get(`${API}/candidate/${candidateId}/opportunities`)
      .then((res) => setOpps(res.data))
      .catch(() => setError("Could not load opportunities. Please try again."));
    axios
      .get(`${API}/candidate/${candidateId}/notifications`)
      .then((res) => setNotifications(res.data || []))
      .catch(() => {});
  }, [candidateId]);

  React.useEffect(() => { load(); }, [load]);

  const respond = async (recId, response) => {
    setResponding(true);
    try {
      const res = await axios.post(
        `${API}/candidate/${candidateId}/opportunities/${recId}/respond`,
        { response }
      );
      const updated = res.data.candidate_response ?? response;
      setOpps((prev) =>
        prev.map((o) => o.id === recId ? { ...o, candidate_response: updated } : o)
      );
      if (selected?.id === recId) setSelected((s) => ({ ...s, candidate_response: updated }));
      if (response === "interested" && onInterested) onInterested();
    } catch {
      setError("Failed to save your response. Please try again.");
    } finally {
      setResponding(false);
    }
  };

  const handleMarkRead = async (notifId) => {
    try {
      await axios.post(`${API}/candidate/${candidateId}/notifications/${notifId}/read`);
      setNotifications((prev) =>
        prev.map((n) => n.id === notifId ? { ...n, is_read: true } : n)
      );
    } catch {
      // silent
    }
  };

  if (error) return <p className="text-[13px] text-red-500">{error}</p>;
  if (opps === null) return <p className="text-[13px] text-[#9A9A98]">Loading…</p>;

  const pending = opps.filter((o) => !o.candidate_response);
  const responded = opps.filter((o) => o.candidate_response);
  const hasActivity = notifications.length > 0;

  if (opps.length === 0 && !hasActivity) {
    return (
      <div className="py-10 text-center">
        <p className="text-[13px] text-[#9A9A98]">No new opportunities yet.</p>
      </div>
    );
  }

  if (selected) {
    const job = selected.job || {};
    const skills = Array.isArray(job.skills)
      ? job.skills.map((s) => (typeof s === "string" ? s : s?.name ?? "")).filter(Boolean)
      : [];
    const responded_val = selected.candidate_response;
    return (
      <div className="space-y-5" data-testid="opportunity-detail">
        <button
          onClick={() => setSelected(null)}
          className="text-[12px] text-[#4A4A48] hover:text-[#1F1F1F] underline underline-offset-2"
        >
          ← Back
        </button>
        <div className="space-y-1">
          <h2 className="text-[16px] font-semibold text-[#1F1F1F]">{job.title || "Role"}</h2>
          <p className="text-[13px] text-[#4A4A48]">
            {job.company && <span className="font-medium">{job.company}</span>}
            {job.location && <span> · {job.location}</span>}
          </p>
        </div>
        {selected.recruiter_message && (
          <div className="bg-[#F4F4F2] rounded-xl px-4 py-3">
            <p className="text-[12px] text-[#4A4A48] font-normal italic">"{selected.recruiter_message}"</p>
          </div>
        )}
        {job.description && (
          <div>
            <p className="text-[12px] font-medium text-[#1F1F1F] mb-1">About the role</p>
            <p className="text-[12.5px] text-[#4A4A48] leading-relaxed">{job.description}</p>
          </div>
        )}
        {job.requirements && (
          <div>
            <p className="text-[12px] font-medium text-[#1F1F1F] mb-1">Requirements</p>
            <p className="text-[12.5px] text-[#4A4A48] leading-relaxed">{job.requirements}</p>
          </div>
        )}
        {skills.length > 0 && (
          <div>
            <p className="text-[12px] font-medium text-[#1F1F1F] mb-2">Skills</p>
            <div className="flex flex-wrap gap-1.5">
              {skills.map((sk) => (
                <span key={sk} className="bg-black/[0.03] text-[#1F1F1F] text-[12px] px-2.5 py-1 rounded-full">{sk}</span>
              ))}
            </div>
          </div>
        )}
        <div className="pt-2">
          {responded_val === "interested" && (
            <p className="text-[13px] font-medium text-[#2E7538]">✓ You expressed interest in this role.</p>
          )}
          {responded_val === "not_interested" && (
            <p className="text-[13px] text-[#9A9A98]">You passed on this opportunity.</p>
          )}
          {!responded_val && (
            <div className="flex gap-3">
              <button
                onClick={() => respond(selected.id, "interested")}
                disabled={responding}
                data-testid="opp-interested-btn"
                className="flex-1 py-2.5 rounded-xl bg-[#1F1F1F] text-white text-[13px] font-medium hover:bg-black transition-colors disabled:opacity-50"
              >
                Interested
              </button>
              <button
                onClick={() => respond(selected.id, "not_interested")}
                disabled={responding}
                data-testid="opp-not-interested-btn"
                className="flex-1 py-2.5 rounded-xl bg-black/[0.05] text-[#4A4A48] text-[13px] font-normal hover:bg-black/[0.09] transition-colors disabled:opacity-50"
              >
                Not Interested
              </button>
            </div>
          )}
        </div>
      </div>
    );
  }

  const renderCard = (opp) => {
    const job = opp.job || {};
    const resp = opp.candidate_response;
    return (
      <button
        key={opp.id}
        onClick={() => setSelected(opp)}
        data-testid={`opp-card-${opp.id}`}
        className="text-left w-full rounded-xl px-4 py-4 eve-hover-row transition-colors"
      >
        <div className="flex items-start justify-between gap-2">
          <div className="flex-1 min-w-0">
            <p className="text-[13.5px] font-medium text-[#1F1F1F] truncate">{job.title || "Role"}</p>
            <p className="text-[12px] text-[#9A9A98] mt-0.5 truncate">
              {job.company}{job.location ? ` · ${job.location}` : ""}
            </p>
          </div>
          {!resp && (
            <span className="shrink-0 text-[11px] font-medium text-[#C58B3E] bg-[#FDF3E3] rounded-full px-2 py-1">
              New
            </span>
          )}
          {resp === "interested" && (
            <span className="shrink-0 text-[11px] font-medium text-[#2E7538] bg-[#E7F2E4] rounded-full px-2 py-1">
              Interested
            </span>
          )}
          {resp === "not_interested" && (
            <span className="shrink-0 text-[11px] font-medium text-[#9A9A98] bg-black/[0.04] rounded-full px-2 py-1">
              Passed
            </span>
          )}
        </div>
        <p className="text-[12px] text-[#4A4A48] mt-2 line-clamp-2 leading-relaxed">
          {job.company
            ? `${job.company} is interested in your profile for ${job.title}.`
            : "A recruiter is interested in your profile."}
        </p>
      </button>
    );
  };

  return (
    <div className="space-y-4" data-testid="opportunities-tab-content">
      {pending.length > 0 && (
        <div>
          <p className="text-[11px] font-normal text-[#9A9A98] mb-2">Awaiting your response</p>
          <div className="space-y-1">{pending.map(renderCard)}</div>
        </div>
      )}
      {responded.length > 0 && (
        <div>
          <p className="text-[11px] font-normal text-[#9A9A98] mb-2 mt-4">Responded</p>
          <div className="space-y-1">{responded.map(renderCard)}</div>
        </div>
      )}
      {hasActivity && (
        <div>
          <p className="text-[11px] font-normal text-[#9A9A98] mb-2 mt-4">Updates</p>
          <div className="space-y-1" data-testid="activity-notifications-list">
            {notifications.map((n) => (
              <ActivityNotificationCard key={n.id} notif={n} onRead={handleMarkRead} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

const TAB_TITLES = {
  jobs: "New jobs",
  tracked: "Tracked jobs",
  profile: "Profile",
  documents: "Documents",
  opportunities: "Notifications",
};

function _profileDownloadFilename(candidateName) {
  const safeName = (candidateName || "").trim().replace(/\s+/g, "_").replace(/[^A-Za-z0-9._-]/g, "");
  return `${safeName || "candidate_profile"}_profile.pdf`;
}

function _extractFilenameFromContentDisposition(value) {
  if (!value || typeof value !== "string") return null;
  const utf8Match = value.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match?.[1]) return decodeURIComponent(utf8Match[1]);
  const plainMatch = value.match(/filename="?([^"]+)"?/i);
  return plainMatch?.[1] || null;
}

async function downloadProfilePdf(candidateId, candidateName) {
  try {
    const response = await axios.get(`${API}/candidate/${candidateId}/profile/download`, {
      responseType: "blob",
    });
    const blob = new Blob([response.data], { type: "application/pdf" });
    const downloadUrl = window.URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = downloadUrl;
    link.download =
      _extractFilenameFromContentDisposition(response.headers?.["content-disposition"]) ||
      _profileDownloadFilename(candidateName);
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(downloadUrl);
  } catch (error) {
    throw error;
  }
}

export default function LivingProfile({
  activeTab,
  userProfile,
  jobs,
  documents,
  docsLoading,
  candidateId,
  candidateToken,
  selectedJob,
  setSelectedJob,
  onTrackJob,
  onDismissJob,
  onToggleOpenToMatches,
  onResumeReplaced,
  onCertUploaded,
  onCertReplaced,
  onResumeDeleted,
  onCertDeleted,
  onInterested,
  onJobViewed,
  onLockedJobClick,
  onPhotoChange,
  matchingJobsTotal = jobs.length,
}) {
  const profileContentRef = React.useRef(null);
  const [pdfGenerating, setPdfGenerating] = React.useState(false);

  const handleDownloadPdf = async () => {
    if (pdfGenerating) return;
    setPdfGenerating(true);
    try {
      await downloadProfilePdf(candidateId || userProfile.candidate_id || userProfile.candidateId || userProfile.id, userProfile.name);
    } finally {
      setPdfGenerating(false);
    }
  };

  return (
    <aside
      data-testid="right-living-profile"
      className="h-full w-full flex flex-col bg-[#FDFDFC] overflow-hidden"
    >
      {/* Sticky header */}
      <div className="flex items-center justify-between px-8 pt-6 pb-4 shrink-0">
        <div className="flex items-center gap-2">
          <h1 className="text-[16px] font-medium text-[#1F1F1F] tracking-tight">
            {TAB_TITLES[activeTab] || "Profile"}
          </h1>
          <Info className="w-3.5 h-3.5 text-[#B5B5B3]" strokeWidth={1.5} />
        </div>
        <div className="flex items-center gap-3">
          {activeTab === "profile" && (
            <button
              onClick={handleDownloadPdf}
              disabled={pdfGenerating}
              data-testid="download-profile-pdf-btn"
              className="flex items-center gap-1.5 text-[12px] font-normal text-[#4A4A48] bg-black/[0.03] hover:bg-black/[0.07] rounded-full px-3 py-1.5 transition-colors disabled:opacity-50"
              title="Download profile as PDF"
            >
              <Download className="w-3.5 h-3.5" strokeWidth={1.75} />
              {pdfGenerating ? "Generating…" : "Download PDF"}
            </button>
          )}
          <ProfileStrengthBar
            label={userProfile.strength}
            percent={userProfile.strengthPercent}
          />
          {userProfile.recommendation_readiness && (
            <span
              data-testid="recommendation-readiness-badge"
              className={`text-[10px] font-medium px-2 py-0.5 rounded-full ml-2 ${
                userProfile.recommendation_readiness.level === "high"
                  ? "bg-[#D4EDDA] text-[#2E7538]"
                  : userProfile.recommendation_readiness.level === "medium"
                  ? "bg-[#FFF3CD] text-[#856404]"
                  : "bg-[#F8D7DA] text-[#721C24]"
              }`}
            >
              {userProfile.recommendation_readiness.level === "high"
                ? "Ready for recommendations"
                : userProfile.recommendation_readiness.level === "medium"
                ? "Partial match confidence"
                : "Building match confidence"}
            </span>
          )}
        </div>
      </div>

      {/* Scrollable content */}
      <div ref={profileContentRef} className="flex-1 overflow-y-auto eve-scroll px-8 pb-10">
        <div className="max-w-2xl mx-auto">
          {activeTab === "profile" && <ProfileTab user={userProfile} candidateId={candidateId} onToggleOpenToMatches={onToggleOpenToMatches} onPhotoChange={onPhotoChange} />}
          {activeTab === "jobs" && (
            <JobsTab
              jobs={jobs}
              matchingJobsTotal={matchingJobsTotal}
              onTrack={onTrackJob}
              onDismiss={onDismissJob}
              selectedJob={selectedJob}
              setSelectedJob={setSelectedJob}
              candidateId={candidateId}
              candidateToken={candidateToken}
              onJobViewed={onJobViewed}
              onLockedJobClick={onLockedJobClick}
            />
          )}
          {activeTab === "tracked" && (
            <TrackedTab jobs={jobs} onTrack={onTrackJob} onDismissJob={onDismissJob} candidateId={candidateId} />
          )}
          {activeTab === "documents" && (
            <DocumentsTab
              documents={documents}
              docsLoading={docsLoading}
              candidateId={candidateId}
              candidateToken={candidateToken}
              onResumeReplaced={onResumeReplaced}
              onCertUploaded={onCertUploaded}
              onCertReplaced={onCertReplaced}
              onResumeDeleted={onResumeDeleted}
              onCertDeleted={onCertDeleted}
            />
          )}
          {activeTab === "opportunities" && (
            <OpportunitiesTab candidateId={candidateId} onInterested={onInterested} />
          )}
        </div>
      </div>
    </aside>
  );
}
