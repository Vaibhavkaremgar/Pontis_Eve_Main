vi.mock("sonner", () => {
  const error = vi.fn();
  return { __esModule: true, toast: { error } };
});

import { toast } from "sonner";
import { getApiErrorMessage, notifyApiError } from "../apiError";

describe("central API error messages", () => {
  beforeEach(() => jest.clearAllMocks());

  it.each([
    [{ code: "ECONNABORTED" }, "The request is taking longer than expected. Please try again."],
    [{ response: { status: 401 } }, "Your session has expired. Please sign in again."],
    [{ response: { status: 429 } }, "Too many requests. Please wait a moment and try again."],
    [{ response: { status: 500 } }, "Something went wrong on our server. Please try again later."],
    [{ request: {} }, "Unable to connect. Please check your internet connection and try again."],
  ])("maps failures to safe user-facing copy", (error, expected) => {
    expect(getApiErrorMessage(error)).toBe(expected);
  });

  it("uses a safe API message for ordinary client errors", () => {
    expect(getApiErrorMessage({ response: { status: 422, data: { detail: "Please choose a resume file." } } }))
      .toBe("Please choose a resume file.");
  });

  it("does not expose technical response details", () => {
    expect(getApiErrorMessage({ response: { status: 400, data: { detail: "Traceback: secret token=abc" } } }))
      .toBe("We couldn't complete that request. Please try again.");
  });

  it("notifies only once when an error is handled in multiple layers", () => {
    const error = { response: { status: 429 } };
    notifyApiError(error);
    notifyApiError(error);
    expect(toast.error).toHaveBeenCalledTimes(1);
  });
});
