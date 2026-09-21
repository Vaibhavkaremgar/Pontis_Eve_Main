import React from "react";
import { act } from "react";
import ReactDOM from "react-dom/client";

import { JobsTab } from "../LivingProfile";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

jest.mock("../SwipeJobCard", () => ({
  JobDetailModal: () => null,
  NotInterestedReasonModal: () => null,
}));

function renderJobs(jobs, onLockedJobClick = jest.fn()) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container);
  act(() => root.render(
    <JobsTab
      jobs={jobs}
      matchingJobsTotal={jobs.length}
      onTrack={jest.fn()}
      onDismiss={jest.fn()}
      selectedJob={null}
      setSelectedJob={jest.fn()}
      candidateId="candidate-1"
      onJobViewed={jest.fn()}
      onLockedJobClick={onLockedJobClick}
    />
  ));
  return { container, onLockedJobClick, unmount: () => act(() => root.unmount()) };
}

describe("JobsTab locked recommendations", () => {
  it("renders accessible jobs normally and redacted locked cards in the horizontal list", () => {
    const view = renderJobs([
      { id: "job-1", title: "Engineer", company: "Acme", description: "Build products", locked: false },
      { id: "job-2", title: "Hidden title", locked: true },
    ]);

    expect(view.container.querySelector('[data-testid="jobs-horizontal-list"]')).toBeTruthy();
    expect(view.container.querySelector('[data-testid="job-card-job-1"]')).toBeTruthy();
    const locked = view.container.querySelector('[data-testid="locked-job-card-job-2"]');
    expect(locked).toBeTruthy();
    expect(locked.textContent).toContain("Unlock this match");
    expect(locked.textContent).not.toContain("Hidden title");
    expect(locked.querySelector(".blur-\\[7px\\]")).toBeTruthy();
    view.unmount();
  });

  it("routes a locked-card click only to the subscription-popup callback", () => {
    const view = renderJobs([{ id: "job-4", locked: true }]);
    act(() => view.container.querySelector('[data-testid="locked-job-card-job-4"]').click());
    expect(view.onLockedJobClick).toHaveBeenCalledTimes(1);
    expect(view.container.querySelector('[data-testid="job-track-job-4"]')).toBeNull();
    expect(view.container.querySelector('[data-testid="job-dismiss-job-4"]')).toBeNull();
    view.unmount();
  });
});
