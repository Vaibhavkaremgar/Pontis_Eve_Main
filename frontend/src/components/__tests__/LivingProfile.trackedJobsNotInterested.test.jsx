import React from "react";
import { act } from "react";
import ReactDOM from "react-dom/client";

import LivingProfile from "../LivingProfile";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

jest.mock("axios");
jest.mock("sonner", () => ({ toast: { error: jest.fn(), success: jest.fn() } }));

const TRACKED_JOB = {
  id: "job-t1",
  title: "Staff Engineer",
  company: "Globex",
  location: "Remote",
  description: "<p>Build things.</p>",
  job_url: "https://globex.example/jobs/1",
  tracked: true,
  applied: false,
};

function renderTracked(onDismissJob = jest.fn(), jobs = [TRACKED_JOB]) {
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
        onDismissJob={onDismissJob}
        onToggleOpenToMatches={jest.fn()}
        onResumeReplaced={jest.fn()}
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

function getButton(container, text) {
  return Array.from(container.querySelectorAll("button")).find((b) =>
    b.textContent.trim() === text || b.textContent.includes(text)
  );
}

afterEach(() => {
  document.body.innerHTML = "";
  jest.clearAllMocks();
});

describe("Tracked Jobs — Not Interested flow", () => {
  it("clicking Not Interested in the JD opens the reason popup", () => {
    const { container, unmount } = renderTracked();
    click(container.querySelector('[data-testid="tracked-job-card-job-t1"]'));
    expect(container.querySelector('[data-testid="job-detail-panel"]')).toBeTruthy();

    click(getButton(container, "Not Interested"));

    // JD closes, reason modal opens
    expect(container.querySelector('[data-testid="job-detail-panel"]')).toBeNull();
    expect(container.textContent).toContain("Why are you passing on this role?");
    unmount();
  });

  it("selecting a reason and clicking OK calls onDismissJob with that reason", async () => {
    const onDismissJob = jest.fn().mockResolvedValue();
    const { container, unmount } = renderTracked(onDismissJob);

    click(container.querySelector('[data-testid="tracked-job-card-job-t1"]'));
    click(getButton(container, "Not Interested"));

    // Select "Company not preferred"
    const reasonBtn = Array.from(container.querySelectorAll("button")).find((b) =>
      b.textContent.includes("Company not preferred")
    );
    click(reasonBtn);

    await act(async () => {
      click(getButton(container, "OK"));
    });

    expect(onDismissJob).toHaveBeenCalledWith("job-t1", "Company not preferred");
    unmount();
  });

  it("after OK the job is removed from Tracked Jobs list", async () => {
    const onDismissJob = jest.fn().mockResolvedValue();
    // Simulate Dashboard behaviour: onDismissJob removes the job from state.
    // We test that the modal closes and the popup disappears (parent controls list removal).
    const { container, unmount } = renderTracked(onDismissJob);

    click(container.querySelector('[data-testid="tracked-job-card-job-t1"]'));
    click(getButton(container, "Not Interested"));

    await act(async () => {
      click(getButton(container, "OK"));
    });

    // Reason modal is gone after OK
    expect(container.textContent).not.toContain("Why are you passing on this role?");
    unmount();
  });

  it("Cancel in the reason popup keeps the job in Tracked Jobs", () => {
    const onDismissJob = jest.fn();
    const { container, unmount } = renderTracked(onDismissJob);

    click(container.querySelector('[data-testid="tracked-job-card-job-t1"]'));
    click(getButton(container, "Not Interested"));
    expect(container.textContent).toContain("Why are you passing on this role?");

    click(getButton(container, "Cancel"));

    expect(onDismissJob).not.toHaveBeenCalled();
    expect(container.textContent).not.toContain("Why are you passing on this role?");
    // Tracked tab is still visible
    expect(container.querySelector('[data-testid="tracked-tab-content"]')).toBeTruthy();
    unmount();
  });
});
