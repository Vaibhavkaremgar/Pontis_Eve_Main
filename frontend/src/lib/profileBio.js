import { formatExperienceDuration, normalizeProfileForDisplay } from "./profileNormalization";

function text(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function key(value) {
  return text(value).toLocaleLowerCase();
}

function join(items) {
  if (items.length < 2) return items[0] || "";
  if (items.length === 2) return `${items[0]} and ${items[1]}`;
  return `${items.slice(0, -1).join(", ")}, and ${items.at(-1)}`;
}

function uniqueValues(values, limit) {
  const seen = new Set();
  return values.reduce((result, value) => {
    const cleaned = text(value);
    const normalized = key(cleaned);
    if (!cleaned || seen.has(normalized) || result.length >= limit) return result;
    seen.add(normalized);
    result.push(cleaned);
    return result;
  }, []);
}

function roleAtCompany(role, company) {
  if (role && company) return `${role} at ${company}`;
  return role || company;
}

function articleFor(value) {
  return /^[aeiou]/i.test(value) ? "an" : "a";
}

/**
 * Builds the Profile Bio solely from the profile currently supplied by the
 * persisted-profile view. It is intentionally a fresh derivation: no saved
 * summary/bio text is reused, so Voice Intake and chat profile updates cannot
 * leave stale prose on the page.
 */
export function buildProfileBio(profile) {
  if (!profile || typeof profile !== "object") return "";

  const candidate = normalizeProfileForDisplay(profile);
  const latest = candidate.experience?.[0] || {};
  const role = text(candidate.current_role || candidate.headline || latest.title);
  const company = text(candidate.current_company || latest.company);
  const experienceDuration = formatExperienceDuration(candidate.experience_years);
  const skills = uniqueValues(candidate.keySkills || candidate.skills || [], 4);
  const focus = uniqueValues(candidate.preferred_roles || [], 3)
    .filter((item) => key(item) !== key(role));
  const currentJob = roleAtCompany(role, company);
  const priorJob = (candidate.experience || [])
    .map((entry) => roleAtCompany(text(entry.title), text(entry.company)))
    .find((job) => job && key(job) !== key(currentJob));
  const sentences = [];

  if (currentJob && experienceDuration) {
    sentences.push(role
      ? `Currently ${articleFor(role)} ${currentJob}, with ${experienceDuration} of professional experience.`
      : `Currently at ${company}, with ${experienceDuration} of professional experience.`);
  } else if (currentJob && role) {
    sentences.push(`Currently ${articleFor(role)} ${currentJob}.`);
  } else if (currentJob) {
    sentences.push(`Currently at ${company}.`);
  } else if (experienceDuration) {
    sentences.push(`Professional with ${experienceDuration} of experience.`);
  }

  if (priorJob && skills.length) {
    sentences.push(`Previous experience includes ${priorJob}, with strengths in ${join(skills)}.`);
  } else if (priorJob) {
    sentences.push(`Previous experience includes ${priorJob}.`);
  } else if (skills.length) {
    sentences.push(`Core strengths include ${join(skills)}.`);
  }

  if (focus.length) {
    sentences.push(`Career focus is ${join(focus)} roles.`);
  }

  return sentences.slice(0, 3).join(" ");
}
