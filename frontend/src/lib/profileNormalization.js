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

const EXPERIENCE_MONTHS = new Map([
  ["jan", 0], ["january", 0],
  ["feb", 1], ["february", 1],
  ["mar", 2], ["march", 2],
  ["apr", 3], ["april", 3],
  ["may", 4],
  ["jun", 5], ["june", 5],
  ["jul", 6], ["july", 6],
  ["aug", 7], ["august", 7],
  ["sep", 8], ["sept", 8], ["september", 8],
  ["oct", 9], ["october", 9],
  ["nov", 10], ["november", 10],
  ["dec", 11], ["december", 11],
]);

const MS_PER_DAY = 24 * 60 * 60 * 1000;
const MS_PER_YEAR = 365.25 * MS_PER_DAY;

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
  const cleaned = text.replace(/\./g, "");

  const yearOnly = cleaned.match(/^(\d{4})$/);
  if (yearOnly) {
    const year = Number(yearOnly[1]);
    return role === "end"
      ? Date.UTC(year + 1, 0, 1)
      : Date.UTC(year, 0, 1);
  }

  const yearMonth = cleaned.match(/^(\d{4})[-/.](\d{1,2})$/);
  if (yearMonth) {
    const year = Number(yearMonth[1]);
    const month = Number(yearMonth[2]) - 1;
    if (role === "end") {
      return month === 11
        ? Date.UTC(year + 1, 0, 1)
        : Date.UTC(year, month + 1, 1);
    }
    return Date.UTC(year, month, 1);
  }

  const monthYear = cleaned.match(/^([A-Za-z]{3,9})\s+(\d{4})$/);
  if (monthYear) {
    const month = EXPERIENCE_MONTHS.get(monthYear[1].toLowerCase());
    if (month != null) {
      const year = Number(monthYear[2]);
      if (role === "end") {
        return month === 11
          ? Date.UTC(year + 1, 0, 1)
          : Date.UTC(year, month + 1, 1);
      }
      return Date.UTC(year, month, 1);
    }
  }

  const iso = cleaned.match(/^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$/);
  if (iso) {
    const year = Number(iso[1]);
    const month = Number(iso[2]) - 1;
    const day = Number(iso[3]);
    return Date.UTC(year, month, day);
  }

  const dayFirst = cleaned.match(/^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$/);
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

function splitExperienceDateRange(value) {
  const text = normalizeText(value);
  if (!text) return null;
  const parts = text.split(/\s+(?:[\u2013\u2014-]|â€”)\s+/, 2).map(normalizeText);
  return parts.length === 2 ? parts : null;
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
    const range = splitExperienceDateRange(datesText);
    if (range && range.length === 2) {
      const [left, right] = range;
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

function experienceKey(exp) {
  if (!exp || typeof exp !== "object") return "";
  return `${normalizeKey(exp.company)}|${normalizeKey(exp.title)}`;
}

function experienceCompletenessScore(exp) {
  if (!exp || typeof exp !== "object") return 0;

  const fields = [
    exp.id,
    exp.title,
    exp.company,
    exp.dates,
    exp.duration,
    exp.start_date,
    exp.startDate,
    exp.end_date,
    exp.endDate,
    exp.description,
    exp.summary,
    exp.location,
  ];

  return fields.reduce((score, value) => score + (normalizeText(value) ? 1 : 0), 0);
}

function mergeExperienceFields(target, source) {
  const merged = { ...target };
  const fillableFields = [
    "title",
    "company",
    "dates",
    "duration",
    "start_date",
    "startDate",
    "end_date",
    "endDate",
    "description",
    "summary",
    "location",
  ];

  fillableFields.forEach((field) => {
    if (!normalizeText(merged[field]) && normalizeText(source?.[field])) {
      merged[field] = normalizeText(source[field]);
    }
  });

  return merged;
}

function extractExperienceInterval(exp) {
  if (!exp || typeof exp !== "object") return null;

  let start = parseExperienceDate(exp.start_date ?? exp.startDate ?? null, "start");
  let end = parseExperienceDate(exp.end_date ?? exp.endDate ?? null, "end");
  let openEnded = isOpenEndedExperienceValue(exp.end_date ?? exp.endDate ?? null);
  const hasExplicitEndField = Object.prototype.hasOwnProperty.call(exp, "end_date") || Object.prototype.hasOwnProperty.call(exp, "endDate");
  if (!openEnded && hasExplicitEndField && !normalizeText(exp.end_date ?? exp.endDate ?? null)) {
    openEnded = true;
  }

  const datesText = normalizeText(exp.dates ?? exp.duration ?? "");
  if (datesText) {
    const range = splitExperienceDateRange(datesText);
    if (range && range.length === 2) {
      const [left, right] = range;
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

  if (!start) return null;

  const effectiveEnd = openEnded ? Date.now() : end;
  if (!effectiveEnd || effectiveEnd < start) return null;

  return [start, effectiveEnd];
}

export function calculateExperienceYears(experience) {
  if (!Array.isArray(experience) || experience.length === 0) return 0;

  const intervals = experience
    .map((exp) => extractExperienceInterval(exp))
    .filter(Boolean)
    .sort((a, b) => a[0] - b[0]);

  if (intervals.length === 0) return 0;

  let mergedMilliseconds = 0;
  let [currentStart, currentEnd] = intervals[0];

  for (let i = 1; i < intervals.length; i += 1) {
    const [start, end] = intervals[i];
    if (start <= currentEnd) {
      currentEnd = Math.max(currentEnd, end);
    } else {
      mergedMilliseconds += currentEnd - currentStart;
      currentStart = start;
      currentEnd = end;
    }
  }

  mergedMilliseconds += currentEnd - currentStart;
  return Math.max(0, mergedMilliseconds / MS_PER_YEAR);
}

function normalizeExperienceRecord(exp) {
  if (!exp || typeof exp !== "object") return exp;

  const normalized = { ...exp };
  [
    "title",
    "company",
    "dates",
    "duration",
    "start_date",
    "startDate",
    "end_date",
    "endDate",
    "description",
    "summary",
    "location",
  ].forEach((field) => {
    if (typeof normalized[field] === "string") {
      normalized[field] = normalizeText(normalized[field]);
    }
  });
  return normalized;
}

function dedupeExperienceForDisplay(experience) {
  if (!Array.isArray(experience)) return [];
  if (experience.length <= 1) return experience;

  const groups = new Map();
  experience.forEach((exp, index) => {
    if (!exp || typeof exp !== "object") return;
    const key = experienceKey(exp) || `__index__${index}`;
    const sortValues = extractExperienceSortValues(exp);
    const record = {
      exp,
      index,
      score: experienceCompletenessScore(exp),
      ...sortValues,
      primary: sortValues.openEnded ? (sortValues.start ?? 0) : ((sortValues.end ?? sortValues.start) ?? 0),
      secondary: sortValues.start ?? sortValues.end ?? 0,
    };
    const list = groups.get(key) || [];
    list.push(record);
    groups.set(key, list);
  });

  const compareRecords = (a, b) => {
    if (a.score !== b.score) return b.score - a.score;
    if (a.openEnded !== b.openEnded) return a.openEnded ? -1 : 1;
    if (a.primary !== b.primary) return (b.primary ?? 0) - (a.primary ?? 0);
    if (a.secondary !== b.secondary) return (b.secondary ?? 0) - (a.secondary ?? 0);
    return a.index - b.index;
  };

  const winners = Array.from(groups.values()).map((records) => {
    const sorted = [...records].sort(compareRecords);
    const winner = sorted[0];
    const merged = sorted.slice(1).reduce(
      (acc, record) => mergeExperienceFields(acc, record.exp),
      { ...winner.exp }
    );
    return {
      ...normalizeExperienceRecord(merged),
      _sortScore: winner.score,
      _sortOpenEnded: winner.openEnded,
      _sortPrimary: winner.primary,
      _sortSecondary: winner.secondary,
      _sortIndex: winner.index,
    };
  });

  return winners
    .sort((a, b) => {
      if (a._sortOpenEnded !== b._sortOpenEnded) return a._sortOpenEnded ? -1 : 1;
      if (a._sortPrimary !== b._sortPrimary) return (b._sortPrimary ?? 0) - (a._sortPrimary ?? 0);
      if (a._sortSecondary !== b._sortSecondary) return (b._sortSecondary ?? 0) - (a._sortSecondary ?? 0);
      return (a._sortIndex ?? 0) - (b._sortIndex ?? 0);
    })
    .map(({ _sortScore, _sortOpenEnded, _sortPrimary, _sortSecondary, _sortIndex, ...exp }) => exp);
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
  const experience = dedupeExperienceForDisplay(
    sortExperienceForDisplay(profile.experience ?? profile.work_experience ?? [])
  );
  const calculatedExperienceYears = calculateExperienceYears(experience);

  return {
    ...profile,
    keySkills,
    certifications,
    experience,
    calculatedExperienceYears,
  };
}
