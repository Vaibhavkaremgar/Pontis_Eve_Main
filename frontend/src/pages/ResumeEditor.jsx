import React from "react";
import axios from "axios";
import { useSearchParams } from "react-router-dom";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const plain = (value) => (value == null ? "" : String(value));
const list = (value) => Array.isArray(value) ? value : [];
const parseSkills = (value) => plain(value).split(/[,;|\n\u2022]+/).map((item) => item.trim()).filter(Boolean);
const score = (value) => value == null || !Number.isFinite(Number(value)) ? null : Math.round(Number(value) * (Number(value) <= 1 ? 100 : 1));

const shorten = (value, max = 120) => {
  const text = plain(value).replace(/\s+/g, " ").trim();
  if (text.length <= max) return text;
  const clipped = text.slice(0, max + 1).replace(/\s+\S*$/, "").replace(/[,:;\s]+$/, "");
  return `${clipped}.`;
};

const conciseSummary = (value) => {
  const sentences = plain(value).replace(/\s+/g, " ").trim().split(/(?<=[.!?])\s+/).filter(Boolean);
  return shorten(sentences.slice(0, 2).join(" "), 220);
};

const bulletize = (value, limit = 3, maxLength = 120) => {
  const source = Array.isArray(value) ? value.join("\n") : plain(value);
  const points = source
    .replace(/^[•\-]\s*/gm, "")
    .split(/\n+|(?<=[.!?])\s+/)
    .map((item) => shorten(item, maxLength).replace(/[.]+$/, ""))
    .filter(Boolean)
    .slice(0, limit);
  return points.map((item) => `• ${item}.`).join("\n");
};

const atsEntry = (item, limit, maxLength) => {
  if (typeof item === "string") return bulletize(item, limit, maxLength);
  if (!item || typeof item !== "object") return item;
  const detailKey = item.description != null ? "description" : item.responsibilities != null ? "responsibilities" : item.details != null ? "details" : null;
  return detailKey ? { ...item, [detailKey]: bulletize(item[detailKey], limit, maxLength) } : item;
};

const makeAtsDraft = (resume, sections) => ({
  ...resume,
  bio: sections.summary ? conciseSummary(resume.bio) : resume.bio,
  work_experience: sections.experience ? list(resume.work_experience).map((item) => atsEntry(item, 3, 120)) : resume.work_experience,
  projects: sections.projects ? list(resume.projects).map((item) => atsEntry(item, 1, 130)) : resume.projects,
});

function Editable({ value, onChange, className = "", multiline = false, testId }) {
  return <div data-testid={testId} contentEditable suppressContentEditableWarning role="textbox" aria-multiline={multiline}
    onBlur={(event) => onChange(event.currentTarget.innerText)} className={`outline-none rounded hover:bg-slate-50 focus:bg-amber-50 focus:ring-1 focus:ring-amber-300 ${className}`}>{plain(value)}</div>;
}

function MatchOdometer({ from, to }) {
  const [value, setValue] = React.useState(from ?? to);
  React.useEffect(() => {
    if (to == null) return undefined;
    const start = from ?? to;
    const started = performance.now(); let frame;
    const tick = (now) => { const p = Math.min((now - started) / 950, 1); setValue(Math.round(start + (to - start) * p)); if (p < 1) frame = requestAnimationFrame(tick); };
    frame = requestAnimationFrame(tick); return () => cancelAnimationFrame(frame);
  }, [from, to]);
  return <span data-testid="match-score-odometer">{value == null ? "—" : `${value}%`}</span>;
}

function sectionText(item) {
  if (typeof item === "string") return { title: item, detail: "" };
  return { title: item?.title || item?.role || item?.degree || item?.name || item?.company || "", detail: item?.description || item?.responsibilities || item?.details || item?.institution || "" };
}

export default function ResumeEditor() {
  const [params] = useSearchParams();
  const candidateId = params.get("candidate_id");
  const recommendationId = params.get("recommendation_id");
  const initialFixCreditClaimId = params.get("fix_credit_claim_id");
  const initialRemainingCredits = params.get("remaining_credits");
  const initialCreditPhase = params.get("credit_phase") || "daily";
  const selectedSkills = React.useMemo(() => {
    try { const value = JSON.parse(params.get("selected_skills") || "[]"); return Array.isArray(value) ? value : []; }
    catch { return []; }
  }, [params]);
  const selectedSections = React.useMemo(() => {
    try {
      const value = JSON.parse(params.get("selected_sections") || "{}");
      return { summary: value.summary !== false, skills: value.skills !== false, experience: value.experience !== false, projects: value.projects !== false };
    } catch { return { summary: true, skills: true, experience: true, projects: true }; }
  }, [params]);
  const [resume, setResume] = React.useState(null);
  const [guidance, setGuidance] = React.useState(null);
  const [saving, setSaving] = React.useState(false);
  const savingRef = React.useRef(false);
  const [result, setResult] = React.useState(null);
  const [claimWarning, setClaimWarning] = React.useState("");
  const [newlyAddedSkills, setNewlyAddedSkills] = React.useState([]);
  const [error, setError] = React.useState("");
  const [remainingCredits] = React.useState(initialRemainingCredits === null ? null : Number(initialRemainingCredits));
  const [generating, setGenerating] = React.useState(Boolean(params.get("selected_sections") || params.get("selected_skills")));
  const [confirmedSkills, setConfirmedSkills] = React.useState(selectedSkills);
  const [fixCreditClaimId, setFixCreditClaimId] = React.useState(initialFixCreditClaimId);
  // ContentEditable blur and button click can share one event turn. Keep the
  // current parsed list outside render state so Save never posts a stale array.
  const skillsDraftRef = React.useRef([]);
  const previous = score(params.get("previous_match_score"));

  React.useEffect(() => {
    if (!candidateId || !recommendationId) { setError("This resume editor needs a candidate and recommendation."); return; }
    axios.get(`${API}/candidate/${candidateId}/jobs/${recommendationId}/match-improvement`)
      .then(({ data }) => {
        const existing = list(data.resume?.skills);
        const mergedSkills = selectedSections.skills
          ? [...existing, ...selectedSkills.filter((skill) => !existing.some((item) => plain(item).trim().toLowerCase() === plain(skill).trim().toLowerCase()))]
          : existing;
        const draft = makeAtsDraft({ ...data.resume, skills: mergedSkills }, selectedSections);
        skillsDraftRef.current = mergedSkills;
        setResume(draft);
        const jdSkills = list(data.required_skills);
        const missingSkills = list(data.missing_skills).length
          ? list(data.missing_skills)
          : jdSkills.filter((skill) => !mergedSkills.some((item) => plain(item).trim().toLowerCase() === plain(skill).trim().toLowerCase()));
        setGuidance({ ...data, required_skills: jdSkills, missing_skills: selectedSections.skills ? missingSkills.filter((skill) => !selectedSkills.includes(skill)) : missingSkills });
        if (params.get("selected_sections") || params.get("selected_skills")) {
          window.setTimeout(() => setGenerating(false), 900);
        } else {
          setGenerating(false);
        }
      })
      .catch((e) => setError(e?.response?.data?.detail || "Unable to load your resume."));
  }, [candidateId, recommendationId, selectedSections, selectedSkills, params]);

  const update = (key, value) => setResume((old) => ({ ...old, [key]: value }));
  const addSuggestedSkill = (skill) => {
    const current = list(skillsDraftRef.current || resume?.skills);
    if (current.some((item) => plain(item).trim().toLowerCase() === plain(skill).trim().toLowerCase())) return;
    const next = [...current, skill];
    setConfirmedSkills((old) => old.some((item) => plain(item).trim().toLowerCase() === plain(skill).trim().toLowerCase()) ? old : [...old, skill]);
    skillsDraftRef.current = next;
    update("skills", next);
    setGuidance((old) => old ? { ...old, missing_skills: list(old.missing_skills).filter((item) => plain(item).trim().toLowerCase() !== plain(skill).trim().toLowerCase()) } : old);
  };
  const addSuggestedDetail = (detail) => {
    const text = plain(detail).trim();
    if (!text) return;
    const current = plain(resume?.bio).trim();
    if (current.toLowerCase().includes(text.toLowerCase())) return;
    update("bio", current ? `${current}\n\n[Confirm if applicable] ${text}` : `[Confirm if applicable] ${text}`);
  };
  const updateItem = (key, index, field, value) => update(key, list(resume[key]).map((item, i) => i === index ? (typeof item === "string" ? value : { ...item, [field]: value }) : item));
  const save = async () => {
    if (savingRef.current) return;
    savingRef.current = true;
    setSaving(true); setError(""); setClaimWarning("");
    try {
      // The first save consumes the charged claim. Every later save must use
      // a fresh, server-issued editor_repeat claim for this entitlement.
      let claimId = fixCreditClaimId;
      if (!claimId) {
        const { data: credit } = await axios.post(`${API}/candidate/${candidateId}/jobs/${recommendationId}/resume-fix-credit-claim`);
        claimId = credit.claim_id;
        setFixCreditClaimId(claimId);
      }
      const { data } = await axios.post(`${API}/candidate/${candidateId}/jobs/${recommendationId}/match-improvement`, { profile_updates: { ...resume, skills: skillsDraftRef.current }, fix_credit_claim_id: claimId, confirmed_skills: confirmedSkills });
      // The API re-reads candidates.skills after saving and returns that
      // canonical profile. Keep the document in sync with it so a combined
      // value entered here is immediately rendered as individual skills.
      const canonicalSkills = data?.profile?.keySkills ?? data?.profile?.skills;
      if (Array.isArray(canonicalSkills)) {
        skillsDraftRef.current = canonicalSkills;
        setResume((old) => ({ ...old, skills: canonicalSkills }));
      }
      if (Array.isArray(data?.remaining_missing_skills)) {
        setGuidance((old) => old ? { ...old, match_score: data.match_score, missing_skills: data.remaining_missing_skills, remaining_missing_skills: data.remaining_missing_skills, remaining_requirements: data.remaining_requirements } : old);
      }
      // The claim used above is consumed by the successful save. Do not leave
      // it available for a later attempt if rotation cannot issue a new one.
      setFixCreditClaimId(null);
      setResult(data);
      setNewlyAddedSkills(list(data?.newly_added_skills ?? data?.changes?.skills_added ?? data?.changed_skills));
      // The editor is opened in its own tab. Notify the dashboard tab to
      // discard its cached profile and load the persisted canonical payload.
      if (window.opener && data.profile) {
        window.opener.postMessage({ type: "eve:candidate-profile-updated", candidateId, profile: data.profile }, window.location.origin);
      }
      // Rotate before the next click; the claim just used is now consumed.
      try {
        const { data: nextCredit } = await axios.post(`${API}/candidate/${candidateId}/jobs/${recommendationId}/resume-fix-credit-claim`);
        if (!nextCredit?.claim_id) throw new Error("The next resume-fix claim was not issued.");
        setFixCreditClaimId(nextCredit.claim_id);
      } catch (rotationError) {
        setClaimWarning("Your changes were saved. Another save will require a new claim.");
      }
    } catch (e) {
      if (e?.response?.status === 403 && e.response.data?.detail?.code === "resume_fix_credits_insufficient") {
        window.opener?.postMessage({ type: "eve:resume-fix-credits-insufficient" }, window.location.origin);
      }
      setError(e?.response?.data?.detail?.message || e?.response?.data?.detail || "Could not save your resume.");
    }
    finally { savingRef.current = false; setSaving(false); }
  };
  if (error && !resume) return <main className="p-8 text-red-700">{error}</main>;
  if (!resume) return <main className="p-8 text-slate-600">Loading your resume…</main>;
  if (generating) return <main className="flex min-h-screen flex-col bg-[#FBFBF9] text-slate-900">
    <header className="border-b px-6 py-5"><p className="text-xl font-semibold">Generate Your Custom Resume</p></header>
    <div className="mx-auto flex w-full max-w-[720px] items-center px-6 py-7 text-sm"><span className="flex items-center gap-2"><b className="flex h-6 w-6 items-center justify-center rounded-full bg-[#70659A] text-white">1</b> See Your Difference</span><span className="mx-4 h-px flex-1 bg-[#70659A]"/><span className="flex items-center gap-2"><b className="flex h-6 w-6 items-center justify-center rounded-full bg-[#70659A] text-white">2</b> Align Your Resume</span><span className="mx-4 h-px flex-1 bg-black/10"/><span className="flex items-center gap-2 text-slate-500"><b className="flex h-6 w-6 items-center justify-center rounded-full bg-black/10">3</b> Review Your New Resume</span></div>
    <section className="m-auto w-full max-w-[700px] rounded-3xl border bg-white px-8 py-24 text-center shadow-sm"><div className="mx-auto h-1.5 w-72 overflow-hidden rounded-full bg-slate-100"><div className="h-full w-2/3 animate-pulse rounded-full bg-[#70659A]"/></div><h1 className="mt-10 text-xl font-semibold">Finalizing Your New Resume…</h1><p className="mt-8 text-sm text-slate-600">Creating a concise, ATS-friendly version using your confirmed information.</p></section>
  </main>;
  const oldScore = score(guidance?.match_score) ?? previous;
  const newScore = score(result?.match_score);
  const change = oldScore != null && newScore != null ? newScore - oldScore : null;
  const renderEntries = (key) => list(resume[key]).map((item, index) => {
    const text = sectionText(item); const titleField = typeof item === "string" ? "value" : (item.title != null ? "title" : item.role != null ? "role" : item.degree != null ? "degree" : item.name != null ? "name" : "company");
    const detailField = item?.description != null ? "description" : item?.responsibilities != null ? "responsibilities" : item?.details != null ? "details" : item?.institution != null ? "institution" : "description";
    return <div className="mb-4" key={`${key}-${index}`}><Editable value={text.title} onChange={(v) => updateItem(key, index, titleField, v)} className="font-semibold" multiline testId={`${key}-${index}-title`} />
      {typeof item !== "string" && <Editable value={text.detail} onChange={(v) => updateItem(key, index, detailField, v)} className="mt-1 whitespace-pre-wrap text-slate-700" multiline testId={`${key}-${index}-detail`} />}</div>;
  });
  const download = async () => {
    if (!result?.resume_download_url) return;
    const response = await axios.get(`${API}${result.resume_download_url}`, { responseType: "blob" });
    const url = URL.createObjectURL(response.data);
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = "updated-resume.pdf"; anchor.click();
    URL.revokeObjectURL(url);
  };
  return <main className="min-h-screen bg-slate-100 p-4 text-slate-900 md:p-8" data-testid="resume-editor-page">
    <header className="mx-auto mb-5 flex max-w-[1500px] items-center justify-between"><div><p className="text-lg font-semibold">Review Your New Resume</p><p className="text-xs text-slate-500">ATS-friendly draft for this job. Review every detail before saving.</p>{remainingCredits != null && <p data-testid="resume-fix-credit-balance" className="mt-1 text-xs font-medium text-slate-700">{remainingCredits} {initialCreditPhase === "starter" ? "starter" : "daily"} credits remaining</p>}</div><div className="flex items-center gap-3"><button onClick={save} disabled={saving} className="rounded-lg bg-[#70659A] px-5 py-2.5 text-sm font-medium text-white disabled:opacity-50">{saving ? "Saving…" : "Save Changes"}</button><button type="button" data-testid="resume-editor-back" onClick={() => window.history?.back?.()} className="rounded-lg bg-black/[.05] px-5 py-2.5 text-sm font-medium text-slate-700">Back</button></div></header>
    <div className="mx-auto grid max-w-[1500px] gap-6 lg:grid-cols-[360px_minmax(0,1fr)]">
      <article className="order-2 min-h-[1056px] bg-white px-8 py-12 shadow-lg md:px-16" data-testid="resume-document">
        <header className="border-b-2 border-slate-800 pb-5 text-center"><Editable value={resume.name} onChange={(v) => update("name", v)} className="text-3xl font-bold tracking-wide" testId="resume-name" /><Editable value={resume.headline} onChange={(v) => update("headline", v)} className="mt-1 text-lg text-slate-600" testId="resume-headline" /><Editable value={[resume.location, resume.email, resume.phone].filter(Boolean).join(" | ")} onChange={(v) => { const [location, email, phone] = v.split("|").map((x) => x.trim()); setResume((old) => ({ ...old, location, email, phone })); }} className="mt-2 text-sm text-slate-600" testId="resume-contact" /></header>
        <section className="mt-7"><h2 className="border-b text-sm font-bold tracking-[.18em]">PROFESSIONAL SUMMARY</h2><Editable value={resume.bio} onChange={(v) => update("bio", v)} className="mt-3 whitespace-pre-wrap leading-6" multiline testId="resume-summary" /></section>
        <section className="mt-7"><h2 className="border-b text-sm font-bold tracking-[.18em]">SKILLS</h2><div className="mt-3 flex flex-wrap gap-2" data-testid="new-skills-highlights">{newlyAddedSkills.map((skill) => <span key={skill} className="rounded bg-amber-100 px-2 py-1 text-sm ring-1 ring-amber-300">{skill} <small className="font-semibold text-amber-800">NEW</small></span>)}</div><Editable value={list(resume.skills).join(" \u2022 ")} onChange={(v) => { const skills = parseSkills(v); skillsDraftRef.current = skills; update("skills", skills); }} className="mt-2 leading-6" multiline testId="resume-skills" /></section>
        <section className="mt-7"><h2 className="border-b text-sm font-bold tracking-[.18em]">PROFESSIONAL EXPERIENCE</h2><div className="mt-3">{renderEntries("work_experience")}</div></section>
        {list(resume.projects).length > 0 && <section className="mt-7"><h2 className="border-b text-sm font-bold tracking-[.18em]">PROJECTS</h2><div className="mt-3">{renderEntries("projects")}</div></section>}
        <section className="mt-7"><h2 className="border-b text-sm font-bold tracking-[.18em]">EDUCATION</h2><div className="mt-3">{renderEntries("education")}</div></section>
        {list(resume.certifications).length > 0 && <section className="mt-7"><h2 className="border-b text-sm font-bold tracking-[.18em]">CERTIFICATIONS</h2><Editable value={list(resume.certifications).join(" • ")} onChange={(v) => update("certifications", v.split(/[,•\n]/).map((x) => x.trim()).filter(Boolean))} className="mt-3" multiline testId="resume-certifications" /></section>}
      </article>
      <aside className="order-1 space-y-4"><section className="rounded-xl bg-[#EEEAF8] p-5 shadow-sm"><p className="text-sm font-semibold">ATS-friendly draft created</p><p className="mt-4 text-xs font-semibold uppercase tracking-wide text-[#62578F]">What changed</p><ul className="mt-3 space-y-2 text-sm"><li>• Summary shortened to the strongest two sentences</li><li>• Selected missing skills added</li><li>• Experience converted into concise bullet points</li><li>• Project descriptions reduced to key evidence</li></ul><p className="mt-4 text-xs text-slate-600">Only your existing information was reorganized. Review before saving.</p></section><section className="rounded-xl bg-white p-5 shadow-sm"><p className="text-xs font-bold tracking-wide text-slate-500">JOB MATCH</p><p className="mt-2 text-3xl font-bold">{oldScore == null ? "—" : `${oldScore}%`}</p><p className="mt-5 text-sm font-medium">Missing skills from the job description:</p><div className="mt-2 flex flex-wrap gap-2">{list(guidance?.missing_skills).map((skill) => <button type="button" key={skill} onClick={() => addSuggestedSkill(skill)} className="rounded-full bg-slate-100 px-2.5 py-1 text-xs hover:bg-slate-200">+ {skill}</button>)}</div>{guidance?.experience_requirement ? <div className="mt-5 border-t pt-4"><p className="text-xs font-medium">Experience comparison</p><p className="mt-2 text-xs text-slate-600">Required: {guidance.experience_requirement}</p><p className="text-xs text-slate-600">Your resume: {resume.experience_years ? `${resume.experience_years} years` : "Not specified"}</p></div> : <p className="mt-5 text-xs text-slate-600">No specific experience requirement provided.</p>}</section>
        {result && <section className="rounded-xl bg-emerald-50 p-5" data-testid="resume-save-result"><p className="font-semibold text-emerald-900">Resume updated ✓</p><p className="mt-3 text-sm">Previous match: {oldScore == null ? "—" : `${oldScore}%`}</p><p className="text-sm">New match: <strong><MatchOdometer from={oldScore} to={newScore} /></strong></p><p className="text-sm">Improvement: {change == null ? "—" : `${change >= 0 ? "+" : ""}${change}%`}</p><button onClick={download} className="mt-4 w-full rounded-lg bg-white py-2 text-sm font-medium">Download Updated Resume</button><button onClick={() => { if (guidance?.job_url) window.open(guidance.job_url, "_blank", "noopener,noreferrer"); }} disabled={!guidance?.job_url} className="mt-2 w-full rounded-lg bg-slate-900 py-2 text-sm font-medium text-white disabled:opacity-50">Apply Now</button></section>}</aside>
    </div>{claimWarning && <p className="mx-auto mt-4 max-w-[1180px] text-sm text-amber-700" data-testid="resume-claim-warning">{claimWarning}</p>}{error && <p className="mx-auto mt-4 max-w-[1180px] text-sm text-red-700">{error}</p>}
  </main>;
}
