import React from "react";
import { act } from "react";
import ReactDOM from "react-dom/client";
import axios from "axios";

import CandidateSettingsModal from "../CandidateSettingsModal";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

jest.mock("axios");
jest.mock("sonner", () => ({
  toast: {
    success: jest.fn(),
    error: jest.fn(),
  },
}));

function renderModal(props = {}) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container);

  act(() => {
    root.render(
      <CandidateSettingsModal
        open
        onOpenChange={jest.fn()}
        candidateId="cand-123"
        candidateToken="token-abc"
        candidateName="Jane Doe"
        candidateEmail="jane@example.com"
        onDeleteSuccess={jest.fn()}
        {...props}
      />
    );
  });

  return {
    container,
    root,
    unmount() {
      act(() => {
        root.unmount();
      });
      container.remove();
    },
  };
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
  });
}

function setNativeValue(element, value) {
  const setter = Object.getOwnPropertyDescriptor(
    element.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype,
    "value"
  ).set;
  setter.call(element, value);
  element.dispatchEvent(new Event("input", { bubbles: true }));
}

describe("CandidateSettingsModal", () => {
  let renderResult;

  afterEach(() => {
    renderResult?.unmount?.();
    renderResult = null;
    jest.clearAllMocks();
  });

  it("renders the FAQ flow and sends a support message through the backend", async () => {
    axios.post.mockResolvedValue({ data: { status: "sent" } });
    renderResult = renderModal();

    act(() => {
      document.body.querySelector('[data-testid="settings-need-help-btn"]').click();
    });

    expect(document.body.textContent).toContain("How do I update my profile?");

    act(() => {
      document.body.querySelector('[data-testid="settings-other-btn"]').click();
    });

    expect(document.body.textContent).toContain("Contact Support");

    const subject = document.body.querySelector('input[placeholder="How can we help?"]');
    const message = document.body.querySelector('textarea[placeholder="Tell us what you need help with."]');
    act(() => {
      setNativeValue(subject, "Candidate question");
      setNativeValue(message, "I need help with my profile.");
    });
    await flush();

    await act(async () => {
      document.body.querySelector('[data-testid="help-send-btn"]').click();
      await Promise.resolve();
    });

    expect(axios.post).toHaveBeenCalledWith(
      expect.stringContaining("/candidate/cand-123/help"),
      expect.objectContaining({
        candidate_id: "cand-123",
        subject: "Candidate question",
        message: "I need help with my profile.",
      }),
      expect.objectContaining({
        headers: { Authorization: "Bearer token-abc" },
      })
    );
    expect(document.body.querySelector('[data-testid="help-success"]').textContent).toContain("sent");
  });

  it("opens the delete confirmation and calls the authenticated delete endpoint", async () => {
    axios.delete.mockResolvedValue({ data: { status: "deleted" } });
    const onDeleteSuccess = jest.fn();
    renderResult = renderModal({ onDeleteSuccess });

    act(() => {
      document.body.querySelector('[data-testid="settings-delete-account-btn"]').click();
    });

    expect(document.body.textContent).toContain("Are you sure you want to delete your account?");

    await act(async () => {
      document.body.querySelector('[data-testid="confirm-delete-account-btn"]').click();
      await Promise.resolve();
    });

    expect(axios.delete).toHaveBeenCalledWith(
      expect.stringContaining("/candidate/cand-123/account"),
      expect.objectContaining({
        headers: { Authorization: "Bearer token-abc" },
      })
    );
    expect(onDeleteSuccess).toHaveBeenCalledTimes(1);
  });
});
