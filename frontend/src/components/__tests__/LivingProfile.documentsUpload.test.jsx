import React from "react";
import { act } from "react";
import ReactDOM from "react-dom/client";
import axios from "axios";

import LivingProfile from "../LivingProfile";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

jest.mock("axios");
jest.mock("sonner", () => ({ toast: { error: jest.fn(), success: jest.fn() } }));

const BASE_PROPS = {
  activeTab: "documents",
  userProfile: {
    name: "Test User",
    strength: "Strong",
    strengthPercent: 80,
    experience: [],
    education: [],
    keySkills: [],
    preferred_roles: [],
    certifications: [],
    additional_information: "",
    isOpenToMatches: true,
  },
  jobs: [],
  docsLoading: false,
  candidateId: "cand-1",
  selectedJob: null,
  setSelectedJob: jest.fn(),
  onTrackJob: jest.fn(),
  onDismissJob: jest.fn(),
  onToggleOpenToMatches: jest.fn(),
  onResumeReplaced: jest.fn(),
  onCertUploaded: jest.fn(),
  onCertReplaced: jest.fn(),
  onResumeDeleted: jest.fn(),
  onCertDeleted: jest.fn(),
  onInterested: jest.fn(),
  onPhotoChange: jest.fn(),
  onJobViewed: jest.fn(),
};

function render(documents, extraProps = {}) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container);
  act(() => {
    root.render(<LivingProfile {...BASE_PROPS} documents={documents} {...extraProps} />);
  });
  return {
    container,
    unmount() {
      act(() => root.unmount());
      container.remove();
    },
  };
}

async function flush() {
  await act(async () => { await Promise.resolve(); });
}

afterEach(() => {
  document.body.innerHTML = "";
  jest.clearAllMocks();
});

// ─── Resume empty-state ───────────────────────────────────────────────────────

describe("Resume empty state", () => {
  it("shows 'Upload Resume' button when resume is null", () => {
    const { container, unmount } = render({ resume: null, certificates: [] });
    expect(container.querySelector("[data-testid='upload-resume-btn']")).not.toBeNull();
    expect(container.textContent).toContain("No resume on file.");
    unmount();
  });

  it("does NOT show 'Upload Resume' button when a resume exists", () => {
    const { container, unmount } = render({
      resume: { filename: "cv.pdf", fingerprint: null },
      certificates: [],
    });
    expect(container.querySelector("[data-testid='upload-resume-btn']")).toBeNull();
    unmount();
  });

  it("calls the resume/replace API and fires onResumeReplaced on upload", async () => {
    const onResumeReplaced = jest.fn();
    axios.post.mockResolvedValue({ data: { profile: null } });
    jest.spyOn(crypto.subtle, "digest").mockResolvedValue(new Uint8Array(32).buffer);

    const { container, unmount } = render(
      { resume: null, certificates: [] },
      { onResumeReplaced }
    );

    const input = container.querySelector("[data-testid='upload-resume-btn'] ~ input, input[accept='.pdf']");
    // Trigger via the hidden file input directly
    const fileInput = container.querySelector("input[type='file'][accept='.pdf']");
    const file = new File(["pdf"], "new-resume.pdf", { type: "application/pdf" });
    await act(async () => {
      Object.defineProperty(fileInput, "files", { value: [file], configurable: true });
      fileInput.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await flush();

    expect(axios.post).toHaveBeenCalledWith(
      expect.stringContaining("/resume/replace"),
      expect.any(FormData)
    );
    expect(onResumeReplaced).toHaveBeenCalledWith("new-resume.pdf", null);
    unmount();
  });

  // Regression: existing Replace/Delete buttons still present when resume exists
  it("still shows Replace and Delete buttons when resume exists", () => {
    const { container, unmount } = render({
      resume: { filename: "cv.pdf", fingerprint: null },
      certificates: [],
    });
    expect(container.textContent).toContain("Replace");
    expect(container.textContent).toContain("Delete");
    unmount();
  });
});

// ─── Certificate empty-state ──────────────────────────────────────────────────

describe("Certificate empty state", () => {
  it("shows 'Upload Certificate' button when certificates list is empty", () => {
    const { container, unmount } = render({ resume: null, certificates: [] });
    expect(container.querySelector("[data-testid='upload-certificate-btn']")).not.toBeNull();
    expect(container.textContent).toContain("No certificates uploaded yet.");
    unmount();
  });

  it("does NOT show 'Upload Certificate' empty-state button when certificates exist", () => {
    const { container, unmount } = render({
      resume: null,
      certificates: [{ id: "c1", filename: "cert.pdf" }],
    });
    expect(container.querySelector("[data-testid='upload-certificate-btn']")).toBeNull();
    unmount();
  });

  it("calls the certificates/upload API and fires onCertUploaded on upload", async () => {
    const onCertUploaded = jest.fn();
    const certData = { id: "c2", filename: "cert2.pdf" };
    axios.post.mockResolvedValue({ data: certData });

    const { container, unmount } = render(
      { resume: null, certificates: [] },
      { onCertUploaded }
    );

    const fileInput = container.querySelector("input[type='file'][accept='.pdf,.png,.jpg,.jpeg']");
    const file = new File(["data"], "cert2.pdf", { type: "application/pdf" });
    await act(async () => {
      Object.defineProperty(fileInput, "files", { value: [file], configurable: true });
      fileInput.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await flush();

    expect(axios.post).toHaveBeenCalledWith(
      expect.stringContaining("/certificates/upload"),
      expect.any(FormData)
    );
    expect(onCertUploaded).toHaveBeenCalledWith(certData);
    unmount();
  });

  // Regression: "+ Add certificate" button still present when certs exist
  it("still shows '+ Add certificate' button when certificates exist", () => {
    const { container, unmount } = render({
      resume: null,
      certificates: [{ id: "c1", filename: "cert.pdf" }],
    });
    expect(container.textContent).toContain("+ Add certificate");
    unmount();
  });
});
