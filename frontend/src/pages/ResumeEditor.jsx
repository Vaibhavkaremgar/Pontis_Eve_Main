import React from "react";
import axios from "axios";
import { useSearchParams } from "react-router-dom";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const plain = (value) => (value == null ? "" : String(value));
const list = (value) => Array.isArray(value) ? value : [];
const score = (value) => value == null || !Number.isFinite(Number(value)) ? null : Math.round(Number(value) * (Number(value) <= 1 ? 100 : 1));

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
  const [resume, setResume] = React.useState(null);
  const [guidance, setGuidance] = React.useState(null);
  const [saving, setSaving] = React.useState(false);
  const [result, setResult] = React.useState(null);
  const [error, setError] = React.useState("");
  const previous = score(params.get("previous_match_score"));

  React.useEffect(() => {
    if (!candidateId || !recommendationId) { setError("This resume editor needs a candidate and recommendation."); return; }
    axios.get(`${API}/candidate/${candidateId}/jobs/${recommendationId}/match-improvement`)
      .then(({ data }) => { setResume(data.resume); setGuidance(data); })
      .catch((e) => setError(e?.response?.data?.detail || "Unable to load your resume."));
  }, [candidateId, recommendationId]);

  const update = (key, value) => setResume((old) => ({ ...old, [key]: value }));
  const updateItem = (key, index, field, value) => update(key, list(resume[key]).map((item, i) => i === index ? (typeof item === "string" ? value : { ...item, [field]: value }) : item));
  const save = async () => {
    setSaving(true); setError("");
    try {
      const { data } = await axios.post(`${API}/candidate/${candidateId}/jobs/${recommendationId}/match-improvement`, { profile_updates: resume });
      setResult(data);
      // The editor is opened in its own tab. Notify the dashboard tab to
      // discard its cached profile and load the persisted canonical payload.
      if (window.opener && data.profile) {
        window.opener.postMessage({ type: "eve:candidate-profile-updated", candidateId, profile: data.profile }, window.location.origin);
      }
    } catch (e) { setError(e?.response?.data?.detail || "Could not save your resume."); }
    finally { setSaving(false); }
  };
  if (error && !resume) return <main className="p-8 text-red-700">{error}</main>;
  if (!resume) return <main className="p-8 text-slate-600">Loading your resume…</main>;
  const oldScore = score(guidance?.match_score) ?? previous;
  const newScore = score(result?.match_score);
  const change = oldScore != null && newScore != null ? newScore - oldScore : null;
  const renderEntries = (key) => list(resume[key]).map((item, index) => {
    const text = sectionText(item); const titleField = typeof item === "string" ? "value" : (item.title != null ? "title" : item.role != null ? "role" : item.degree != null ? "degree" : item.name != null ? "name" : "company");
    const detailField = item?.description != null ? "description" : item?.responsibilities != null ? "responsibilities" : item?.details != null ? "details" : item?.institution != null ? "institution" : "description";
    return <div className="mb-4" key={`${key}-${index}`}><Editable value={text.title} onChange={(v) => updateItem(key, index, titleField, v)} className="font-semibold" multiline testId={`${key}-${index}-title`} />
      {typeof item !== "string" && <Editable value={text.detail} onChange={(v) => updateItem(key, index, detailField, v)} className="mt-1 whitespace-pre-wrap text-slate-700" multiline testId={`${key}-${index}-detail`} />}</div>;
  });
  return <main className="min-h-screen bg-slate-100 p-4 text-slate-900 md:p-8" data-testid="resume-editor-page">
    <header className="mx-auto mb-5 flex max-w-[1180px] items-center justify-between"><div><p className="text-sm font-semibold">Fix My Resume</p><p className="text-xs text-slate-500">Edit the document directly, then save to update your Eve profile.</p></div><button onClick={save} disabled={saving} className="rounded-lg bg-slate-900 px-5 py-2.5 text-sm font-medium text-white disabled:opacity-50">{saving ? "Saving…" : "Save Changes"}</button></header>
    <div className="mx-auto grid max-w-[1180px] gap-6 lg:grid-cols-[minmax(0,820px)_280px]">
      <article className="min-h-[1056px] bg-white px-8 py-12 shadow-lg md:px-16" data-testid="resume-document">
        <header className="border-b-2 border-slate-800 pb-5 text-center"><Editable value={resume.name} onChange={(v) => update("name", v)} className="text-3xl font-bold tracking-wide" testId="resume-name" /><Editable value={resume.headline} onChange={(v) => update("headline", v)} className="mt-1 text-lg text-slate-600" testId="resume-headline" /><Editable value={[resume.location, resume.email, resume.phone].filter(Boolean).join(" | ")} onChange={(v) => { const [location, email, phone] = v.split("|").map((x) => x.trim()); setResume((old) => ({ ...old, location, email, phone })); }} className="mt-2 text-sm text-slate-600" testId="resume-contact" /></header>
        <section className="mt-7"><h2 className="border-b text-sm font-bold tracking-[.18em]">PROFESSIONAL SUMMARY</h2><Editable value={resume.bio} onChange={(v) => update("bio", v)} className="mt-3 whitespace-pre-wrap leading-6" multiline testId="resume-summary" /></section>
        <section className="mt-7"><h2 className="border-b text-sm font-bold tracking-[.18em]">SKILLS</h2><Editable value={list(resume.skills).join(" \u2022 ")} onChange={(v) => update("skills", v.split(/[,;|\n\u2022]+/).map((x) => x.trim()).filter(Boolean))} className="mt-3 leading-6" multiline testId="resume-skills" /></section>
        <section className="mt-7"><h2 className="border-b text-sm font-bold tracking-[.18em]">PROFESSIONAL EXPERIENCE</h2><div className="mt-3">{renderEntries("work_experience")}</div></section>
        {list(resume.projects).length > 0 && <section className="mt-7"><h2 className="border-b text-sm font-bold tracking-[.18em]">PROJECTS</h2><div className="mt-3">{renderEntries("projects")}</div></section>}
        <section className="mt-7"><h2 className="border-b text-sm font-bold tracking-[.18em]">EDUCATION</h2><div className="mt-3">{renderEntries("education")}</div></section>
        {list(resume.certifications).length > 0 && <section className="mt-7"><h2 className="border-b text-sm font-bold tracking-[.18em]">CERTIFICATIONS</h2><Editable value={list(resume.certifications).join(" • ")} onChange={(v) => update("certifications", v.split(/[,•\n]/).map((x) => x.trim()).filter(Boolean))} className="mt-3" multiline testId="resume-certifications" /></section>}
      </article>
      <aside className="space-y-4"><section className="rounded-xl bg-white p-5 shadow-sm"><p className="text-xs font-bold tracking-wide text-slate-500">JOB MATCH</p><p className="mt-2 text-3xl font-bold">{oldScore == null ? "—" : `${oldScore}%`}</p><p className="mt-5 text-sm font-medium">Consider adding if you genuinely have them:</p><div className="mt-2 flex flex-wrap gap-2">{list(guidance?.missing_skills).map((skill) => <span key={skill} className="rounded-full bg-slate-100 px-2.5 py-1 text-xs">{skill}</span>)}</div>{!guidance?.experience_requirement && <p className="mt-4 text-xs text-slate-600">No specific experience requirement provided.</p>}</section>
        {result && <section className="rounded-xl bg-emerald-50 p-5" data-testid="resume-save-result"><p className="font-semibold text-emerald-900">Resume updated ✓</p><p className="mt-3 text-sm">Previous match: {oldScore == null ? "—" : `${oldScore}%`}</p><p className="text-sm">New match: <strong><MatchOdometer from={oldScore} to={newScore} /></strong></p><p className="text-sm">Improvement: {change == null ? "—" : `${change >= 0 ? "+" : ""}${change}%`}</p><button onClick={() => window.open(`${API}${result.resume_download_url}`, "_blank", "noopener,noreferrer")} className="mt-4 w-full rounded-lg bg-white py-2 text-sm font-medium">Download Updated Resume</button><button onClick={() => { if (guidance?.job_url) window.open(guidance.job_url, "_blank", "noopener,noreferrer"); }} disabled={!guidance?.job_url} className="mt-2 w-full rounded-lg bg-slate-900 py-2 text-sm font-medium text-white disabled:opacity-50">Apply Now</button></section>}</aside>
    </div>{error && <p className="mx-auto mt-4 max-w-[1180px] text-sm text-red-700">{error}</p>}
  </main>;
}
