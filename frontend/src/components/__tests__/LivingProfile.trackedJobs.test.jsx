import React from "react";
import { act } from "react";
import ReactDOM from "react-dom/client";

import LivingProfile from "../LivingProfile";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

jest.mock("axios");
jest.mock("sonner", () => ({ toast: { error: jest.fn(), success: jest.fn() } }));

const TRACKED_JOB = {
  id: "job-tracked-1",
  title: "Senior Engineer",
  company: "Acme",
  location: "Remote",
  description: "<p>Build great things.</p>",
  requirements: "<p>5+ years experience.</p>",
  job_url: "https://acme.example/jobs/1",
  tracked: true,
  applied: false,
};

function renderTracked(jobs = [TRACKED_JOB], extraProps = {}) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container);
  act(() => {
    root.render(
      <LivingProfile
        activeTab="tracked"
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
        jobs={jobs}
        documents={{ resume: null, certificates: [] }}
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

function click(node) {
  act(() => {
    node.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

afterEach(() => {
  document.body.innerHTML = "";
  jest.clearAllMocks();
});

describe("Tracked Jobs — clickable card opens JD", () => {
  it("tracked job card is clickable and opens the JD view", () => {
    const { container, unmount } = renderTracked();
    const card = container.querySelector('[data-testid="tracked-job-card-job-tracked-1"]');
    expect(card).toBeTruthy();
    click(card);
    expect(container.querySelector('[data-testid="job-detail-panel"]')).toBeTruthy();
    unmount();
  });

  it("JD shows full job details (title, description)", () => {
    const { container, unmount } = renderTracked();
    click(container.querySelector('[data-testid="tracked-job-card-job-tracked-1"]'));
    const panel = container.querySelector('[data-testid="job-detail-panel"]');
    expect(panel.textContent).toContain("Senior Engineer");
    expect(panel.textContent).toContain("Build great things.");
    unmount();
  });

  it("Apply opens the job URL in a new tab", () => {
    const openSpy = jest.spyOn(window, "open").mockImplementation(() => null);
    const { container, unmount } = renderTracked();
    click(container.querySelector('[data-testid="tracked-job-card-job-tracked-1"]'));
    const applyBtn = Array.from(container.querySelectorAll("button")).find((b) =>
      b.textContent.includes("Apply Now")
    );
    expect(applyBtn).toBeTruthy();
    click(applyBtn);
    expect(openSpy).toHaveBeenCalledWith(
      "https://acme.example/jobs/1",
      "_blank",
      "noopener,noreferrer"
    );
    openSpy.mockRestore();
    unmount();
  });

  it("Cancel closes the JD and returns to Tracked Jobs", () => {
    const { container, unmount } = renderTracked();
    click(container.querySelector('[data-testid="tracked-job-card-job-tracked-1"]'));
    expect(container.querySelector('[data-testid="job-detail-panel"]')).toBeTruthy();

    // The footer "Not Interested" button acts as Cancel in TrackedTab (closes modal)
    const cancelBtn = Array.from(container.querySelectorAll("button")).find((b) =>
      b.textContent.includes("Not Interested")
    );
    expect(cancelBtn).toBeTruthy();
    click(cancelBtn);

    expect(container.querySelector('[data-testid="job-detail-panel"]')).toBeNull();
    expect(container.querySelector('[data-testid="tracked-tab-content"]')).toBeTruthy();
    unmount();
  });
});
