import React from "react";
import axios from "axios";
import { Info, MapPin, Bookmark, BookmarkCheck, Bell } from "lucide-react";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

function ProfileStrengthBar({ label, percent }) {
  return (
    <div
      data-testid="profile-strength-bar"
      className="flex items-center gap-2.5"
    >
      <span className="text-[11.5px] text-[#9A9A98] font-normal">
        Profile strength:{" "}
        <span className="text-[#1F1F1F] font-medium">{label}</span>
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

function ProfileTab({ user, onToggleOpenToMatches }) {
  return (
    <div className="space-y-8" data-testid="living-profile-content">
      {/* Header */}
      <div
        data-testid="profile-header-card"
        className={`relative rounded-2xl p-6 border border-black/[0.05] shadow-[0_1px_0_rgba(0,0,0,0.02)] overflow-hidden transition-opacity ${
          user.isOpenToMatches ? "" : "opacity-60"
        }`}
        style={{
          background: user.isOpenToMatches
            ? "linear-gradient(180deg, #EFEFED 0%, #F4F4F2 45%, #FAFAF8 100%)"
            : "linear-gradient(180deg, #E8E8E8 0%, #EFEFEF 45%, #F5F5F5 100%)",
        }}
      >
        <div className="flex items-start justify-between gap-5">
          <div className="flex-1 min-w-0">
            <h2 className="text-[19px] font-semibold text-[#1F1F1F] leading-tight tracking-tight">
              {user.name || "Your Profile"}
            </h2>
            {user.headline && (
              <p className="text-[13px] text-[#4A4A48] mt-2 leading-relaxed font-normal">
                {user.headline}
              </p>
            )}
            <div className="mt-5 flex items-center gap-3 flex-wrap">
              {user.location && (
                <span className="inline-flex items-center gap-1.5 text-[12px] text-[#4A4A48] font-normal">
                  <MapPin className="w-3.5 h-3.5 text-[#9A9A98]" strokeWidth={1.75} />
                  {user.location}
                </span>
              )}
              {user.experience_years != null && (
                <span className="text-[12px] text-[#4A4A48] font-normal">
                  {user.experience_years} yr{user.experience_years !== 1 ? "s" : ""} exp
                </span>
              )}
              <OpenToMatchesBadge isOpen={user.isOpenToMatches} onToggle={onToggleOpenToMatches} />
            </div>
            {user.availability && (
              <p className="mt-2 text-[12px] text-[#2E7538] font-normal">
                Available: {user.availability}
              </p>
            )}
          </div>
          {user.avatar ? (
            <img src={user.avatar} alt={user.name} className="w-14 h-14 rounded-full object-cover shrink-0" />
          ) : (
            <div className="w-14 h-14 rounded-full bg-[#E7E3F0] flex items-center justify-center shrink-0">
              <span className="text-[18px] font-medium text-[#7B6FB8]">
                {user.name
                  ? user.name.split(" ").map((n) => n[0]).join("").slice(0, 2).toUpperCase()
                  : "?"}
              </span>
            </div>
          )}
        </div>
      </div>

      {/* Bio */}
      <div>
        <SectionLabel>Bio</SectionLabel>
        {user.bio ? (
          <p className="text-[13.5px] text-[#1F1F1F] leading-[1.7] font-normal">{user.bio}</p>
        ) : (
          <p className="text-[13px] text-[#9A9A98] font-normal">Not provided yet</p>
        )}
      </div>

      {/* Experience */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <SectionLabel>
            {user.name ? `Where ${user.name.split(" ")[0]} has worked` : "Experience"}
          </SectionLabel>
          {user.experience?.length > 0 && (
            <button className="text-[11.5px] text-[#4A4A48] hover:text-[#1F1F1F] underline underline-offset-2 font-normal">
              See all experiences
            </button>
          )}
        </div>
        {user.experience?.length > 0 ? (
          <div className="space-y-1">
            {user.experience.map((exp) => (
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
        {user.education?.length > 0 ? (
          <div className="space-y-1">
            {user.education.map((edu) => (
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
        {user.keySkills?.length > 0 ? (
          <div className="flex flex-wrap gap-1.5">
            {user.keySkills.map((sk) => (
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
      {user.preferred_roles?.length > 0 && (
        <div>
          <SectionLabel>Preferred roles</SectionLabel>
          <div className="flex flex-wrap gap-1.5">
            {user.preferred_roles.map((r) => (
              <span key={r} className="bg-[#E7E3F0] text-[#7B6FB8] text-[12px] px-2.5 py-1 rounded-full font-normal">
                {r}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Certifications */}
      {user.certifications?.length > 0 && (
        <div>
          <SectionLabel>Certifications</SectionLabel>
          <div className="flex flex-wrap gap-1.5">
            {user.certifications.map((c) => (
              <span key={c} className="bg-black/[0.03] text-[#1F1F1F] text-[12px] px-2.5 py-1 rounded-full font-normal">
                {c}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Additional information */}
      {user.additional_information && (
        <div>
          <SectionLabel>Additional information</SectionLabel>
          <p className="text-[13.5px] text-[#1F1F1F] leading-[1.7] font-normal">{user.additional_information}</p>
        </div>
      )}
    </div>
  );
}

function JobsTab({ jobs, onTrack, onDismiss, selectedJob, setSelectedJob }) {
  return (
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
              onClick={() => setSelectedJob(job)}
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
                {job.description}
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
            <button
              onClick={() => onTrack(job.id)}
              className="text-[11.5px] font-normal text-[#4A4A48] hover:text-[#1F1F1F] underline underline-offset-2"
            >
              Untrack
            </button>
          </div>
        ))
      )}
    </div>
  );
}

function DocumentsTab({ documents, docsLoading, candidateId, onResumeReplaced, onCertUploaded, onCertReplaced }) {
  const resumeInputRef = React.useRef(null);
  const certInputRef = React.useRef(null);
  const certReplaceRefs = React.useRef({});
  const [busy, setBusy] = React.useState(false);

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

  if (docsLoading) {
    return <p className="text-[13px] text-[#9A9A98] font-normal">Loading documents…</p>;
  }

  return (
    <div className="space-y-8" data-testid="documents-tab-content">
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

function OpportunitiesTab({ candidateId }) {
  const [opps, setOpps] = React.useState(null);
  const [selected, setSelected] = React.useState(null);
  const [responding, setResponding] = React.useState(false);
  const [error, setError] = React.useState(null);

  const load = React.useCallback(() => {
    if (!candidateId) return;
    axios
      .get(`${API}/candidate/${candidateId}/opportunities`)
      .then((res) => setOpps(res.data))
      .catch(() => setError("Could not load opportunities. Please try again."));
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
    } catch {
      setError("Failed to save your response. Please try again.");
    } finally {
      setResponding(false);
    }
  };

  if (error) return <p className="text-[13px] text-red-500">{error}</p>;
  if (opps === null) return <p className="text-[13px] text-[#9A9A98]">Loading…</p>;

  const pending = opps.filter((o) => !o.candidate_response);
  const responded = opps.filter((o) => o.candidate_response);

  if (opps.length === 0) {
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
}) {
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
        <ProfileStrengthBar
          label={userProfile.strength}
          percent={userProfile.strengthPercent}
        />
      </div>

      {/* Scrollable content */}
      <div className="flex-1 overflow-y-auto eve-scroll px-8 pb-10">
        <div className="max-w-2xl mx-auto">
          {activeTab === "profile" && <ProfileTab user={userProfile} onToggleOpenToMatches={onToggleOpenToMatches} />}
          {activeTab === "jobs" && (
            <JobsTab
              jobs={jobs}
              onTrack={onTrackJob}
              onDismiss={onDismissJob}
              selectedJob={selectedJob}
              setSelectedJob={setSelectedJob}
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
            />
          )}
          {activeTab === "opportunities" && (
            <OpportunitiesTab candidateId={candidateId} />
          )}
        </div>
      </div>
    </aside>
  );
}
