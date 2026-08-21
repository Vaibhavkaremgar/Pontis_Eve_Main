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

  return {
    ...profile,
    keySkills,
    certifications,
  };
}
