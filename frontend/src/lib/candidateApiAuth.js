import axios from "axios";
import { loadOnboardingState } from "./onboardingStorage";

// Resolve the signed candidate session at request time so login/token refreshes
// are reflected without trusting a candidate_id supplied in a request body.
axios.interceptors.request.use((config) => {
  const token = loadOnboardingState().candidateToken;
  const url = String(config?.url || "");
  if (token && url.includes("/api/") && !config.headers?.Authorization) {
    config.headers = config.headers || {};
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

