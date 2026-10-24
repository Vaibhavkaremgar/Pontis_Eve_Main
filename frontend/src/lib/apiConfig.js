import { z } from "zod";

const backendUrlSchema = z
  .string()
  .trim()
  .min(1, "VITE_BACKEND_URL is required")
  .url("VITE_BACKEND_URL must be a valid URL")
  .refine((value) => /^https?:$/i.test(new URL(value).protocol), {
    message: "VITE_BACKEND_URL must use http or https",
  });

export function createApiConfig(rawBackendUrl) {
  if (rawBackendUrl == null || String(rawBackendUrl).trim() === "") {
    throw new Error("Invalid backend configuration: VITE_BACKEND_URL is required");
  }
  const parsed = backendUrlSchema.safeParse(rawBackendUrl);
  if (!parsed.success) {
    throw new Error(`Invalid backend configuration: ${parsed.error.issues[0].message}`);
  }

  const backendUrl = parsed.data.replace(/\/+$/, "").replace(/\/api$/i, "");
  return {
    backendUrl,
    apiUrl: `${backendUrl}/api`,
    buildUrl: (path) => `${backendUrl}/api/${String(path).replace(/^\/+/, "")}`,
  };
}

export const { backendUrl: BACKEND_URL, apiUrl: API, buildUrl: buildApiUrl } =
  createApiConfig(import.meta.env.VITE_BACKEND_URL);

export function buildResumeParseUrl(existingId) {
  const url = new URL(buildApiUrl("onboarding/parse-resume"));
  if (existingId) url.searchParams.set("existing_id", existingId);
  return url.toString();
}
