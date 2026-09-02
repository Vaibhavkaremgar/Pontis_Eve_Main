import React from "react";
import { act } from "react";
import ReactDOM from "react-dom/client";
import axios from "axios";

import LivingProfile from "../LivingProfile";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

jest.mock("axios");
jest.mock("sonner", () => ({ toast: { error: jest.fn(), success: jest.fn() } }));

// Stable fake fingerprint used across tests
const EXISTING_FINGERPRINT = "abc123def456";

// Helper: build a SHA-256 ArrayBuffer whose hex equals EXISTING_FINGERPRINT.
// We mock crypto.subtle.digest so the actual bytes don't matter.
function makeFakeFile(name = "resume.pdf") {
  return new File(["pdf-content"], name, { type: "application/pdf" });
}

function mockDigest(hexResult) {
  // Convert hex string to Uint8Array so the component's Array.from loop produces hexResult
  const bytes = new Uint8Array(
    hexResult.match(/.{2}/g).map((b) => parseInt(b, 16))
  );
  const buffer = bytes.buffer;
  jest.spyOn(crypto.subtle, "digest").mockResolvedValue(buffer);
}

function renderDocuments(resumeFingerprint = null, extraProps = {}) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container);
  act(() => {
    root.render(
      <LivingProfile
        activeTab="documents"
        userProfile={{
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
        }}
        jobs={[]}
        documents={{
          resume: resumeFingerprint
            ? { filename: "old-resume.pdf", fingerprint: resumeFingerprint }
            : null,
          certificates: [],
        }}
        docsLoading={false}
        candidateId="cand-1"
        selectedJob={null}
        setSelectedJob={jest.fn()}
        onTrackJob={jest.fn()}
        onDismissJob={jest.fn()}
        onToggleOpenToMatches={jest.fn()}
        onResumeReplaced={jest.fn()}
        onCertUploaded={jest.fn()}
        onCertReplaced={jest.fn()}
        onResumeDeleted={jest.fn()}
        onCertDeleted={jest.fn()}
        onInterested={jest.fn()}
        onPhotoChange={jest.fn()}
        onJobViewed={jest.fn()}
        {...extraProps}
      />
    );
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

async function triggerFileInput(container, file) {
  const input = container.querySelector('input[type="file"][accept=".pdf"]');
  await act(async () => {
    Object.defineProperty(input, "files", { value: [file], configurable: true });
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await flush();
}

afterEach(() => {
  document.body.innerHTML = "";
  jest.clearAllMocks();
  jest.restoreAllMocks();
});

describe("Duplicate resume upload detection", () => {
  it("shows 'Duplicate Upload' error and does not call the API when the same file is re-uploaded", async () => {
    mockDigest(EXISTING_FINGERPRINT);
    const onResumeReplaced = jest.fn();
    const { container, unmount } = renderDocuments(EXISTING_FINGERPRINT, { onResumeReplaced });

    await triggerFileInput(container, makeFakeFile());

    expect(axios.post).not.toHaveBeenCalled();
    expect(onResumeReplaced).not.toHaveBeenCalled();
    expect(container.textContent).toContain("Duplicate Upload");
    unmount();
  });

  it("calls the API and does not show duplicate error when a different file is uploaded", async () => {
    const differentFingerprint = "999aaabbbccc";
    mockDigest(differentFingerprint); // different from EXISTING_FINGERPRINT
    const onResumeReplaced = jest.fn();
    axios.post.mockResolvedValue({ data: { profile: null } });

    const { container, unmount } = renderDocuments(EXISTING_FINGERPRINT, { onResumeReplaced });

    await triggerFileInput(container, makeFakeFile("new-resume.pdf"));

    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(onResumeReplaced).toHaveBeenCalledWith("new-resume.pdf", null);
    expect(container.textContent).not.toContain("Duplicate Upload");
    unmount();
  });

  it("calls the API normally when no existing fingerprint is stored (first upload)", async () => {
    mockDigest(EXISTING_FINGERPRINT);
    const onResumeReplaced = jest.fn();
    axios.post.mockResolvedValue({ data: { profile: null } });

    // No existing resume (fingerprint = null)
    const { container, unmount } = renderDocuments(null, { onResumeReplaced });

    // Render with a resume so the Replace button is visible
    act(() => {
      container.innerHTML = "";
    });
    // Re-render with a resume present but no fingerprint
    const root2 = ReactDOM.createRoot(container);
    act(() => {
      root2.render(
        <LivingProfile
          activeTab="documents"
          userProfile={{ name: "T", strength: "Strong", strengthPercent: 80, experience: [], education: [], keySkills: [], preferred_roles: [], certifications: [], additional_information: "", isOpenToMatches: true }}
          jobs={[]}
          documents={{ resume: { filename: "resume.pdf", fingerprint: null }, certificates: [] }}
          docsLoading={false}
          candidateId="cand-1"
          selectedJob={null}
          setSelectedJob={jest.fn()}
          onTrackJob={jest.fn()}
          onDismissJob={jest.fn()}
          onToggleOpenToMatches={jest.fn()}
          onResumeReplaced={onResumeReplaced}
          onCertUploaded={jest.fn()}
          onCertReplaced={jest.fn()}
          onResumeDeleted={jest.fn()}
          onCertDeleted={jest.fn()}
          onInterested={jest.fn()}
          onPhotoChange={jest.fn()}
          onJobViewed={jest.fn()}
        />
      );
    });

    await triggerFileInput(container, makeFakeFile());

    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(container.textContent).not.toContain("Duplicate Upload");
    act(() => root2.unmount());
    unmount();
  });

  it("non-duplicate error handling is unaffected — API failure still calls onResumeReplaced with null", async () => {
    const differentFingerprint = "deadbeef1234";
    mockDigest(differentFingerprint);
    const onResumeReplaced = jest.fn();
    axios.post.mockRejectedValue(new Error("Network error"));

    const { container, unmount } = renderDocuments(EXISTING_FINGERPRINT, { onResumeReplaced });

    await triggerFileInput(container, makeFakeFile("other.pdf"));

    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(onResumeReplaced).toHaveBeenCalledWith("other.pdf", null);
    expect(container.textContent).not.toContain("Duplicate Upload");
    unmount();
  });
});
