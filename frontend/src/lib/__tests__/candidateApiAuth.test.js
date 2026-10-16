import axios from "axios";
import { candidateAuthHeaders } from "../candidateApiAuth";
import { saveOnboardingState, loadOnboardingState } from "../onboardingStorage";

describe("candidate API authentication", () => {
  beforeEach(() => {
    localStorage.clear();
    saveOnboardingState({ candidateId: "candidate-a", candidateToken: "session-token-a" });
  });

  it("builds the bearer header from the current stored session", () => {
    expect(candidateAuthHeaders()).toEqual({ Authorization: "Bearer session-token-a" });
  });

  it("adds the current session to absolute protected API requests", () => {
    const interceptor = axios.interceptors.request.handlers.at(-1).fulfilled;
    const result = interceptor({ url: "https://api.example.test/api/candidate/candidate-a/profile" });

    expect(result.headers.Authorization).toBe("Bearer session-token-a");
  });

  it("clears an invalid candidate session after a protected 401", async () => {
    const responseInterceptor = axios.interceptors.response.handlers.at(-1).rejected;
    const requestInterceptor = axios.interceptors.request.handlers.at(-1).fulfilled;
    window.history.replaceState({}, "", "/");

    const config = requestInterceptor({ url: "https://api.example.test/api/candidate/candidate-a/profile" });

    await expect(responseInterceptor({
      config,
      response: { status: 401 },
    })).rejects.toMatchObject({ response: { status: 401 } });

    expect(loadOnboardingState().candidateToken).toBeNull();
  });

  it("does not clear a candidate session for an unrelated 401", async () => {
    const responseInterceptor = axios.interceptors.response.handlers.at(-1).rejected;

    await expect(responseInterceptor({
      config: { url: "https://api.example.test/api/auth/session/exchange" },
      response: { status: 401 },
    })).rejects.toMatchObject({ response: { status: 401 } });

    expect(loadOnboardingState().candidateToken).toBe("session-token-a");
  });
});
