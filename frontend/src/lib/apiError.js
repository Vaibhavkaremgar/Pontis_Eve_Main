import { toast } from "sonner";

export const API_ERROR_MESSAGES = {
  timeout: "The request is taking longer than expected. Please try again.",
  rateLimit: "Too many requests. Please wait a moment and try again.",
  server: "Something went wrong on our server. Please try again later.",
  unauthorized: "Your session has expired. Please sign in again.",
  network: "Unable to connect. Please check your internet connection and try again.",
  fallback: "We couldn't complete that request. Please try again.",
};

const SENSITIVE_ERROR_PATTERN = /\b(stack|traceback|exception|sql|database|token|api[ _-]?key|password|secret)\b/i;

function responseMessage(error) {
  const detail = error?.response?.data?.detail ?? error?.response?.data?.message;
  const message = typeof detail === "string" ? detail.trim() : "";
  return message && message.length <= 300 && !SENSITIVE_ERROR_PATTERN.test(message) ? message : null;
}

/** Convert an Axios/fetch failure into copy that is safe to show to a candidate. */
export function getApiErrorMessage(error) {
  const status = error?.response?.status;
  if (error?.code === "ECONNABORTED" || error?.code === "ETIMEDOUT" || status === 408 || status === 504) return API_ERROR_MESSAGES.timeout;
  if (status === 429) return API_ERROR_MESSAGES.rateLimit;
  if (status === 500 || status === 502 || status === 503) return API_ERROR_MESSAGES.server;
  if (status === 401) return API_ERROR_MESSAGES.unauthorized;
  if (!error?.response || error?.request) return API_ERROR_MESSAGES.network;
  return responseMessage(error) || API_ERROR_MESSAGES.fallback;
}

/**
 * Show a failure once even when it is caught by a request interceptor and a
 * component. Components can use this for a contextual error without creating
 * a second toast.
 */
export function notifyApiError(error, options = {}) {
  if (error?.__eveErrorNotified) return getApiErrorMessage(error);
  const message = options.message || getApiErrorMessage(error);
  if (error && typeof error === "object") error.__eveErrorNotified = true;
  toast.error(message, { id: options.id || `api-error:${message}` });
  return message;
}
