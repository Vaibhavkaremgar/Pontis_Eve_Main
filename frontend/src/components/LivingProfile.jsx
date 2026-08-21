import React from "react";
import axios from "axios";
import DOMPurify from "dompurify";
import { Info, MapPin, Bookmark, BookmarkCheck, Bell, Download, Camera, Trash2, UserCircle2 } from "lucide-react";
import { JobDetailModal } from "./SwipeJobCard";
import { normalizeProfileForDisplay } from "../lib/profileNormalization";

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
  return (
    <div
      data-testid="profile-strength-bar"
      className="flex items-center gap-2.5"
    >
      <span className="text-[11.5px] text-[#9A9A98] font-normal">
        Profile strength:{" "}
        <span className="text-[#1F1F1F] font-medium">{label} {percent}%</span>
      </span>
      <div className="w-[90px] h-1.5 rounded-full bg-[#E7E3F0] overflow-hidden">
        <div
          className="h-full bg-[#7B6FB8] rounded-full transition-all"
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
    <h3 className="text-[13px] font-medium text-[#1F1F1F] mb-3">{children}</h3>
  );
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

const ALLOWED_PHOTO_TYPES = ["image/jpeg", "image/png", "image/webp"];
const MAX_PHOTO_BYTES = 5 * 1024 * 1024; // 5 MB

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

export function ProfileTab({ user, onToggleOpenToMatches, onPhotoChange }) {
  const profile = normalizeProfileForDisplay(user);
  const profileCandidateId = profile.candidate_id ?? profile.candidateId ?? profile.id ?? null;

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
              {profile.experience_years != null && (
                <span className="text-[12px] text-[#4A4A48] font-normal">
                  {profile.experience_years} yr{profile.experience_years !== 1 ? "s" : ""} exp
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

      {/* Bio */}
      <div>
        <SectionLabel>Bio</SectionLabel>
        {profile.bio ? (
          <p className="text-[13.5px] text-[#1F1F1F] leading-[1.7] font-normal">{profile.bio}</p>
        ) : (
          <p className="text-[13px] text-[#9A9A98] font-normal">Not provided yet</p>
        )}
      </div>

      {/* Experience */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <SectionLabel>
            {profile.name ? `Where ${profile.name.split(" ")[0]} has worked` : "Experience"}
          </SectionLabel>
          {profile.experience?.length > 0 && (
            <button className="text-[11.5px] text-[#4A4A48] hover:text-[#1F1F1F] underline underline-offset-2 font-normal">
              See all experiences
            </button>
          )}
        </div>
        {profile.experience?.length > 0 ? (
          <div className="space-y-1">
            {profile.experience.map((exp) => (
              <ExperienceRow key={exp.id} exp={exp} />
            ))}
          </div>
        ) : (
          <p className="text-[13px] text-[#9A9A98] font-normal">Not provided yet</p>
        )}
      </div>

      {/* Education */}
      <div>
        <SectionLabel>Education</SectionLabel>
        {profile.education?.length > 0 ? (
          <div className="space-y-1">
            {profile.education.map((edu) => (
              <EducationRow key={edu.id} edu={edu} />
            ))}
          </div>
        ) : (
          <p className="text-[13px] text-[#9A9A98] font-normal">Not provided yet</p>
        )}
      </div>

      {/* Skills */}
      <div>
        <SectionLabel>Verified skills</SectionLabel>
        {profile.keySkills?.length > 0 ? (
          <div className="flex flex-wrap gap-1.5">
            {profile.keySkills.map((sk) => (
              <span key={sk} className="bg-black/[0.03] text-[#1F1F1F] text-[12px] px-2.5 py-1 rounded-full font-normal">
                {sk}
              </span>
            ))}
          </div>
        ) : (
          <p className="text-[13px] text-[#9A9A98] font-normal">Not provided yet</p>
        )}
      </div>

      {/* Preferred roles */}
      {profile.preferred_roles?.length > 0 && (
        <div>
          <SectionLabel>Preferred roles</SectionLabel>
          <div className="flex flex-wrap gap-1.5">
            {profile.preferred_roles.map((r) => (
              <span key={r} className="bg-[#E7E3F0] text-[#7B6FB8] text-[12px] px-2.5 py-1 rounded-full font-normal">
                {r}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Certifications */}
      {profile.certifications?.length > 0 && (
        <div>
          <SectionLabel>Certifications</SectionLabel>
          <div className="flex flex-wrap gap-1.5">
            {profile.certifications.map((c) => (
              <span key={c} className="bg-black/[0.03] text-[#1F1F1F] text-[12px] px-2.5 py-1 rounded-full font-normal">
                {c}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Additional information */}
      {profile.additional_information && (
        <div>
          <SectionLabel>Additional information</SectionLabel>
          <p className="text-[13.5px] text-[#1F1F1F] leading-[1.7] font-normal">{profile.additional_information}</p>
        </div>
      )}
    </div>
  );
}

function JobsTab({ jobs, onTrack, onDismiss, selectedJob, setSelectedJob, candidateId, onJobViewed }) {
  const [detailJob, setDetailJob] = React.useState(null);
  const [applying, setApplying] = React.useState(false);

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

  const handleApply = React.useCallback(() => {
    if (!detailJob || !detailJob.job_url) return;
    window.open(detailJob.job_url, "_blank", "noopener,noreferrer");
  }, [detailJob]);

  const handleNotInterested = React.useCallback(async () => {
    if (!detailJob) return;
    const id = detailJob.id;
    setDetailJob(null);
    onDismiss(id);
  }, [detailJob, onDismiss]);

  return (
    <div className="relative">
      {detailJob && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
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
          {jobs.length} matches ranked by fit
        </p>

        {jobs.length === 0 && (
          <div className="py-10 text-center">
            <p className="text-[13px] text-[#9A9A98] font-normal">
              No job recommendations yet. Check back soon.
            </p>
          </div>
        )}

        <div className="space-y-2">
          {jobs.map((job) => {
            const isSelected = selectedJob?.id === job.id;
            const matchPct = job.match_score != null
              ? `${Math.round(job.match_score * (job.match_score <= 1 ? 100 : 1))}%`
              : null;
            return (
              <button
                key={job.id}
                onClick={() => { setSelectedJob(job); openDetail(job); }}
                data-testid={`job-card-${job.id}`}
                className={`text-left w-full rounded-xl px-4 py-4 transition-colors eve-hover-row ${
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
                    onClick={(e) => { e.stopPropagation(); onDismiss(job.id); }}
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
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function TrackedTab({ jobs, onTrack }) {
  const tracked = jobs.filter((j) => j.tracked);
  return (
    <div className="space-y-2" data-testid="tracked-tab-content">
      {tracked.length === 0 ? (
        <div className="py-10 text-center">
          <p className="text-[13px] text-[#9A9A98] font-normal">
            You're not tracking any roles yet. Tap "Track" on a job to save it here.
          </p>
        </div>
      ) : (
        tracked.map((job) => (
          <div
            key={job.id}
            className="eve-hover-row flex items-center justify-between gap-3 px-3 py-3 -mx-3"
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
              <button
                onClick={() => onTrack(job.id)}
                className="text-[11.5px] font-normal text-[#4A4A48] hover:text-[#1F1F1F] underline underline-offset-2"
              >
                Untrack
              </button>
            </div>
          </div>
        ))
      )}
    </div>
  );
}

function DocumentsTab({ documents, docsLoading, candidateId, onResumeReplaced, onCertUploaded, onCertReplaced, onResumeDeleted, onCertDeleted }) {
  const resumeInputRef = React.useRef(null);
  const certInputRef = React.useRef(null);
  const certReplaceRefs = React.useRef({});
  const [busy, setBusy] = React.useState(false);
  const [deleteError, setDeleteError] = React.useState(null);
  const [confirmDelete, setConfirmDelete] = React.useState(null); // { type: 'resume' } | { type: 'cert', id, filename }

  const viewUrl = (path) => `${API}${path}`;

  const handleResumeReplace = async (file) => {
    if (!file || !candidateId) return;
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await axios.post(`${API}/candidate/${candidateId}/resume/replace`, fd);
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
      const res = await axios.post(`${API}/candidate/${candidateId}/certificates/upload`, fd);
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
      await axios.post(`${API}/candidate/${candidateId}/certificates/${certId}/replace`, fd);
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
        await axios.delete(`${API}/candidate/${candidateId}/resume`);
        setConfirmDelete(null);
        onResumeDeleted();
      } else {
        await axios.delete(`${API}/candidate/${candidateId}/certificates/${confirmDelete.id}`);
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
              href={viewUrl(`/candidate/${candidateId}/resume/view`)}
              target="_blank"
              rel="noreferrer"
              className="text-[13px] font-medium text-[#1F1F1F] truncate hover:underline min-w-0"
            >
              {documents.resume.filename}
            </a>
            <div className="flex items-center gap-2 shrink-0">
              <a
                href={viewUrl(`/candidate/${candidateId}/resume/view`)}
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
          <p className="text-[13px] text-[#9A9A98] font-normal">No resume on file.</p>
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
                  href={viewUrl(`/candidate/${candidateId}/certificates/${cert.id}/view`)}
                  target="_blank"
                  rel="noreferrer"
                  className="text-[13px] font-medium text-[#1F1F1F] truncate hover:underline min-w-0"
                >
                  {cert.filename}
                </a>
                <div className="flex items-center gap-2 shrink-0">
                  <a
                    href={viewUrl(`/candidate/${candidateId}/certificates/${cert.id}/view`)}
                    target="_blank"
                    rel="noreferrer"
                    className="text-[12px] font-normal text-[#4A4A48] bg-black/[0.03] hover:bg-black/[0.06] rounded-full px-3 py-1.5 transition-colors"
                  >
                    View
                  </a>
                  <input
                    ref={(el) => { certReplaceRefs.current[cert.id] = el; }}
                    type="file"
                    accept=".pdf,.png,.jpg,.jpeg"
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
          accept=".pdf,.png,.jpg,.jpeg"
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

async function downloadProfilePdf(contentRef, candidateName) {
  const { default: jsPDF } = await import("jspdf");
  const { default: html2canvas } = await import("html2canvas");

  const el = contentRef.current;
  if (!el) return;

  // Build a PDF-only clone with excluded sections removed and headings renamed
  const clone = el.cloneNode(true);
  clone.style.cssText = "position:fixed;left:-9999px;top:0;overflow:visible;max-height:none;height:auto;width:" + el.offsetWidth + "px;background:#FDFDFC;";
  document.body.appendChild(clone);

  const firstName = candidateName ? candidateName.split(" ")[0] : null;
  // Remove avatar/initials badge (the shrink-0 sibling in the header card flex row)
  const headerCard = clone.querySelector("[data-testid='profile-header-card']");
  if (headerCard) {
    const flexRow = headerCard.querySelector(".flex.items-start.justify-between");
    if (flexRow) {
      const avatar = flexRow.querySelector("img.rounded-full, div.rounded-full.shrink-0");
      avatar?.remove();
    }
  }

  clone.querySelectorAll("*").forEach((node) => {
    if (node.nodeType !== 1) return;
    const text = node.textContent.trim();
    if (node.getAttribute("role") === "switch") {
      node.closest(".inline-flex")?.remove();
      return;
    }
    if (node.tagName === "P" && text.startsWith("Available:")) { node.remove(); return; }
    if (node.tagName === "H3" && text === "Preferred roles") { node.closest("div")?.remove(); return; }
    if (node.tagName === "H3" && text === "Additional information") { node.closest("div")?.remove(); return; }
    if (node.tagName === "H3" && text === "Verified skills") { node.textContent = "Skills"; return; }
    if (node.tagName === "H3" && firstName && text === `Where ${firstName} has worked`) { node.textContent = "Experience"; }
  });

  try {
    const canvas = await html2canvas(clone, {
      scale: 2,
      useCORS: true,
      backgroundColor: "#FDFDFC",
      logging: false,
    });

    document.body.removeChild(clone);
    const imgData = canvas.toDataURL("image/png");
    const pdf = new jsPDF({ orientation: "portrait", unit: "mm", format: "a4" });

    const pageW = pdf.internal.pageSize.getWidth();
    const pageH = pdf.internal.pageSize.getHeight();
    const margin = 12;
    const printW = pageW - margin * 2;
    const imgW = canvas.width;
    const imgH = canvas.height;
    const ratio = printW / imgW;
    const printH = imgH * ratio;

    let yOffset = 0;
    let pageCount = 0;
    const pageContentH = pageH - margin * 2;

    while (yOffset < printH) {
      if (pageCount > 0) pdf.addPage();
      // srcY in canvas pixels for this page slice
      const srcY = (yOffset / ratio);
      const sliceH = Math.min(pageContentH / ratio, imgH - srcY);

      // Create a slice canvas
      const sliceCanvas = document.createElement("canvas");
      sliceCanvas.width = imgW;
      sliceCanvas.height = Math.ceil(sliceH);
      const ctx = sliceCanvas.getContext("2d");
      ctx.drawImage(canvas, 0, srcY, imgW, sliceH, 0, 0, imgW, sliceH);

      const sliceData = sliceCanvas.toDataURL("image/png");
      pdf.addImage(sliceData, "PNG", margin, margin, printW, sliceH * ratio);

      yOffset += pageContentH;
      pageCount++;
    }

    const filename = candidateName
      ? `${candidateName.replace(/\s+/g, "_")}_profile.pdf`
      : "candidate_profile.pdf";
    pdf.save(filename);
  } catch (err) {
    if (document.body.contains(clone)) document.body.removeChild(clone);
    throw err;
  }
}

export default function LivingProfile({
  activeTab,
  userProfile,
  jobs,
  documents,
  docsLoading,
  candidateId,
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
  onPhotoChange,
}) {
  const profileContentRef = React.useRef(null);
  const [pdfGenerating, setPdfGenerating] = React.useState(false);

  const handleDownloadPdf = async () => {
    if (pdfGenerating) return;
    setPdfGenerating(true);
    try {
      await downloadProfilePdf(profileContentRef, userProfile.name);
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
        </div>
      </div>

      {/* Scrollable content */}
      <div ref={profileContentRef} className="flex-1 overflow-y-auto eve-scroll px-8 pb-10">
        <div className="max-w-2xl mx-auto">
          {activeTab === "profile" && <ProfileTab user={userProfile} onToggleOpenToMatches={onToggleOpenToMatches} onPhotoChange={onPhotoChange} />}
          {activeTab === "jobs" && (
            <JobsTab
              jobs={jobs}
              onTrack={onTrackJob}
              onDismiss={onDismissJob}
              selectedJob={selectedJob}
              setSelectedJob={setSelectedJob}
              candidateId={candidateId}
              onJobViewed={onJobViewed}
            />
          )}
          {activeTab === "tracked" && (
            <TrackedTab jobs={jobs} onTrack={onTrackJob} />
          )}
          {activeTab === "documents" && (
            <DocumentsTab
              documents={documents}
              docsLoading={docsLoading}
              candidateId={candidateId}
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
