import { normalizeProfileForDisplay } from "./profileNormalization";

function sentence(value) {
  const text = String(value || "").replace(/\s+/g, " ").trim().replace(/[.!?]+$/, "");
  return text ? `${text}.` : "";
}

function readableYears(value) {
  const years = Number(value);
  if (!Number.isFinite(years) || years <= 0) return "";
  const rounded = Math.round(years * 10) / 10;
  return `${Number.isInteger(rounded) ? rounded : rounded.toFixed(1)} year${rounded === 1 ? "" : "s"}`;
}

function articleFor(value) {
  return /^[aeiou]/i.test(String(value || "").trim()) ? "an" : "a";
}

function isVoiceIntakeComplete(profile) {
  const status = profile?.voice_intake_resume?.status || profile?.raw_data?.voice_intake?.status;
  return String(status || "").toLowerCase() === "completed";
}

/**
 * A compact professional narrative shared by Profile and the onboarding recap.
 * It intentionally reads only professional overview fields: no skills,
 * education, or certification data is used here.
 */
export function buildCandidateNarrative(profile) {
  if (!profile) return "";

  const candidate = normalizeProfileForDisplay(profile);
  const completed = isVoiceIntakeComplete(candidate);
  const name = String(candidate.name || "").trim();
  const latest = candidate.experience?.[0] || {};
  const previous = candidate.experience?.[1] || {};
  const role = String(candidate.headline || candidate.current_role || latest.title || "").trim();
  const company = String(candidate.current_company || latest.company || "").trim();
  const years = readableYears(candidate.experience_years || candidate.calculatedExperienceYears);
  const targetRoles = (candidate.preferred_roles || []).filter(Boolean).slice(0, 3);
  const subject = name || "This candidate";
  const sentences = [];

  if (role) {
    sentences.push(sentence(`${subject} is ${articleFor(role)} ${role} professional`));
  } else {
    sentences.push(sentence(`${subject} is building a professional profile`));
  }

  if (years) {
    sentences.push(sentence(`${subject} brings ${years} of professional experience`));
  } else if (previous.title) {
    sentences.push(sentence(`${subject}'s background includes work as a ${previous.title}`));
  } else if (latest.title && latest.title !== role) {
    sentences.push(sentence(`${subject}'s background includes ${latest.title} experience`));
  } else {
    sentences.push(sentence(`${subject}'s professional background is captured in their saved profile`));
  }

  if (role && company) {
    sentences.push(sentence(`${subject} currently works as ${role} at ${company}`));
  } else if (latest.title) {
    sentences.push(sentence(`${subject}'s most recent role is ${latest.title}${latest.company ? ` at ${latest.company}` : ""}`));
  } else if (targetRoles.length) {
    sentences.push(sentence(`${subject} is focused on the next step in their career`));
  }

  if (targetRoles.length) {
    sentences.push(sentence(`${subject} is targeting ${targetRoles.join(", ")} roles`));
  } else if (role) {
    sentences.push(sentence(`${subject} is open to opportunities aligned with their professional background`));
  }

  // Do not reuse free-form intake text here: it can contain skills,
  // education, or certifications. Keep the final sentence to career intent.
  if (targetRoles.length) {
    sentences.push(sentence(`${subject} is looking for a role that supports continued career growth`));
  } else if (role) {
    sentences.push(sentence(`${subject} is interested in making a meaningful contribution in their next role`));
  }

  const desiredLength = completed ? 4 : 3;
  while (sentences.length < desiredLength) {
    sentences.push(sentence(`${subject} is shaping the next stage of their professional career`));
  }
  return sentences.slice(0, completed ? 5 : 3).join(" ");
}

export function candidateNarrativeLineCount(profile) {
  return isVoiceIntakeComplete(profile) ? 5 : 3;
}
