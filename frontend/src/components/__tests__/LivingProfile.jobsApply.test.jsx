import React from "react";
import { act } from "react";
import ReactDOM from "react-dom/client";
import axios from "axios";

import { JobsTab } from "../LivingProfile";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

jest.mock("axios");

const job = {
  id: "job-free-1", title: "Product Engineer", company: "Acme", location: "Remote",
  description: "Build useful products.", match_score: 0.88, job_url: "https://acme.example/jobs/1",
};

describe("JobsTab Apply Now", () => {
  let container;
  let root;
  let openSpy;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = ReactDOM.createRoot(container);
    openSpy = jest.spyOn(window, "open").mockImplementation(() => null);
    act(() => root.render(<JobsTab jobs={[job]} matchingJobsTotal={1} onTrack={jest.fn()} onDismiss={jest.fn()} selectedJob={null} setSelectedJob={jest.fn()} candidateId="cand-1" onJobViewed={jest.fn()} onLockedJobClick={jest.fn()} />));
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    openSpy.mockRestore();
  });

  it("does not navigate from the Jobs for You detail action; confirmation is the only external navigation", async () => {
    act(() => container.querySelector('[data-testid="job-card-job-free-1"]').dispatchEvent(new MouseEvent("click", { bubbles: true })));
    axios.get.mockResolvedValueOnce({ data: { match_score: 0.88, missing_skills: ["React"] } });
    await act(async () => {
      Array.from(container.querySelectorAll("button")).find((button) => button.textContent.includes("Apply Now")).dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });
    expect(container.querySelector('[data-testid="improve-match-modal"]')).toBeTruthy();
    expect(container.textContent).toContain("React");
    expect(openSpy).not.toHaveBeenCalled();
    act(() => Array.from(container.querySelectorAll("button")).find((button) => button.textContent.includes("Apply with Current Resume")).dispatchEvent(new MouseEvent("click", { bubbles: true })));
    expect(openSpy).toHaveBeenCalledWith(job.job_url, "_blank", "noopener,noreferrer");
  });
});
