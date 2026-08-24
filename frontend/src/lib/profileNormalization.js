const CERTIFICATION_BOILERPLATE_WORDS = new Set([
  "cert",
  "certificate",
  "certificates",
  "certification",
  "certifications",
  "certified",
  "course",
  "courses",
  "credential",
  "credentials",
  "training",
]);

const EXPERIENCE_OPEN_ENDED_MARKERS = new Set([
  "present",
  "current",
  "ongoing",
  "now",
]);

function normalizeText(value) {
  if (value === null || value === undefined) return "";
  return String(value).replace(/\s+/g, " ").trim();
}

function normalizeKey(value) {
  return normalizeText(value).toLowerCase();
}

function relaxedCertificationKey(value) {
  const stripped = normalizeKey(value).replace(/[^\w\s]+/g, " ");
  const tokens = stripped.split(/\s+/).filter(Boolean).filter((token) => !CERTIFICATION_BOILERPLATE_WORDS.has(token));
  return tokens.join(" ") || stripped.trim();
}

function looksLikeCertification(value) {
  return /\b(?:cert|certificate|certificates|certification|certifications|certified|course|courses|credential|credentials|training|license|licence)\b/i.test(normalizeKey(value));
}

function isOpenEndedExperienceValue(value) {
  const normalized = normalizeKey(value);
  if (!normalized) return false;
  if (EXPERIENCE_OPEN_ENDED_MARKERS.has(normalized)) return true;
  return /\b(?:present|current|ongoing|now)\b/i.test(normalized);
}

function parseExperienceDate(value, role = "end") {
  const text = normalizeText(value);
  if (!text || isOpenEndedExperienceValue(text)) return null;

  const yearOnly = text.match(/^(\d{4})$/);
  if (yearOnly) {
    const year = Number(yearOnly[1]);
    return Date.UTC(year, role === "end" ? 11 : 0, role === "end" ? 31 : 1);
  }

  const iso = text.match(/^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$/);
  if (iso) {
    const year = Number(iso[1]);
    const month = Number(iso[2]) - 1;
    const day = Number(iso[3]);
    return Date.UTC(year, month, day);
  }

  const dayFirst = text.match(/^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$/);
  if (dayFirst) {
    const first = Number(dayFirst[1]);
    const second = Number(dayFirst[2]);
    const year = Number(dayFirst[3]);

    const day = first;
    const month = second - 1;
    return Date.UTC(year, month, day);
  }

  return null;
}

function extractExperienceSortValues(exp) {
  if (!exp || typeof exp !== "object") {
    return { openEnded: false, start: null, end: null };
  }

  const rawStart = exp.start_date ?? exp.startDate ?? null;
  const rawEnd = exp.end_date ?? exp.endDate ?? null;
  const hasExplicitEndField = Object.prototype.hasOwnProperty.call(exp, "end_date") || Object.prototype.hasOwnProperty.call(exp, "endDate");

  let start = parseExperienceDate(rawStart, "start");
  let end = parseExperienceDate(rawEnd, "end");
  let openEnded = isOpenEndedExperienceValue(rawEnd);
  if (!openEnded && hasExplicitEndField && !normalizeText(rawEnd)) {
    openEnded = true;
  }

  const datesText = normalizeText(exp.dates ?? exp.duration ?? "");
  if (datesText) {
    const separatorMatch = datesText.match(/\s+[\u2013\u2014-]\s+/);
    if (separatorMatch) {
      const [left, right] = datesText.split(separatorMatch[0], 2).map(normalizeText);
      if (!start) start = parseExperienceDate(left, "start");
      if (right) {
        if (isOpenEndedExperienceValue(right)) {
          openEnded = true;
          end = null;
        } else if (!end) {
          end = parseExperienceDate(right, "end");
        }
      }
    } else {
      if (!start) start = parseExperienceDate(datesText, "start");
      if (!end && !openEnded) end = parseExperienceDate(datesText, "end");
      if (isOpenEndedExperienceValue(datesText)) openEnded = true;
    }
  }

  return { openEnded, start, end };
}

function sortExperienceForDisplay(experience) {
  if (!Array.isArray(experience)) return [];

  return experience
    .map((exp, index) => {
      const { openEnded, start, end } = extractExperienceSortValues(exp);
      return {
        exp,
        index,
        openEnded,
        start,
        end,
      };
    })
    .sort((a, b) => {
      if (a.openEnded !== b.openEnded) return a.openEnded ? -1 : 1;
      const primaryA = a.openEnded ? a.start : (a.end ?? a.start);
      const primaryB = b.openEnded ? b.start : (b.end ?? b.start);
      if (primaryA !== primaryB) return primaryB - primaryA;
      const secondaryA = a.start ?? a.end;
      const secondaryB = b.start ?? b.end;
      if (secondaryA !== secondaryB) return (secondaryB ?? 0) - (secondaryA ?? 0);
      return a.index - b.index;
    })
    .map(({ exp }) => exp);
}

function normalizeCertifications(certifications) {
  if (!Array.isArray(certifications)) return [];

  const normalized = [];
  const seenStrict = new Set();
  const seenRelaxed = new Set();

  certifications.forEach((cert) => {
    const cleaned = normalizeText(cert);
    if (!cleaned) return;

    const strictKey = normalizeKey(cleaned);
    const relaxedKey = relaxedCertificationKey(cleaned);
    if (seenStrict.has(strictKey) || seenRelaxed.has(relaxedKey)) return;

    seenStrict.add(strictKey);
    seenRelaxed.add(relaxedKey);
    normalized.push(cleaned);
  });

  return normalized;
}

function skillShouldBeFiltered(skillText, certText) {
  const skillKey = normalizeKey(skillText);
  const certKey = normalizeKey(certText);
  if (skillKey === certKey) return true;

  const skillRelaxed = relaxedCertificationKey(skillText);
  const certRelaxed = relaxedCertificationKey(certText);
  if (skillRelaxed !== certRelaxed) return false;

  return certRelaxed.split(/\s+/).filter(Boolean).length >= 2;
}

function normalizeSkills(skills, certifications = []) {
  if (!Array.isArray(skills)) return [];

  const normalizedCerts = normalizeCertifications(certifications);
  const normalized = [];
  const seen = new Set();

  skills.forEach((skill) => {
    const cleaned = normalizeText(typeof skill === "object" && skill ? skill.name : skill);
    if (!cleaned) return;

    const key = normalizeKey(cleaned);
    if (seen.has(key)) return;

    const shouldFilter = normalizedCerts.some(
      (cert) =>
        skillShouldBeFiltered(cleaned, cert) &&
        (looksLikeCertification(cleaned) || looksLikeCertification(cert))
    );
    if (shouldFilter) return;

    seen.add(key);
    normalized.push(cleaned);
  });

  return normalized;
}

export function normalizeProfileForDisplay(profile = {}) {
  const certifications = normalizeCertifications(profile.certifications ?? []);
  const keySkills = normalizeSkills(profile.keySkills ?? profile.skills ?? [], certifications);
  const experience = sortExperienceForDisplay(profile.experience ?? profile.work_experience ?? []);

  return {
    ...profile,
    keySkills,
    certifications,
    experience,
  };
}
