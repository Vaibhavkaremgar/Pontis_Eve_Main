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

const MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const IMMEDIATE_JOINER_PATTERN = /\b(?:immediate\s+joiner|i(?:'m| am)?\s+an?\s+immediate\s+joiner)\b/i;

function normalizeText(value) {
  if (value === null || value === undefined) return "";
  return String(value).replace(/\s+/g, " ").trim();
}

// Profile experience_years is the persisted source of truth. Keep its display
// normalization in one place so the header and Bio cannot diverge.
export function formatExperienceYears(value) {
  const years = Number(value);
  if (!Number.isFinite(years) || years < 0) return "";
  const rounded = Math.round(years * 10) / 10;
  return Number.isInteger(rounded) ? String(Math.trunc(rounded)) : rounded.toFixed(1);
}

// Work history is stored and displayed at month precision.  Converting the
// calculated value to months here keeps the profile header and bio readable
// without losing a partial year to decimal rounding.
export function formatExperienceDuration(value) {
  const years = Number(value);
  if (!Number.isFinite(years) || years < 0) return "";

  const totalMonths = Math.round(years * 12);
  if (totalMonths <= 0) return "";
  const wholeYears = Math.floor(totalMonths / 12);
  const months = totalMonths % 12;
  const parts = [];
  if (wholeYears) parts.push(`${wholeYears} year${wholeYears === 1 ? "" : "s"}`);
  if (months) parts.push(`${months} month${months === 1 ? "" : "s"}`);
  return parts.join(" ") || "0 months";
}

function normalizeKey(value) {
  return normalizeText(value).toLowerCase();
}

function normalizeAvailabilityValue(value) {
  const cleaned = normalizeText(value);
  if (!cleaned) return "";
  if (IMMEDIATE_JOINER_PATTERN.test(cleaned)) return "Immediately";
  return cleaned;
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

function formatExperienceDateLabel(value) {
  const text = normalizeText(value);
  if (!text) return "";
  if (isOpenEndedExperienceValue(text)) return "Present";

  const yearOnly = text.match(/^(\d{4})$/);
  if (yearOnly) return yearOnly[1];

  const yearMonth = text.match(/^(\d{4})[-/.](\d{1,2})$/);
  if (yearMonth) return yearMonth[1];

  const monthYear = text.match(/^([A-Za-z]{3,9})\s+(\d{4})$/);
  if (monthYear && EXPERIENCE_MONTHS.has(monthYear[1].toLowerCase())) return monthYear[2];

  const iso = text.match(/^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$/);
  if (iso) return iso[1];

  const dayFirst = text.match(/^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$/);
  if (dayFirst) return dayFirst[3];

  return text;
}

function formatExperienceDateRange(exp) {
  if (!exp || typeof exp !== "object") return "";

  const rawStart = exp.start_date ?? exp.startDate ?? null;
  const rawEnd = exp.end_date ?? exp.endDate ?? null;
  const rawDates = normalizeText(exp.dates ?? exp.duration ?? "");

  let start = rawStart;
  let end = rawEnd;

  if (rawDates) {
    const range = splitExperienceDateRange(rawDates);
    if (range && range.length === 2) {
      if (!normalizeText(start)) start = range[0];
      if (!normalizeText(end)) end = range[1];
    } else if (!normalizeText(start)) {
      start = rawDates;
    }
  }

  const startLabel = formatExperienceDateLabel(start);
  // No valid start date — if end is open-ended show just "Present", else nothing
  if (!startLabel || startLabel === "Present") {
    const endLabel = formatExperienceDateLabel(end);
    if (endLabel === "Present" || isOpenEndedExperienceValue(normalizeText(start))) return "Present";
    return rawDates;
  }

  const endLabel = formatExperienceDateLabel(end);
  if (!endLabel || endLabel === "Present") {
    return `${startLabel} — Present`;
  }
  return `${startLabel} — ${endLabel}`;
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

const EXPERIENCE_TITLE_STOPWORDS = new Set(["and", "for", "in", "of", "on", "the", "to", "with"]);

function experienceTextTokens(value) {
  return new Set(
    (normalizeKey(value).match(/[a-z0-9+#.]+/g) || []).filter(
      (token) => token && !EXPERIENCE_TITLE_STOPWORDS.has(token)
    )
  );
}

function experienceTextKey(value) {
  return normalizeKey(value).replace(/[^\w\s+#.]+/g, " ");
}

function experienceTextMatches(existing, next) {
  const existingText = normalizeText(existing);
  const nextText = normalizeText(next);
  if (!existingText || !nextText) return false;

  const existingKey = experienceTextKey(existingText);
  const nextKey = experienceTextKey(nextText);
  if (existingKey === nextKey) return true;
  if (existingKey.includes(nextKey) || nextKey.includes(existingKey)) return true;

  const existingTokens = experienceTextTokens(existingText);
  const nextTokens = experienceTextTokens(nextText);
  if (!existingTokens.size || !nextTokens.size) return false;

  let overlap = 0;
  existingTokens.forEach((token) => {
    if (nextTokens.has(token)) overlap += 1;
  });

  const smallest = Math.min(existingTokens.size, nextTokens.size);
  const largest = Math.max(existingTokens.size, nextTokens.size);
  return (overlap >= smallest && overlap >= 2) || (overlap >= 2 && overlap / largest >= 0.66);
}

function extractExperienceInterval(exp) {
  if (!exp || typeof exp !== "object") return null;

  const { start, end, openEnded } = parseExperienceWindow(exp);
  if (start === null) return null;

  const effectiveEnd = openEnded ? Date.now() : end;
  if (!effectiveEnd || effectiveEnd < start) return null;

  return [start, effectiveEnd];
}

function parseExperienceWindow(exp) {
  if (!exp || typeof exp !== "object") {
    return { start: null, end: null, openEnded: false };
  }

  let start = parseExperienceDate(exp.start_date ?? exp.startDate ?? null, "start");
  let end = parseExperienceDate(exp.end_date ?? exp.endDate ?? null, "end");
  let openEnded = isOpenEndedExperienceValue(exp.end_date ?? exp.endDate ?? null);
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

  return { start, end, openEnded };
}

function experienceEntriesCompatible(existing, next) {
  const existingTitle = normalizeText(existing?.title ?? existing?.role ?? "");
  const nextTitle = normalizeText(next?.title ?? next?.role ?? "");
  const existingCompany = normalizeText(existing?.company ?? existing?.company_name ?? "");
  const nextCompany = normalizeText(next?.company ?? next?.company_name ?? "");

  if (existingCompany && nextCompany && !experienceTextMatches(existingCompany, nextCompany)) {
    return false;
  }
  if (existingTitle && nextTitle && !experienceTextMatches(existingTitle, nextTitle)) {
    return false;
  }

  const existingWindow = parseExperienceWindow(existing);
  const nextWindow = parseExperienceWindow(next);
  const existingHasDates = existingWindow.start !== null || existingWindow.end !== null || existingWindow.openEnded;
  const nextHasDates = nextWindow.start !== null || nextWindow.end !== null || nextWindow.openEnded;

  if (!existingHasDates || !nextHasDates) {
    return true;
  }

  if (existingWindow.start !== null && nextWindow.start !== null && existingWindow.start !== nextWindow.start) {
    return false;
  }
  if (existingWindow.end !== null && nextWindow.end !== null && existingWindow.end !== nextWindow.end) {
    return false;
  }
  if (
    existingWindow.start !== null &&
    existingWindow.end !== null &&
    nextWindow.start !== null &&
    nextWindow.end !== null
  ) {
    return !(existingWindow.end < nextWindow.start || nextWindow.end < existingWindow.start);
  }

  return true;
}

function experienceMatchScore(existing, next) {
  if (!experienceEntriesCompatible(existing, next)) return -1;

  let score = 0;
  const existingTitle = normalizeText(existing?.title ?? existing?.role ?? "");
  const nextTitle = normalizeText(next?.title ?? next?.role ?? "");
  const existingCompany = normalizeText(existing?.company ?? existing?.company_name ?? "");
  const nextCompany = normalizeText(next?.company ?? next?.company_name ?? "");

  if (existingCompany && nextCompany) {
    if (experienceTextMatches(existingCompany, nextCompany)) {
      score += 5;
    } else if (existingCompany === nextCompany) {
      score += 6;
    }
  }

  if (existingTitle && nextTitle) {
    if (experienceTextMatches(existingTitle, nextTitle)) {
      score += 5;
    } else if (existingTitle === nextTitle) {
      score += 6;
    }
  }

  const existingWindow = parseExperienceWindow(existing);
  const nextWindow = parseExperienceWindow(next);
  if (existingWindow.start !== null && nextWindow.start !== null) {
    score += existingWindow.start === nextWindow.start ? 3 : 1;
  }
  if (existingWindow.end !== null && nextWindow.end !== null) {
    score += existingWindow.end === nextWindow.end ? 2 : 1;
  }
  if (existingWindow.openEnded && nextWindow.openEnded) {
    score += 2;
  }
  if (
    (existingWindow.start !== null || existingWindow.end !== null || existingWindow.openEnded) &&
    (nextWindow.start !== null || nextWindow.end !== null || nextWindow.openEnded)
  ) {
    score += 1;
  }

  return score;
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
  const copyIfEmpty = (field) => {
    if (!normalizeText(merged[field]) && normalizeText(source?.[field])) {
      merged[field] = normalizeText(source[field]);
    }
  };

  copyIfEmpty("location");

  ["title", "company"].forEach((field) => {
    const existingValue = normalizeText(merged[field]);
    const nextValue = normalizeText(source?.[field]);
    if (!existingValue && nextValue) {
      merged[field] = nextValue;
      return;
    }
    if (
      existingValue &&
      nextValue &&
      experienceTextMatches(existingValue, nextValue) &&
      nextValue.length > existingValue.length &&
      nextValue.toLowerCase().includes(existingValue.toLowerCase())
    ) {
      merged[field] = nextValue;
    }
  });

  ["start_date", "startDate"].forEach((field) => {
    if (normalizeText(merged[field])) return;
    if (parseExperienceDate(source?.[field], "start") !== null) {
      merged[field] = normalizeText(source[field]);
    }
  });

  ["end_date", "endDate"].forEach((field) => {
    const existingValue = normalizeText(merged[field]);
    const nextValue = normalizeText(source?.[field]);
    if (!nextValue) return;
    if (!existingValue) {
      if (parseExperienceDate(source?.[field], "end") !== null || isOpenEndedExperienceValue(source?.[field])) {
        merged[field] = nextValue;
      }
      return;
    }
    const existingIsPresent = isOpenEndedExperienceValue(existingValue);
    const nextIsPresent = isOpenEndedExperienceValue(nextValue);
    if (existingIsPresent && parseExperienceDate(source?.[field], "end") !== null) {
      merged[field] = nextValue;
    } else if (!existingIsPresent && !nextIsPresent) {
      return;
    }
  });

  ["dates", "duration"].forEach((field) => {
    if (!normalizeText(merged[field])) {
      const nextValue = normalizeText(source?.[field]);
      if (!nextValue) return;
      const window = parseExperienceWindow(source);
      if (window.start !== null || window.end !== null || window.openEnded || nextValue) {
        merged[field] = nextValue;
      }
    }
  });

  ["description", "summary"].forEach((field) => {
    const existingValue = normalizeText(merged[field]);
    const nextValue = normalizeText(source?.[field]);
    if (!existingValue && nextValue) {
      merged[field] = nextValue;
    } else if (existingValue && nextValue && !existingValue.toLowerCase().includes(nextValue.toLowerCase())) {
      merged[field] = `${existingValue} ${nextValue}`.trim();
    }
  });

  return merged;
}

// Work history is more precise than education: keep the supplied month while
// presenting it in the compact dashboard format.  Do not use this for
// education, whose existing year-only display is intentional.
function formatWorkExperienceDateLabel(value) {
  const text = normalizeText(value);
  if (!text) return "";
  if (isOpenEndedExperienceValue(text)) return "Present";

  const timestamp = parseExperienceDate(text, "start");
  if (timestamp === null) return "";
  const date = new Date(timestamp);
  return `${MONTH_LABELS[date.getUTCMonth()]} ${date.getUTCFullYear()}`;
}

function formatWorkExperienceMonth(timestamp) {
  if (timestamp === null || timestamp === undefined) return "";
  const date = new Date(timestamp);
  return `${MONTH_LABELS[date.getUTCMonth()]} ${date.getUTCFullYear()}`;
}

function formatWorkExperienceDateRange(exp) {
  if (!exp || typeof exp !== "object") return "";

  let start = exp.start_date ?? exp.startDate ?? null;
  let end = exp.end_date ?? exp.endDate ?? null;
  const rawDates = normalizeText(exp.dates ?? exp.duration ?? "");

  if (rawDates) {
    const range = splitExperienceDateRange(rawDates);
    if (range) {
      if (!normalizeText(start)) start = range[0];
      if (!normalizeText(end)) end = range[1];
    }
  }

  const startLabel = formatWorkExperienceDateLabel(start);
  const endLabel = formatWorkExperienceDateLabel(end);
  if (!startLabel || startLabel === "Present") return endLabel === "Present" ? "Present" : "";
  return `${startLabel} – ${endLabel || "Present"}`;
}

function dedupeExperienceDescription(value) {
  const text = normalizeText(value);
  if (!text) return "";

  const seen = new Set();
  const uniqueFragments = text
    .split(/(?<=[.!?])\s+|\n+/)
    .map((fragment) => normalizeText(fragment).replace(/\s+([.!?])/g, "$1"))
    .filter((fragment) => {
      if (!fragment) return false;
      const key = normalizeKey(fragment).replace(/[^\w\s]+/g, " ").replace(/\s+/g, " ").trim();
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .join(" ");

  // Resume parsers occasionally repeat a whole description without sentence
  // punctuation (for example, a duplicated Python Developer responsibility).
  // Collapse an exact repeated half in addition to repeated sentences.
  const words = uniqueFragments.split(/\s+/).filter(Boolean);
  if (words.length >= 4 && words.length % 2 === 0) {
    const midpoint = words.length / 2;
    const left = words.slice(0, midpoint).join(" ");
    const right = words.slice(midpoint).join(" ");
    if (normalizeKey(left).replace(/[^\w\s]+/g, "") === normalizeKey(right).replace(/[^\w\s]+/g, "")) {
      return left;
    }
  }
  return uniqueFragments;
}

function dedupeExperienceDescriptionForRecord(value, exp) {
  const text = dedupeExperienceDescription(value);
  if (!text) return "";

  const identityKey = (item) => normalizeKey(item).replace(/[^\w\s]+/g, " ").replace(/\s+/g, " ").trim();
  const title = identityKey(exp?.title ?? exp?.role ?? "");
  const company = identityKey(exp?.company ?? exp?.company_name ?? "");
  const boilerplate = new Set([
    title,
    title && company ? `${title} at ${company}` : "",
  ].filter(Boolean));

  return text
    .split(/(?<=[.!?])\s+|\n+/)
    .filter((fragment) => !boilerplate.has(identityKey(fragment)))
    .join(" ");
}

function synthesizeExperienceDates(exp) {
  if (!exp || typeof exp !== "object") return "";
  const startLabel = normalizeText(exp.start_date ?? exp.startDate ?? "");
  if (!startLabel) return "";
  return formatWorkExperienceDateRange(exp);
}

export function calculateExperienceYears(experience) {
  if (!Array.isArray(experience) || experience.length === 0) return 0;

  const intervals = experience
    .map((exp) => extractExperienceInterval(exp))
    .filter(Boolean)
    .sort((a, b) => a[0] - b[0]);

  if (intervals.length === 0) return 0;

  // Use calendar months rather than day counts.  The profile intentionally
  // displays month-only dates, so a Jan 2024–Mar 2025 role is 15 months,
  // irrespective of the number of days in the intervening calendar years.
  const asMonth = (timestamp) => {
    const date = new Date(timestamp);
    return date.getUTCFullYear() * 12 + date.getUTCMonth();
  };
  const monthIntervals = intervals
    // Completed-role end dates are interval boundaries, while an active role
    // extends through the current month. This avoids counting an extra month
    // for ranges such as Jan 1–Jan 1.
    .map(([start, end]) => [asMonth(start), asMonth(Math.max(start, end - 1))])
    .sort((a, b) => a[0] - b[0]);

  let mergedMonths = 0;
  let [currentStart, currentEnd] = monthIntervals[0];

  for (let i = 1; i < monthIntervals.length; i += 1) {
    const [start, end] = monthIntervals[i];
    if (start <= currentEnd + 1) {
      currentEnd = Math.max(currentEnd, end);
    } else {
      mergedMonths += currentEnd - currentStart + 1;
      currentStart = start;
      currentEnd = end;
    }
  }

  mergedMonths += currentEnd - currentStart + 1;
  return Math.max(0, mergedMonths / 12);
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
  ["description", "summary"].forEach((field) => {
    if (typeof normalized[field] === "string") {
      normalized[field] = dedupeExperienceDescriptionForRecord(normalized[field], normalized);
    }
  });
  const formattedDates = formatWorkExperienceDateRange(normalized);
  if (formattedDates) {
    normalized.dates = formattedDates;
  }
  const window = parseExperienceWindow(normalized);
  if (window.start !== null) {
    normalized.start_date = formatWorkExperienceMonth(window.start);
  }
  if (window.openEnded) {
    normalized.end_date = "Present";
  } else if (window.end !== null) {
    normalized.end_date = formatWorkExperienceMonth(Math.max(window.start ?? window.end, window.end - 1));
  }
  return normalized;
}

function dedupeExperienceForDisplay(experience) {
  if (!Array.isArray(experience)) return [];
  if (experience.length <= 1) return experience.map((exp) => normalizeExperienceRecord(exp));

  const records = experience
    .filter((exp) => exp && typeof exp === "object")
    .map((exp, index) => {
      const sortValues = extractExperienceSortValues(exp);
      return {
        exp: { ...exp },
        index,
        score: experienceCompletenessScore(exp),
        sortValues,
        primary: sortValues.openEnded ? (sortValues.start ?? 0) : ((sortValues.end ?? sortValues.start) ?? 0),
        secondary: sortValues.start ?? sortValues.end ?? 0,
      };
    })
    .sort((a, b) => {
      if (a.score !== b.score) return b.score - a.score;
      if (a.sortValues.openEnded !== b.sortValues.openEnded) return a.sortValues.openEnded ? -1 : 1;
      if (a.primary !== b.primary) return (b.primary ?? 0) - (a.primary ?? 0);
      if (a.secondary !== b.secondary) return (b.secondary ?? 0) - (a.secondary ?? 0);
      return a.index - b.index;
    });

  const winners = [];
  records.forEach((record) => {
    let matchIndex = -1;
    let bestScore = -1;
    winners.forEach((winner, index) => {
      const score = experienceMatchScore(winner.exp, record.exp);
      if (score > bestScore) {
        bestScore = score;
        matchIndex = index;
      }
    });
    if (matchIndex === -1) {
      winners.push(record);
      return;
    }
    winners[matchIndex] = {
      ...winners[matchIndex],
      exp: mergeExperienceFields(winners[matchIndex].exp, record.exp),
      score: Math.max(winners[matchIndex].score, record.score),
    };
  });

  return winners
    .sort((a, b) => {
      if (a.sortValues.openEnded !== b.sortValues.openEnded) return a.sortValues.openEnded ? -1 : 1;
      if (a.primary !== b.primary) return (b.primary ?? 0) - (a.primary ?? 0);
      if (a.secondary !== b.secondary) return (b.secondary ?? 0) - (a.secondary ?? 0);
      return a.index - b.index;
    })
    .map(({ exp }) => {
      const normalized = normalizeExperienceRecord(exp);
      const synthesizedDates = synthesizeExperienceDates(normalized);
      if (synthesizedDates) {
        normalized.dates = synthesizedDates;
      }
      return normalized;
    });
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

function normalizeEducation(education) {
  if (!Array.isArray(education)) return [];

  const merged = [];
  const indexByKey = new Map();
  const educationKey = (entry) => {
    const degree = normalizeKey(entry?.degree || entry?.field_of_study || "");
    const institution = normalizeKey(entry?.institution || entry?.school || "");
    return `${institution}|${degree}`;
  };

  education.forEach((entry) => {
    if (!entry || typeof entry !== "object") return;
    const key = educationKey(entry);
    if (indexByKey.has(key)) {
      const target = merged[indexByKey.get(key)];
      ["degree", "institution", "dates", "duration", "start_date", "end_date", "field_of_study", "location", "description"].forEach(
        (field) => {
          if (!normalizeText(target[field]) && normalizeText(entry[field])) {
            target[field] = normalizeText(entry[field]);
          }
        }
      );
      return;
    }
    indexByKey.set(key, merged.length);
    merged.push({ ...entry });
  });

  return merged.map((entry) => {
    const formatted = formatExperienceDateRange(entry);
    return formatted ? { ...entry, dates: formatted } : entry;
  });
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

function isNonEmptyValue(value) {
  return normalizeText(value) !== "";
}

function pickFirstNonEmptyValue(primary, secondary) {
  return isNonEmptyValue(primary) ? primary : secondary;
}

function toArray(value) {
  return Array.isArray(value) ? value : [];
}

function readRawData(profile) {
  const raw = profile?.raw_data;
  return raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
}

function collectProfileList(profile, fields, rawFields = fields) {
  const raw = readRawData(profile);
  return [
    ...fields.flatMap((field) => toArray(profile?.[field])),
    ...rawFields.flatMap((field) => toArray(raw?.[field])),
  ];
}

export function mergeProfilesForDisplay(resumeProfile = {}, voiceProfile = {}) {
  const resume = resumeProfile && typeof resumeProfile === "object" ? resumeProfile : {};
  const voice = voiceProfile && typeof voiceProfile === "object" ? voiceProfile : {};
  const resumeRaw = readRawData(resume);
  const voiceRaw = readRawData(voice);
  const mergedRaw = {
    ...resumeRaw,
    ...voiceRaw,
  };
  mergedRaw.availability = normalizeAvailabilityValue(mergedRaw.availability);
  mergedRaw.salary_expectation = pickFirstNonEmptyValue(
    normalizeText(resumeRaw.salary_expectation) || normalizeText(resume.salary_expectation),
    normalizeText(voiceRaw.salary_expectation) || normalizeText(voice.salary_expectation)
  );

  const certifications = normalizeCertifications([
    ...collectProfileList(resume, ["certifications"]),
    ...collectProfileList(voice, ["certifications"]),
    ...toArray(mergedRaw.certifications),
  ]);

  const keySkills = normalizeSkills([
    ...collectProfileList(resume, ["keySkills", "skills"]),
    ...collectProfileList(voice, ["keySkills", "skills"]),
    ...toArray(mergedRaw.skills),
  ], certifications);

  const experience = dedupeExperienceForDisplay(
    sortExperienceForDisplay([
      ...collectProfileList(resume, ["experience", "work_experience"]),
      ...collectProfileList(voice, ["experience", "work_experience"]),
    ])
  );
  const education = normalizeEducation([
    ...collectProfileList(resume, ["education"]),
    ...collectProfileList(voice, ["education"]),
  ]);

  const preferredRoles = Array.from(
    new Map(
      collectProfileList(resume, ["preferred_roles"])
        .concat(collectProfileList(voice, ["preferred_roles"]))
        .map((role) => normalizeText(role))
        .filter(Boolean)
        .map((role) => [normalizeKey(role), role])
    ).values()
  );

  const merged = {
    ...resume,
    raw_data: mergedRaw,
    keySkills,
    skills: keySkills,
    certifications,
    experience,
    work_experience: experience,
    education,
    preferred_roles: preferredRoles,
  };

  merged.candidate_id = pickFirstNonEmptyValue(resume.candidate_id ?? resume.candidateId, voice.candidate_id ?? voice.candidateId);
  merged.candidateId = pickFirstNonEmptyValue(resume.candidateId ?? resume.candidate_id, voice.candidateId ?? voice.candidate_id);
  merged.avatar = pickFirstNonEmptyValue(resume.avatar ?? resume.photo_url, voice.avatar ?? voice.photo_url);
  merged.name = pickFirstNonEmptyValue(resume.name, voice.name);
  merged.email = pickFirstNonEmptyValue(resume.email, voice.email);
  merged.phone = pickFirstNonEmptyValue(resume.phone, voice.phone);
  merged.headline = pickFirstNonEmptyValue(
    resume.headline ?? resume.current_role,
    voice.headline ?? voice.current_role
  );
  merged.current_role = pickFirstNonEmptyValue(resume.current_role, voice.current_role);
  merged.current_company = pickFirstNonEmptyValue(resume.current_company, voice.current_company);
  merged.location = pickFirstNonEmptyValue(resume.location, voice.location);
  merged.bio = pickFirstNonEmptyValue(resume.bio, voice.bio);
  merged.summary = pickFirstNonEmptyValue(resume.summary, voice.summary);
  merged.experience_years = pickFirstNonEmptyValue(resume.experience_years, voice.experience_years);
  merged.availability = normalizeAvailabilityValue(pickFirstNonEmptyValue(resume.availability, voice.availability));
  merged.salary_expectation = pickFirstNonEmptyValue(
    normalizeText(resume.salary_expectation) || normalizeText(resumeRaw.salary_expectation),
    normalizeText(voice.salary_expectation) || normalizeText(voiceRaw.salary_expectation)
  );
  merged.additional_information = pickFirstNonEmptyValue(
    resume.additional_information,
    voice.additional_information
  );
  merged.voice_intake_resume = voice.voice_intake_resume ?? resume.voice_intake_resume ?? mergedRaw.voice_intake ?? null;

  return merged;
}

export function normalizeProfileForDisplay(profile = {}) {
  const raw = readRawData(profile);
  const certifications = normalizeCertifications(profile.certifications ?? []);
  const keySkills = normalizeSkills(profile.keySkills ?? profile.skills ?? [], certifications);
  const experience = dedupeExperienceForDisplay(
    sortExperienceForDisplay(profile.experience ?? profile.work_experience ?? [])
  );
  const education = normalizeEducation(profile.education ?? []);
  const calculatedExperienceYears = calculateExperienceYears(experience);
  const hasDatedWorkHistory = experience.some((entry) => extractExperienceInterval(entry) !== null);

  return {
    ...profile,
    availability: normalizeAvailabilityValue(profile.availability ?? raw.availability),
    salary_expectation: pickFirstNonEmptyValue(profile.salary_expectation, raw.salary_expectation),
    keySkills,
    certifications,
    experience,
    education,
    calculatedExperienceYears,
    // A dated work history is the current source of truth. This prevents an
    // older profile field from disagreeing with both the header and Bio.
    experience_years: hasDatedWorkHistory ? calculatedExperienceYears : profile.experience_years,
  };
}
