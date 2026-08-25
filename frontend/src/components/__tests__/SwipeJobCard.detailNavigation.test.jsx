import React from "react";
import { act } from "react";
import ReactDOM from "react-dom/client";

import SwipeJobDeck from "../SwipeJobCard";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

jest.mock("axios");
jest.mock("sonner", () => ({
  toast: {
    error: jest.fn(),
    success: jest.fn(),
  },
}));

function renderDeck(props = {}) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container);
  const job = {
    id: "job-1",
    title: "Senior Product Manager",
    company: "Acme",
    location: "Remote",
    salary: "$150k",
    description: "<p>Lead product strategy.</p>",
    requirements: "<ul><li>5+ years experience</li></ul>",
    job_url: "https://acme.example/jobs/1",
  };

  act(() => {
    root.render(
      <SwipeJobDeck
        jobs={[job]}
        candidateId="cand-123"
        onJobsChange={jest.fn()}
        onDismissJob={jest.fn()}
        {...props}
      />
    );
  });

  return {
    container,
    root,
    job,
    unmount() {
      act(() => {
        root.unmount();
      });
      container.remove();
    },
  };
}

function dispatchMouseDown(node) {
  act(() => {
    node.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
  });
}

describe("SwipeJobDeck job details navigation", () => {
  let renderResult;
  let openSpy;

  beforeEach(() => {
    jest.clearAllMocks();
    openSpy = jest.spyOn(window, "open").mockImplementation(() => null);
  });

  afterEach(() => {
    renderResult?.unmount?.();
    renderResult = null;
    openSpy?.mockRestore?.();
  });

  function openDetail() {
    act(() => {
      renderResult.container.querySelector("h3").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(renderResult.container.querySelector('[aria-label="Back"]')).toBeTruthy();
  }

  it("closes the job details when the back arrow is clicked", () => {
    renderResult = renderDeck();
    openDetail();

    act(() => {
      renderResult.container.querySelector('[aria-label="Back"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(renderResult.container.querySelector('[aria-label="Back"]')).toBeNull();
  });

  it("closes the job details when clicking outside the panel", () => {
    renderResult = renderDeck();
    openDetail();

    dispatchMouseDown(renderResult.container.querySelector('[data-testid="job-detail-backdrop"]'));

    expect(renderResult.container.querySelector('[aria-label="Back"]')).toBeNull();
  });

  it("keeps the job details open when clicking inside the panel", () => {
    renderResult = renderDeck();
    openDetail();

    const insidePanel = renderResult.container.querySelector('[data-testid="job-detail-panel"]');
    dispatchMouseDown(insidePanel);

    expect(renderResult.container.querySelector('[aria-label="Back"]')).toBeTruthy();
  });

  it("preserves Apply Now and Not Interested behavior", () => {
    renderResult = renderDeck();
    openDetail();

    act(() => {
      const applyButton = Array.from(renderResult.container.querySelectorAll("button")).find((button) =>
        button.textContent?.includes("Apply Now")
      );
      applyButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(openSpy).toHaveBeenCalledWith("https://acme.example/jobs/1", "_blank", "noopener,noreferrer");
    expect(renderResult.container.querySelector('[aria-label="Back"]')).toBeTruthy();

    act(() => {
      renderResult.container.querySelector('[aria-label="Back"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    openDetail();

    act(() => {
      const notInterestedButton = Array.from(renderResult.container.querySelectorAll("button")).find((button) =>
        button.textContent?.includes("Not Interested")
      );
      notInterestedButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(renderResult.container.textContent).toContain("Why are you passing on this role?");
  });
});
