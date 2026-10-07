import React, { act } from "react";
import { createRoot } from "react-dom/client";
import axios from "axios";
import ResumeEditor from "../ResumeEditor";

jest.mock("axios");
jest.mock("react-router-dom", () => {
  const stableSearchParams = new URLSearchParams("candidate_id=candidate-1&recommendation_id=rec-1&fix_credit_claim_id=claim-initial");
  return { useSearchParams: () => [stableSearchParams] };
}, { virtual: true });

const resume = { name: "Candidate", headline: "Engineer", skills: ["Python", "FastAPI"], work_experience: [], education: [], certifications: [], projects: [] };

describe("ResumeEditor profile refresh", () => {
  it("rotates claims across three saves without skipping the stored claim", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const claimResponses = ["claim-B", "claim-C", "claim-D"].map((claim_id) => ({ data: { claim_id } }));
    axios.get.mockResolvedValue({ data: { resume, match_score: 50, missing_skills: [] } });
    axios.post
      .mockImplementationOnce(async () => ({ data: { match_score: 60, profile: { candidate_id: "candidate-1" } } }))
      .mockImplementationOnce(async () => claimResponses[0])
      .mockImplementationOnce(async () => ({ data: { match_score: 70, profile: { candidate_id: "candidate-1" } } }))
      .mockImplementationOnce(async () => claimResponses[1])
      .mockImplementationOnce(async () => ({ data: { match_score: 80, profile: { candidate_id: "candidate-1" } } }))
      .mockImplementationOnce(async () => claimResponses[2]);
    const container = document.createElement("div");
    const root = createRoot(container);
    await act(async () => { root.render(<ResumeEditor />); });
    await act(async () => { await Promise.resolve(); });
    const saveButton = () => Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "Save Changes");
    for (let i = 0; i < 3; i += 1) await act(async () => { saveButton().click(); await Promise.resolve(); });
    const matchCalls = axios.post.mock.calls.filter(([url]) => url.includes("match-improvement"));
    expect(matchCalls.map(([, body]) => body.fix_credit_claim_id)).toEqual(["claim-initial", "claim-B", "claim-C"]);
    expect(axios.post.mock.calls.filter(([url]) => url.includes("resume-fix-credit-claim"))).toHaveLength(3);
    await act(async () => { root.unmount(); });
  });

  it("shows the successful save when claim rotation fails and fetches a fresh claim next time", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    axios.get.mockResolvedValue({ data: { resume, match_score: 50, missing_skills: [] } });
    axios.post
      .mockResolvedValueOnce({ data: { match_score: 60, profile: { candidate_id: "candidate-1" } } })
      .mockRejectedValueOnce(new Error("rotation failed"))
      .mockResolvedValueOnce({ data: { claim_id: "claim-fresh" } })
      .mockResolvedValueOnce({ data: { match_score: 70, profile: { candidate_id: "candidate-1" } } })
      .mockResolvedValueOnce({ data: { claim_id: "claim-next" } });
    const container = document.createElement("div");
    const root = createRoot(container);
    await act(async () => { root.render(<ResumeEditor />); });
    await act(async () => { await Promise.resolve(); });
    await act(async () => { Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "Save Changes").click(); await Promise.resolve(); });
    expect(container.querySelector('[data-testid="resume-save-result"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="resume-claim-warning"]')?.textContent).toContain("changes were saved");
    await act(async () => { Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "Save Changes").click(); await Promise.resolve(); });
    const matchCalls = axios.post.mock.calls.filter(([url]) => url.includes("match-improvement"));
    expect(matchCalls.map(([, body]) => body.fix_credit_claim_id)).toEqual(["claim-initial", "claim-fresh"]);
    await act(async () => { root.unmount(); });
  });

  it("prevents a second save request while the first save is pending", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    let resolveMatch;
    axios.get.mockResolvedValue({ data: { resume, match_score: 50, missing_skills: [] } });
    axios.post
      .mockImplementationOnce(() => new Promise((resolve) => { resolveMatch = resolve; }));
    const container = document.createElement("div");
    const root = createRoot(container);
    await act(async () => { root.render(<ResumeEditor />); });
    await act(async () => { await Promise.resolve(); });
    const saveButton = () => Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "Save Changes");
    await act(async () => { saveButton().click(); saveButton().click(); });
    expect(axios.post.mock.calls.filter(([url]) => url.includes("match-improvement"))).toHaveLength(1);
    resolveMatch({ data: { match_score: 60, profile: { candidate_id: "candidate-1" } } });
    await act(async () => { await Promise.resolve(); });
    await act(async () => { root.unmount(); });
  });

  it("renders separate skills with bullet separators", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    axios.get.mockResolvedValue({ data: { resume: { ...resume, skills: ["React.js", "Node.js", "Frontend Development"] }, match_score: 50, missing_skills: [] } });
    const container = document.createElement("div");
    const root = createRoot(container);
    await act(async () => { root.render(<ResumeEditor />); });
    await act(async () => { await Promise.resolve(); });
    expect(container.querySelector('[data-testid="resume-skills"]').textContent).toBe("React.js • Node.js • Frontend Development");
    await act(async () => { root.unmount(); });
  });

  it("renders each cleaned canonical skill as a separate bullet-delimited value", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const canonicalSkills = ["React.js", "Node.js", "Frontend Development", "AI Applications", "Google Cloud Platform", "Database Design", "Problem Solving", "Object-Oriented Programming", "SQL", "HTML5"];
    axios.get.mockResolvedValue({ data: { resume: { ...resume, skills: canonicalSkills }, match_score: 50, missing_skills: [] } });
    const container = document.createElement("div");
    const root = createRoot(container);
    await act(async () => { root.render(<ResumeEditor />); });
    await act(async () => { await Promise.resolve(); });
    expect(container.querySelector('[data-testid="resume-skills"]').textContent).toBe(canonicalSkills.join(" \u2022 "));
    await act(async () => { root.unmount(); });
  });

  it("renders the reported normalized skills as four separate bullet-delimited values", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const canonicalSkills = ["HTML5", "Express.js", "Flask", "CSS3"];
    axios.get.mockResolvedValue({ data: { resume: { ...resume, skills: canonicalSkills }, match_score: 50, missing_skills: [] } });
    const container = document.createElement("div");
    const root = createRoot(container);
    await act(async () => { root.render(<ResumeEditor />); });
    await act(async () => { await Promise.resolve(); });
    expect(container.querySelector('[data-testid="resume-skills"]').textContent).toBe(canonicalSkills.join(" \u2022 "));
    await act(async () => { root.unmount(); });
  });

  it("renders canonical separate skills after saving one newly entered value", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const canonicalSkills = ["HTML5", "Express.js", "Flask", "CSS3"];
    axios.get.mockResolvedValue({ data: { resume: { ...resume, skills: [] }, match_score: 50, missing_skills: canonicalSkills } });
    axios.post.mockResolvedValue({ data: { match_score: 60, profile: { candidate_id: "candidate-1", keySkills: canonicalSkills }, remaining_missing_skills: [] } });
    const container = document.createElement("div");
    const root = createRoot(container);
    await act(async () => { root.render(<ResumeEditor />); });
    await act(async () => { await Promise.resolve(); });
    const skills = container.querySelector('[data-testid="resume-skills"]');
    skills.textContent = "HTML5 Express.js FlaskCSS3";
    // jsdom does not implement HTMLElement.innerText, which the
    // contentEditable blur handler reads in browsers.
    Object.defineProperty(skills, "innerText", { configurable: true, value: "HTML5 Express.js FlaskCSS3" });
    await act(async () => { skills.dispatchEvent(new FocusEvent("focusout", { bubbles: true })); });
    await act(async () => { container.querySelector("button").click(); });
    expect(axios.post).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({
      profile_updates: expect.objectContaining({ skills: ["HTML5 Express.js FlaskCSS3"] }),
    }));
    expect(skills.textContent).toBe(canonicalSkills.join(" \u2022 "));
    expect(container.querySelectorAll("aside .mt-2.flex.flex-wrap.gap-2 span")).toHaveLength(0);
    await act(async () => { root.unmount(); });
  });

  it("posts every bullet-delimited skill as an individual array member", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    axios.get.mockResolvedValue({ data: { resume: { ...resume, skills: ["CSS3"] }, match_score: 50, missing_skills: [] } });
    axios.post.mockResolvedValue({ data: { match_score: 60, profile: { candidate_id: "candidate-1", keySkills: ["CSS3", "OpenCV", "Computer Vision"] } } });
    const container = document.createElement("div");
    const root = createRoot(container);
    await act(async () => { root.render(<ResumeEditor />); });
    await act(async () => { await Promise.resolve(); });
    const skills = container.querySelector('[data-testid="resume-skills"]');
    Object.defineProperty(skills, "innerText", { configurable: true, value: "CSS3 \u2022 OpenCV \u2022 Computer Vision" });
    await act(async () => { skills.dispatchEvent(new FocusEvent("focusout", { bubbles: true })); });
    await act(async () => { container.querySelector("button").click(); });
    expect(axios.post).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({
      profile_updates: expect.objectContaining({ skills: ["CSS3", "OpenCV", "Computer Vision"] }),
    }));
    expect(skills.textContent).toBe("CSS3 \u2022 OpenCV \u2022 Computer Vision");
    await act(async () => { root.unmount(); });
  });

  it("notifies the dashboard to refetch the canonical profile after save", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    axios.get.mockResolvedValue({ data: { resume, match_score: 50, missing_skills: [] } });
    axios.post
      .mockResolvedValueOnce({ data: { match_score: 60, profile: { candidate_id: "candidate-1", keySkills: ["Python", "FastAPI", "Java"] } } })
      .mockResolvedValueOnce({ data: { claim_id: "claim-next" } });
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

  it("renders the freshly recalculated match score returned after save", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const raf = global.requestAnimationFrame;
    global.requestAnimationFrame = (callback) => { callback(performance.now() + 1000); return 1; };
    axios.get.mockResolvedValue({ data: { resume, match_score: 42, missing_skills: [] } });
    axios.post
      .mockResolvedValueOnce({ data: { previous_match_score: 42, match_score: 87, profile: { candidate_id: "candidate-1" } } })
      .mockResolvedValueOnce({ data: { claim_id: "claim-next" } });
    const container = document.createElement("div");
    const root = createRoot(container);
    await act(async () => { root.render(<ResumeEditor />); });
    await act(async () => { await Promise.resolve(); });
    await act(async () => { Array.from(container.querySelectorAll("button")).find((button) => button.textContent === "Save Changes").click(); });
    expect(container.querySelector('[data-testid="resume-save-result"]').textContent).toContain("87%");
    await act(async () => { root.unmount(); });
    global.requestAnimationFrame = raf;
  });

  it("returns to the previous Align Your Resume step without replacing browser history", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    axios.get.mockResolvedValue({ data: { resume, match_score: 50, missing_skills: [] } });
    const history = { back: jest.fn() };
    Object.defineProperty(window, "history", { configurable: true, value: history });
    const back = history.back;
    const container = document.createElement("div");
    const root = createRoot(container);
    await act(async () => { root.render(<ResumeEditor />); });
    await act(async () => { await Promise.resolve(); });
    await act(async () => { container.querySelector('[data-testid="resume-editor-back"]').click(); });
    expect(back).toHaveBeenCalledTimes(1);
    await act(async () => { root.unmount(); });
  });

  it("downloads the persisted updated-resume endpoint without duplicating the API prefix", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    axios.get.mockResolvedValue({ data: { resume, match_score: 50, missing_skills: [] } });
    axios.post
      .mockResolvedValueOnce({ data: {
      match_score: 60,
      profile: { candidate_id: "candidate-1", keySkills: ["Python", "FastAPI"] },
      resume_download_url: "/candidate/candidate-1/resume/updated/download",
      } })
      .mockResolvedValueOnce({ data: { claim_id: "claim-next" } });
    axios.get.mockResolvedValueOnce({ data: { resume, match_score: 50, missing_skills: [] } })
      .mockResolvedValueOnce({ data: new Blob(["pdf"]) });
    const originalCreateObjectURL = URL.createObjectURL;
    const originalRevokeObjectURL = URL.revokeObjectURL;
    URL.createObjectURL = jest.fn(() => "blob:resume");
    URL.revokeObjectURL = jest.fn();
    const open = jest.spyOn(window, "open").mockImplementation(() => null);
    const container = document.createElement("div");
    const root = createRoot(container);
    await act(async () => { root.render(<ResumeEditor />); });
    await act(async () => { await Promise.resolve(); });
    await act(async () => { container.querySelector("button").click(); });
    const downloadButton = Array.from(container.querySelectorAll("button"))
      .find((button) => button.textContent === "Download Updated Resume");
    await act(async () => { downloadButton.click(); });
    expect(axios.get).toHaveBeenCalledWith(
      expect.stringMatching(/\/api\/candidate\/candidate-1\/resume\/updated\/download$/),
      { responseType: "blob" },
    );
    await act(async () => { root.unmount(); });
    open.mockRestore();
    URL.createObjectURL = originalCreateObjectURL;
    URL.revokeObjectURL = originalRevokeObjectURL;
  });
});
