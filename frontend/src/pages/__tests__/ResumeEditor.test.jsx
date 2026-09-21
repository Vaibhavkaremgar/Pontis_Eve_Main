import React, { act } from "react";
import { createRoot } from "react-dom/client";
import axios from "axios";
import ResumeEditor from "../ResumeEditor";

jest.mock("axios");
jest.mock("react-router-dom", () => ({ useSearchParams: () => [new URLSearchParams("candidate_id=candidate-1&recommendation_id=rec-1")] }), { virtual: true });

const resume = { name: "Candidate", headline: "Engineer", skills: ["Python", "FastAPI"], work_experience: [], education: [], certifications: [], projects: [] };

describe("ResumeEditor profile refresh", () => {
  it("notifies the dashboard to refetch the canonical profile after save", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    axios.get.mockResolvedValue({ data: { resume, match_score: 50, missing_skills: [] } });
    axios.post.mockResolvedValue({ data: { match_score: 60, profile: { candidate_id: "candidate-1", keySkills: ["Python", "FastAPI", "Java"] } } });
    // jsdom normally has no opener; give this independent editor tab one.
    Object.defineProperty(window, "opener", { configurable: true, value: window });
    const postMessage = jest.spyOn(window, "postMessage").mockImplementation(() => {});
    const container = document.createElement("div");
    const root = createRoot(container);
    await act(async () => { root.render(<ResumeEditor />); });
    await act(async () => { await Promise.resolve(); });
    await act(async () => { container.querySelector("button").click(); });
    expect(postMessage).toHaveBeenCalledWith(expect.objectContaining({ type: "eve:candidate-profile-updated", candidateId: "candidate-1" }), window.location.origin);
    await act(async () => { root.unmount(); });
    postMessage.mockRestore();
  });
});
