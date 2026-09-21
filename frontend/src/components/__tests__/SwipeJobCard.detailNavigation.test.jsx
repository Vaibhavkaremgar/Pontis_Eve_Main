import React from "react";
import { act } from "react";
import ReactDOM from "react-dom/client";
import axios from "axios";

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
    match_score: 0.92,
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

  it("renders a horizontal card with its details and score on the right", () => {
    renderResult = renderDeck({ jobs: [{
      id: "job-1", title: "Senior Product Manager", company: "Acme", location: "Remote", salary: "$150k",
      description: "Lead product strategy.", match_score: 0.92, skills: ["Strategy"], job_url: "https://acme.example/jobs/1",
    }] });

    const card = renderResult.container.querySelector('[data-testid="job-card-job-1"]');
    expect(card.textContent).toContain("Senior Product Manager");
    expect(card.textContent).toContain("Acme");
    expect(card.textContent).toContain("Remote");
    expect(card.textContent).toContain("$150k");
    expect(renderResult.container.querySelector('[data-testid="match-score-job-1"]').textContent).toContain("92%");
    expect(card.querySelector('[data-testid="not-interested-job-1"]')).toBeTruthy();
    expect(card.querySelector('[data-testid="apply-job-1"]')).toBeTruthy();
    expect(card.querySelector('[data-testid="track-job-1"]')).toBeTruthy();
  });

  it("opens details from the card but not from a card action", () => {
    renderResult = renderDeck();
    act(() => {
      renderResult.container.querySelector('[data-testid="not-interested-job-1"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(renderResult.container.textContent).toContain("Why are you passing on this role?");
    expect(renderResult.container.querySelector('[aria-label="Back"]')).toBeNull();

    act(() => {
      renderResult.container.querySelector('[aria-label="Close"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
      renderResult.container.querySelector('[data-testid="job-card-job-1"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(renderResult.container.querySelector('[aria-label="Back"]')).toBeTruthy();
    expect(renderResult.container.textContent).toContain("Lead product strategy.");
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

  it("opens the improvement modal and displays missing requirements below 90%", async () => {
    axios.get.mockResolvedValueOnce({ data: { match_score: 0.72, missing_skills: ["Kubernetes"], requirements: ["Kubernetes experience required"] } });
    renderResult = renderDeck({ jobs: [{ id: "job-1", title: "Senior Product Manager", company: "Acme", location: "Remote", description: "Lead product strategy.", match_score: 0.72, skills: ["Kubernetes"], job_url: "https://acme.example/jobs/1" }] });
    await act(async () => {
      renderResult.container.querySelector('[data-testid="apply-job-1"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });
    expect(renderResult.container.querySelector('[data-testid="improve-match-modal"]')).toBeTruthy();
    expect(renderResult.container.textContent).toContain("Kubernetes");
    expect(openSpy).not.toHaveBeenCalled();
  });

  it("lets Fix My Resume enter the job-scoped editor without preliminary claims", async () => {
    axios.get.mockResolvedValueOnce({ data: { match_score: 0.72, missing_skills: ["Kubernetes"], requirements: ["Kubernetes experience required"] } });
    renderResult = renderDeck({ jobs: [{ id: "job-1", title: "Senior Product Manager", company: "Acme", match_score: 0.72, job_url: "https://acme.example/jobs/1" }] });
    await act(async () => {
      renderResult.container.querySelector('[data-testid="apply-job-1"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });
    const fix = renderResult.container.querySelector('[data-testid="fix-my-resume"]');
    expect(fix.disabled).toBe(false);
    act(() => fix.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    expect(renderResult.container.querySelector('[data-testid="resume-improvement-editor"]')).toBeTruthy();
    expect(renderResult.container.textContent).toContain("Kubernetes");
  });
});
