import { describe, expect, it } from "vitest";
import { buildResumeParseUrl, createApiConfig } from "../apiConfig";

describe("API configuration", () => {
  it("normalizes a backend URL with or without /api", () => {
    expect(createApiConfig("https://api.pontis.one/").apiUrl).toBe("https://api.pontis.one/api");
    expect(createApiConfig("https://api.pontis.one/api").apiUrl).toBe("https://api.pontis.one/api");
  });

  it("builds resume parse URLs with and without an existing candidate", () => {
    expect(buildResumeParseUrl()).toBe("http://test-backend.local/api/onboarding/parse-resume");
    expect(buildResumeParseUrl("candidate/123")).toBe(
      "http://test-backend.local/api/onboarding/parse-resume?existing_id=candidate%2F123",
    );
  });

  it("fails clearly when the backend URL is missing or invalid", () => {
    expect(() => createApiConfig(undefined)).toThrow(/VITE_BACKEND_URL is required/);
    expect(() => createApiConfig("not-a-url")).toThrow(/valid URL/);
  });
});
