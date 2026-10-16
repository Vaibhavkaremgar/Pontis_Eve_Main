import axios from "axios";
import { clearOnboardingState, loadOnboardingState } from "./onboardingStorage";
import { notifyApiError } from "./apiError";

function isCandidateApiRequest(config) {
  const url = String(config?.url || "");
  return url.includes("/api/candidate/") || url.includes("/api/chat") || url.includes("/api/voice/");
}

export function candidateAuthHeaders() {
  const token = loadOnboardingState().candidateToken;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// Resolve the signed candidate session at request time so login/token refreshes
// are reflected without trusting a candidate_id supplied in a request body.
axios.interceptors.request.use((config) => {
  const url = String(config?.url || "");
  const headers = candidateAuthHeaders();
  if (Object.keys(headers).length && url.includes("/api/") && !config.headers?.Authorization) {
    config.headers = config.headers || {};
    config.headers.Authorization = headers.Authorization;
  }
  // Track only requests authenticated with the current candidate session. This
  // prevents unrelated 401s (for example an integration or public endpoint)
  // from signing the candidate out.
  config.__eveCandidateSessionRequest = Boolean(
    isCandidateApiRequest(config) && headers.Authorization &&
    config.headers?.Authorization === headers.Authorization,
  );
  return config;
});

// A deployed backend may rotate CANDIDATE_SESSION_SECRET. Existing browser
// tokens then become invalid; never retry them or make the API public. Clear
// only the candidate session and return the user to the sign-in flow.
axios.interceptors.response.use(
  (response) => response,
  (error) => {
    const config = error?.config;
    if (error?.response?.status === 401 && config?.__eveCandidateSessionRequest) {
      clearOnboardingState();
      if (typeof window !== "undefined" && window.location.pathname !== "/") {
        window.location.replace("/");
      }
    }
    // A single response interceptor provides visible, consistent feedback for
    // every Axios request. notifyApiError marks the error so component-level
    // handlers can safely add inline context without duplicate notifications.
    notifyApiError(error);
    return Promise.reject(error);
  },
);

