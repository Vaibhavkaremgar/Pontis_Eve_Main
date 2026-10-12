import axios from "axios";
import { loadOnboardingState } from "./onboardingStorage";

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
  return config;
});

