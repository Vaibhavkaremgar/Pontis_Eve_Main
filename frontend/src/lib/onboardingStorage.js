// Simple, resilient localStorage wrapper for onboarding progress.
// File objects can't be serialized, so we only persist metadata + parsed profile.

const KEY = "eve_onboarding_v1";

const DEFAULT_STATE = {
  step: 1,
  countryCode: "US",
  phoneDigits: "",
  resumeMeta: null, // { name, size }
  certsMeta: [], // [{ name, size }]
  parsedProfile: null, // full backend response
  candidateId: null, // UUID from the Dashboard candidate record
  newlyOnboarded: false,
  transcription: false,
  muted: false,
  voiceElapsedMs: 0, // how long the candidate stayed on the voice-intake step
  voiceIntakeCompleted: false,
  voiceIntakeProgress: 0, // last answered question index (0 = not started)
  isOpenToMatches: true,
  linkedInAuthenticated: false,
  linkedInProfile: null, // { name, email, picture, linkedin_id }
};

export function loadOnboardingState() {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return { ...DEFAULT_STATE };
    const parsed = JSON.parse(raw);
    return { ...DEFAULT_STATE, ...parsed };
  } catch {
    return { ...DEFAULT_STATE };
  }
}

export function saveOnboardingState(state) {
  try {
    // Only persist serializable slice
    const slim = {
      step: state.step,
      countryCode: state.countryCode,
      phoneDigits: state.phoneDigits,
      resumeMeta: state.resumeMeta,
      certsMeta: state.certsMeta,
      parsedProfile: state.parsedProfile,
      candidateId: state.candidateId ?? null,
      newlyOnboarded: state.newlyOnboarded ?? false,
      transcription: state.transcription,
      muted: state.muted,
      voiceElapsedMs: state.voiceElapsedMs,
      voiceIntakeCompleted: state.voiceIntakeCompleted ?? false,
      voiceIntakeProgress: state.voiceIntakeProgress ?? 0,
      isOpenToMatches: state.isOpenToMatches ?? true,
      linkedInAuthenticated: state.linkedInAuthenticated ?? false,
      linkedInProfile: state.linkedInProfile ?? null,
    };
    localStorage.setItem(KEY, JSON.stringify(slim));
  } catch {
    // Quota / private mode — ignore silently
  }
}

export function clearOnboardingState() {
  try {
    localStorage.removeItem(KEY);
  } catch {
    // ignore
  }
}

/**
 * When restoring, clamp the step to something we can actually resume:
 * - If no parsedProfile yet → user must re-upload their resume, so cap at 2.
 * - If parsedProfile exists → user can resume anywhere from 3 onward.
 */
export function resumableStep(state) {
  const step = state?.step || 1;
  if (state?.parsedProfile) return step;
  return Math.min(step, 2);
}
