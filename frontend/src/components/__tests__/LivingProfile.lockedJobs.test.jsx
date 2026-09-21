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
  it("stacks every ranked match vertically, keeping the first three accessible and later matches redacted", () => {
    const view = renderJobs(Array.from({ length: 6 }, (_, index) => ({
      id: `job-${index + 1}`,
      title: index < 3 ? `Engineer ${index + 1}` : "Hidden title",
      company: "Acme",
      description: "Build products",
      locked: index >= 3,
    })));

    const list = view.container.querySelector('[data-testid="jobs-vertical-list"]');
    expect(list).toBeTruthy();
    expect(list.className).toContain("flex-col");
    expect(list.className).not.toContain("overflow-x-auto");
    expect([...list.children].map((card) => card.dataset.testid)).toEqual([
      "job-card-job-1",
      "job-card-job-2",
      "job-card-job-3",
      "locked-job-card-job-4",
      "locked-job-card-job-5",
      "locked-job-card-job-6",
    ]);
    expect(view.container.querySelectorAll('[data-testid^="job-card-"]')).toHaveLength(3);
    expect(view.container.querySelectorAll('[data-testid^="locked-job-card-"]')).toHaveLength(3);
    const locked = view.container.querySelector('[data-testid="locked-job-card-job-4"]');
    expect(locked).toBeTruthy();
    expect(locked.textContent).toContain("Unlock this match");
    expect(locked.textContent).not.toContain("Hidden title");
    expect(locked.querySelector(".blur-\\[7px\\]")).toBeTruthy();
    expect(locked.className).toContain("w-full");
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
