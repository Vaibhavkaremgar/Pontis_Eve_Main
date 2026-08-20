export function getProfileStrengthLabel(percent) {
  if (percent >= 75) return "Strong";
  if (percent >= 50) return "Developing";
  return "Building";
}

export function hydrateProfileStrength(profile = {}) {
  const rawPercent =
    profile.profile_strength_percent ?? profile.strengthPercent ?? 0;
  const percentNumber = Number(rawPercent);
  const strengthPercent = Number.isFinite(percentNumber)
    ? Math.max(0, Math.min(100, Math.round(percentNumber)))
    : 0;
  const strength =
    profile.profile_strength_label ||
    profile.strength ||
    getProfileStrengthLabel(strengthPercent);

  return {
    ...profile,
    profile_strength_percent: strengthPercent,
    profile_strength_label: strength,
    strengthPercent,
    strength,
  };
}
