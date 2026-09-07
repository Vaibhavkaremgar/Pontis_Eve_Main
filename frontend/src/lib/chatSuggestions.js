function readRawData(profile) {
  const raw = profile?.raw_data;
  return raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
}

function normalizeText(value) {
  if (value === null || value === undefined) return "";
  return String(value).replace(/\s+/g, " ").trim();
}

function hasMeaningfulValue(value) {
  if (Array.isArray(value)) return value.some((item) => hasMeaningfulValue(item));
  if (value && typeof value === "object") return Object.values(value).some((item) => hasMeaningfulValue(item));
  return normalizeText(value) !== "";
}

function hasAnyField(profile, topLevelFields = [], rawFields = topLevelFields) {
  const raw = readRawData(profile);
  return [...topLevelFields.map((field) => profile?.[field]), ...rawFields.map((field) => raw?.[field])]
    .some((value) => hasMeaningfulValue(value));
}

function hasSalaryExpectation(profile) {
  const raw = readRawData(profile);
  const salaryPattern = /(?:[$₹€£]\s*\d|\d[\d,]*(?:\s*(?:[-–—]|to)\s*\d[\d,]*)?\s*(?:k|lpa|pa|per annum|annual(?:ly)?|year(?:ly)?|yr|lac|lakhs?|crore|crores)?)/i;
  const values = [
    profile?.salary_expectation,
    raw?.salary_expectation,
    raw?.additional_information,
    profile?.additional_information,
  ];
  return values.some((value) => {
    const text = normalizeText(value);
    return Boolean(text) && salaryPattern.test(text);
  });
}

const SUGGESTIONS = [
  { question: "What's your current job title and industry?", missing: (p) => !hasAnyField(p, ["headline", "current_role"]) },
  { question: "What are your top skills?", missing: (p) => !hasAnyField(p, ["keySkills", "skills"]) },
  { question: "Can you walk me through your work experience?", missing: (p) => !hasAnyField(p, ["experience", "work_experience"]) },
  { question: "What's your highest level of education?", missing: (p) => !hasAnyField(p, ["education"]) },
  { question: "Do you have any certifications?", missing: (p) => !hasAnyField(p, ["certifications"]) },
  { question: "What roles are you targeting?", missing: (p) => !hasAnyField(p, ["preferred_roles"]) },
  { question: "Where are you located?", missing: (p) => !hasAnyField(p, ["location", "preferred_locations"]) },
  { question: "What's your availability to start?", missing: (p) => !hasAnyField(p, ["availability", "notice_period"]) },
  { question: "Tell me about yourself in a few sentences.", missing: (p) => !hasAnyField(p, ["bio"]) },
  { question: "What salary range are you targeting?", missing: (p) => !hasSalaryExpectation(p) },
];

const FALLBACK = ["Search for roles", "Update preferences", "Salary help", "CV help"];

export function getDynamicChatSuggestions(profile, max = 4) {
  if (!profile) return FALLBACK;
  const missing = SUGGESTIONS.filter((s) => s.missing(profile)).map((s) => s.question);
  return missing.length > 0 ? missing.slice(0, max) : FALLBACK;
}
