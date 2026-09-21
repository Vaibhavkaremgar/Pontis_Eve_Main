import React, { act } from "react";
import { createRoot } from "react-dom/client";
import axios from "axios";
import ResumeEditor from "../ResumeEditor";

jest.mock("axios");
jest.mock("react-router-dom", () => ({ useSearchParams: () => [new URLSearchParams("candidate_id=candidate-1&recommendation_id=rec-1")] }), { virtual: true });

const resume = { name: "Candidate", headline: "Engineer", skills: ["Python", "FastAPI"], work_experience: [], education: [], certifications: [], projects: [] };

describe("ResumeEditor profile refresh", () => {
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

  it("downloads the persisted updated-resume endpoint without duplicating the API prefix", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    axios.get.mockResolvedValue({ data: { resume, match_score: 50, missing_skills: [] } });
    axios.post.mockResolvedValue({ data: {
      match_score: 60,
      profile: { candidate_id: "candidate-1", keySkills: ["Python", "FastAPI"] },
      resume_download_url: "/candidate/candidate-1/resume/updated/download",
    } });
    const open = jest.spyOn(window, "open").mockImplementation(() => null);
    const container = document.createElement("div");
    const root = createRoot(container);
    await act(async () => { root.render(<ResumeEditor />); });
    await act(async () => { await Promise.resolve(); });
    await act(async () => { container.querySelector("button").click(); });
    const downloadButton = Array.from(container.querySelectorAll("button"))
      .find((button) => button.textContent === "Download Updated Resume");
    await act(async () => { downloadButton.click(); });
    expect(open).toHaveBeenCalledWith(
      expect.stringMatching(/\/api\/candidate\/candidate-1\/resume\/updated\/download$/),
      "_blank",
      "noopener,noreferrer",
    );
    await act(async () => { root.unmount(); });
    open.mockRestore();
  });
});
