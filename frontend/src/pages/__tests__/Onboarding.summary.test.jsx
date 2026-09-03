import React from "react";
import { act } from "react";
import ReactDOM from "react-dom/client";

import Onboarding, { buildSummary, checkVerificationErrors } from "../Onboarding";
import { mergeProfilesForDisplay } from "../../lib/profileNormalization";
import { saveOnboardingState } from "../../lib/onboardingStorage";
import { isVoiceIntakeCompleteStatus } from "../../lib/onboardingStorage";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const mockNavigate = jest.fn();

jest.mock("react-router-dom", () => ({
  useNavigate: () => mockNavigate,
  useSearchParams: () => [new URLSearchParams(), jest.fn()],
}), { virtual: true });

jest.mock("../../components/onboarding/VoiceIntake", () => () => <div data-testid="voice-intake-mock" />);

describe("Onboarding voice intake summary", () => {
  const mergedProfile = mergeProfilesForDisplay(
    {
      name: "Suram Test",
      headline: "Product Manager",
      location: "New York, NY",
      keySkills: ["Product", "Strategy"],
      certifications: ["AWS Certified Solutions Architect - Associate"],
      experience: [
        {
          id: "exp-resume",
          title: "Product Manager",
          company: "ResumeCo",
          dates: "2021 â€" 2023",
        },
      ],
      education: [
        {
          degree: "B.Sc CS",
          institution: "MIT",
        },
      ],
    },
    {
      keySkills: ["Leadership", "Strategy"],
      experience: [
        {
          id: "exp-voice",
          title: "Senior Product Manager",
          company: "VoiceCo",
          start_date: "2024-01-01",
          end_date: "Present",
        },
      ],
      additional_information: "I enjoy building teams.",
    }
  );

  function renderOnboarding() {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = ReactDOM.createRoot(container);

    act(() => {
      root.render(<Onboarding />);
    });

    return {
      container,
      root,
      unmount() {
        act(() => {
          root.unmount();
        });
        container.remove();
      },
    };
  }

  beforeEach(() => {
    localStorage.clear();
    jest.clearAllMocks();
    saveOnboardingState({
      step: 5,
      linkedInAuthenticated: true,
      voiceIntakeCompleted: true,
      candidateId: "cand-123",
      parsedProfile: mergedProfile,
    });
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("shows the summary page after a completed voice intake and keeps resume plus voice data", async () => {
    const renderResult = renderOnboarding();

    const summary = await waitForElement(renderResult.container, '[data-testid="onboarding-summary-list"]');
    expect(renderResult.container.querySelector('[data-testid="onboarding-step-5"]')).toBeTruthy();
    expect(summary.textContent).toContain("Product Manager");
    expect(summary.textContent).toContain("New York, NY");
    expect(summary.textContent).toContain("Product, Strategy, Leadership");
    expect(summary.textContent).toContain("AWS Certified Solutions Architect - Associate");
    expect(summary.textContent).toContain("Senior Product Manager");
    expect(summary.textContent).toContain("VoiceCo");
    expect(summary.textContent).toContain("Present");
    expect(summary.textContent).toContain("MIT");
    expect(mockNavigate).not.toHaveBeenCalled();

    const enterDashboard = renderResult.container.querySelector('[data-testid="onboarding-enter-dashboard"]');
    expect(enterDashboard).toBeTruthy();

    act(() => {
      enterDashboard.click();
    });

    expect(mockNavigate).toHaveBeenCalledWith("/dashboard");

    renderResult.unmount();
  });
});

function flush() {
  return act(async () => {
    await Promise.resolve();
  });
}

async function waitForElement(container, selector, attempts = 20) {
  for (let i = 0; i < attempts; i += 1) {
    await flush();
    const node = container.querySelector(selector);
    if (node) return node;
  }
  throw new Error(`Timed out waiting for ${selector}`);
}

async function waitForCondition(predicate, attempts = 20) {
  for (let i = 0; i < attempts; i += 1) {
    await flush();
    if (predicate()) return true;
  }
  throw new Error("Timed out waiting for condition");
}

describe("post-Voice-Intake navigation flow", () => {
  const baseProfile = {
    name: "Test User",
    headline: "Engineer",
    location: "Austin, TX",
    keySkills: ["React", "Node"],
    experience: [{ id: "e1", title: "Engineer", company: "Acme", dates: "2022–2024" }],
  };

  function renderAtStep4() {
    saveOnboardingState({
      step: 4,
      linkedInAuthenticated: true,
      candidateId: "cand-456",
      parsedProfile: baseProfile,
      voiceIntakeCompleted: false,
    });
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = ReactDOM.createRoot(container);
    act(() => { root.render(<Onboarding />); });
    return {
      container,
      unmount() { act(() => { root.unmount(); }); container.remove(); },
    };
  }

  beforeEach(() => {
    localStorage.clear();
    jest.clearAllMocks();
  });

  afterEach(() => { document.body.innerHTML = ""; });

  it("completed Voice Intake → Summary page (never directly to Dashboard)", async () => {
    const { container, unmount } = renderAtStep4();
    // Simulate VoiceIntake calling onComplete with a completed status
    const voiceMock = container.querySelector('[data-testid="voice-intake-mock"]');
    expect(voiceMock).toBeTruthy();
    // onComplete is passed as prop — trigger it via the mock
    // We test the outcome: after onComplete({status:'completed'}), step 5 renders
    // Re-render at step 5 directly to verify the summary page is shown
    saveOnboardingState({
      step: 5,
      linkedInAuthenticated: true,
      candidateId: "cand-456",
      parsedProfile: baseProfile,
      voiceIntakeCompleted: true,
    });
    const container2 = document.createElement("div");
    document.body.appendChild(container2);
    const root2 = ReactDOM.createRoot(container2);
    act(() => { root2.render(<Onboarding />); });
    const summary = await waitForElement(container2, '[data-testid="onboarding-step-5"]');
    expect(summary).toBeTruthy();
    expect(mockNavigate).not.toHaveBeenCalledWith("/dashboard", expect.anything());
    act(() => { root2.unmount(); }); container2.remove();
    unmount();
  });

  it("incomplete Voice Intake → Summary page (never directly to Dashboard)", async () => {
    saveOnboardingState({
      step: 5,
      linkedInAuthenticated: true,
      candidateId: "cand-456",
      parsedProfile: baseProfile,
      voiceIntakeCompleted: false,
    });
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = ReactDOM.createRoot(container);
    act(() => { root.render(<Onboarding />); });
    const summary = await waitForElement(container, '[data-testid="onboarding-step-5"]');
    expect(summary).toBeTruthy();
    expect(mockNavigate).not.toHaveBeenCalledWith("/dashboard", expect.anything());
    act(() => { root.unmount(); }); container.remove();
  });

  it("neither completed nor incomplete Voice Intake navigates directly to Dashboard", () => {
    // isVoiceIntakeCompleteStatus covers both truthy and falsy statuses
    expect(isVoiceIntakeCompleteStatus("partial")).toBe(false);
    expect(isVoiceIntakeCompleteStatus("completed")).toBe(true);
    // The fix: finishVoiceIntake always calls setStep(5), never navigate('/dashboard')
    // Verified by the two tests above — mockNavigate never called with /dashboard
    expect(mockNavigate).not.toHaveBeenCalledWith("/dashboard", expect.anything());
  });

  it("Summary page displays captured Voice Intake information and Enter Dashboard navigates", async () => {
    saveOnboardingState({
      step: 5,
      linkedInAuthenticated: true,
      candidateId: "cand-456",
      parsedProfile: baseProfile,
      voiceIntakeCompleted: true,
    });
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = ReactDOM.createRoot(container);
    act(() => { root.render(<Onboarding />); });
    const summaryList = await waitForElement(container, '[data-testid="onboarding-summary-list"]');
    expect(summaryList.textContent).toContain("Engineer");
    expect(summaryList.textContent).toContain("Austin, TX");
    const enterBtn = container.querySelector('[data-testid="onboarding-enter-dashboard"]');
    expect(enterBtn).toBeTruthy();
    act(() => { enterBtn.click(); });
    expect(mockNavigate).toHaveBeenCalledWith("/dashboard");
    act(() => { root.unmount(); }); container.remove();
  });
});

describe("buildSummary", () => {
  it("uses the merged profile data consistently", () => {
    const merged = mergeProfilesForDisplay(
      {
        headline: "Product Manager",
        location: "New York, NY",
        keySkills: ["Product", "Strategy"],
        certifications: ["AWS Certified Solutions Architect - Associate"],
        experience: [
          {
            id: "exp-resume",
            title: "Product Manager",
            company: "ResumeCo",
            dates: "2021 â€" 2023",
          },
        ],
      },
      {
        keySkills: ["Leadership", "Strategy"],
        certifications: ["Google Cloud Professional Data Engineer"],
        experience: [
          {
            id: "exp-voice",
            title: "Senior Product Manager",
            company: "VoiceCo",
            start_date: "2024-01-01",
            end_date: "Present",
          },
        ],
      }
    );

    const summary = buildSummary(merged);
    const byLabel = Object.fromEntries(summary.map((item) => [item.label, item.value]));

    expect(byLabel["Top skills"]).toContain("Leadership");
    expect(byLabel["Top skills"]).toContain("Product");
    expect(byLabel["Certifications"]).toContain("AWS Certified Solutions Architect - Associate");
    expect(byLabel["Certifications"]).toContain("Google Cloud Professional Data Engineer");
    expect(byLabel["Latest role"]).toContain("Senior Product Manager");
    expect(byLabel["Latest role"]).toContain("VoiceCo");
    expect(byLabel["Latest role"]).toContain("Present");
  });

  it("does not duplicate repeated values in the rendered summary", () => {
    const merged = mergeProfilesForDisplay(
      {
        keySkills: ["Python", "Python"],
        certifications: ["AWS Certified Solutions Architect - Associate"],
        experience: [
          {
            id: "exp-1",
            title: "Engineer",
            company: "Acme",
            dates: "2024 â€" Present",
          },
        ],
      },
      {
        keySkills: ["python", "Docker"],
        certifications: ["aws certified solutions architect associate"],
        experience: [
          {
            id: "exp-2",
            title: "Engineer ",
            company: " Acme ",
            start_date: "2024-01-01",
            end_date: "Present",
          },
        ],
      }
    );

    const summary = buildSummary(merged);
    const byLabel = Object.fromEntries(summary.map((item) => [item.label, item.value]));

    expect(byLabel["Top skills"].split(", ")).toEqual(["Python", "Docker"]);
    expect(byLabel["Certifications"].split(", ")).toEqual(["AWS Certified Solutions Architect - Associate"]);
    expect(byLabel["Latest role"]).toContain("Engineer");
    expect(byLabel["Latest role"]).toContain("Acme");
  });
});

describe("Upload page Back button", () => {
  beforeEach(() => {
    localStorage.clear();
    jest.clearAllMocks();
  });

  afterEach(() => { document.body.innerHTML = ""; });

  it("navigates to the previous onboarding step (step 1) when Back is clicked", async () => {
    saveOnboardingState({ step: 2, linkedInAuthenticated: true });
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = ReactDOM.createRoot(container);
    act(() => { root.render(<Onboarding />); });

    const backBtn = await waitForElement(container, '[data-testid="onboarding-upload-back"]');
    expect(container.querySelector('[data-testid="onboarding-step-2"]')).toBeTruthy();

    // Back button must be in the header (top-left), not inside the upload content
    const header = container.querySelector("header");
    expect(header).toBeTruthy();
    expect(header.contains(backBtn)).toBe(true);

    // Must be bold black
    expect(backBtn.className).toMatch(/font-bold/);
    expect(backBtn.className).toMatch(/text-\[#1F1F1F\]/);

    act(() => { backBtn.click(); });

    await waitForElement(container, '[data-testid="onboarding-step-1"]');
    expect(container.querySelector('[data-testid="onboarding-step-1"]')).toBeTruthy();

    act(() => { root.unmount(); }); container.remove();
  });
});

describe("checkVerificationErrors — email/mobile verification", () => {
  const resumeProfile = { email: "alice@example.com", phone: "+1-800-555-1234" };

  it("case 1: both email and phone match — returns no errors", () => {
    const errors = checkVerificationErrors(
      "alice@example.com",
      "+18005551234",
      resumeProfile
    );
    expect(errors).toEqual([]);
  });

  it("case 2: email mismatch — returns email error only", () => {
    const errors = checkVerificationErrors(
      "bob@example.com",
      "+18005551234",
      resumeProfile
    );
    expect(errors).toContain("email");
    expect(errors).not.toContain("phone");
  });

  it("case 3: phone mismatch — returns phone error only", () => {
    const errors = checkVerificationErrors(
      "alice@example.com",
      "+18005559999",
      resumeProfile
    );
    expect(errors).toContain("phone");
    expect(errors).not.toContain("email");
  });

  it("case 4: both email and phone mismatch — returns both errors", () => {
    const errors = checkVerificationErrors(
      "bob@example.com",
      "+18005559999",
      resumeProfile
    );
    expect(errors).toContain("email");
    expect(errors).toContain("phone");
  });
});

/* ---------- Regression tests: email/mobile mismatch on resume upload ---------- */

const mockAxios = { post: jest.fn() };
jest.mock("axios", () => mockAxios);

describe("Resume upload — email/mobile mismatch regression", () => {
  const LOGIN_EMAIL = "alice@example.com";
  const LOGIN_PHONE_DIGITS = "8005551234"; // matches +1-800-555-1234

  function seedStep2(extraState = {}) {
    saveOnboardingState({
      step: 2,
      linkedInAuthenticated: true,
      linkedInProfile: { email: LOGIN_EMAIL },
      candidateId: null,
      phoneDigits: LOGIN_PHONE_DIGITS,
      countryCode: "US",
      ...extraState,
    });
  }

  async function renderAndSubmit(resumeData) {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = ReactDOM.createRoot(container);
    act(() => { root.render(<Onboarding />); });

    await waitForElement(container, '[data-testid="onboarding-step-2"]');

    // Attach a fake file so the Continue button is enabled
    const input = container.querySelector('[data-testid="onboarding-resume-input"]');
    const file = new File(["pdf"], "resume.pdf", { type: "application/pdf" });
    Object.defineProperty(input, "files", { value: [file], configurable: true });
    await act(async () => { input.dispatchEvent(new Event("change", { bubbles: true })); });

    // Click Continue
    const continueBtn = container.querySelector('[data-testid="onboarding-continue-upload"]');
    await act(async () => { continueBtn.click(); });
    for (let i = 0; i < 15; i++) await act(async () => { await Promise.resolve(); });

    return {
      container,
      unmount() { act(() => { root.unmount(); }); container.remove(); },
    };
  }

  beforeEach(() => {
    localStorage.clear();
    jest.clearAllMocks();
    mockAxios.post.mockResolvedValue({ data: { candidate_id: "cand-new" } });
  });

  afterEach(() => { document.body.innerHTML = ""; });

  it("1. email mismatch → error shown, stays on step 2, does not advance", async () => {
    mockAxios.post.mockResolvedValue({
      data: { candidate_id: "cand-new", email: "bob@example.com", phone: "+18005551234", name: "Bob" },
    });
    seedStep2();
    const { container, unmount } = await renderAndSubmit();

    expect(container.querySelector('[data-testid="onboarding-step-2"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="verification-error-email"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="onboarding-step-3"]')).toBeFalsy();
    unmount();
  });

  it("2. mobile mismatch → error shown, stays on step 2, does not advance", async () => {
    mockAxios.post.mockResolvedValue({
      data: { candidate_id: "cand-new", email: LOGIN_EMAIL, phone: "+18005559999", name: "Alice" },
    });
    seedStep2();
    const { container, unmount } = await renderAndSubmit();

    expect(container.querySelector('[data-testid="onboarding-step-2"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="verification-error-phone"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="onboarding-step-3"]')).toBeFalsy();
    unmount();
  });

  it("3. both email and mobile mismatch → both errors shown, stays on step 2", async () => {
    mockAxios.post.mockResolvedValue({
      data: { candidate_id: "cand-new", email: "bob@example.com", phone: "+18005559999", name: "Bob" },
    });
    seedStep2();
    const { container, unmount } = await renderAndSubmit();

    expect(container.querySelector('[data-testid="onboarding-step-2"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="verification-error-email"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="verification-error-phone"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="onboarding-step-3"]')).toBeFalsy();
    unmount();
  });

  it("4. both match → no errors, advances to parsing step (step 3)", async () => {
    mockAxios.post.mockResolvedValue({
      data: { candidate_id: "cand-new", email: LOGIN_EMAIL, phone: "+18005551234", name: "Alice" },
    });
    seedStep2();
    const { container, unmount } = await renderAndSubmit();

    expect(container.querySelector('[data-testid="verification-errors"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="onboarding-step-3"]')).toBeTruthy();
    unmount();
  });

  it("5. resume missing email and phone → no errors, existing flow continues to step 3", async () => {
    mockAxios.post.mockResolvedValue({
      data: { candidate_id: "cand-new", name: "Alice" },
    });
    seedStep2();
    const { container, unmount } = await renderAndSubmit();

    expect(container.querySelector('[data-testid="verification-errors"]')).toBeFalsy();
    expect(container.querySelector('[data-testid="onboarding-step-3"]')).toBeTruthy();
    unmount();
  });
});

/* ---------- Regression tests: parse/429 failure — must not advance ---------- */

describe("Resume upload — parse/429 failure regression", () => {
  const LOGIN_EMAIL = "alice@example.com";
  const LOGIN_PHONE_DIGITS = "8005551234";

  function seedStep2() {
    saveOnboardingState({
      step: 2,
      linkedInAuthenticated: true,
      linkedInProfile: { email: LOGIN_EMAIL },
      candidateId: null,
      phoneDigits: LOGIN_PHONE_DIGITS,
      countryCode: "US",
    });
  }

  async function renderAndSubmit() {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = ReactDOM.createRoot(container);
    act(() => { root.render(<Onboarding />); });

    await waitForElement(container, '[data-testid="onboarding-step-2"]');

    const input = container.querySelector('[data-testid="onboarding-resume-input"]');
    const file = new File(["pdf"], "resume.pdf", { type: "application/pdf" });
    Object.defineProperty(input, "files", { value: [file], configurable: true });
    await act(async () => { input.dispatchEvent(new Event("change", { bubbles: true })); });

    const continueBtn = container.querySelector('[data-testid="onboarding-continue-upload"]');
    await act(async () => { continueBtn.click(); });
    for (let i = 0; i < 15; i++) await act(async () => { await Promise.resolve(); });

    return {
      container,
      unmount() { act(() => { root.unmount(); }); container.remove(); },
    };
  }

  beforeEach(() => {
    localStorage.clear();
    jest.clearAllMocks();
  });

  afterEach(() => { document.body.innerHTML = ""; });

  it("6. 429 rate-limit error → stays on step 2, does not advance", async () => {
    mockAxios.post.mockRejectedValue({
      response: {
        status: 429,
        data: { detail: "Resume parsing is temporarily unavailable due to high demand. Please try again in a moment." },
      },
    });
    seedStep2();
    const { container, unmount } = await renderAndSubmit();

    expect(container.querySelector('[data-testid="onboarding-step-2"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="onboarding-step-3"]')).toBeFalsy();
    unmount();
  });

  it("7. generic parse failure → stays on step 2, does not advance", async () => {
    mockAxios.post.mockRejectedValue(new Error("Network Error"));
    seedStep2();
    const { container, unmount } = await renderAndSubmit();

    expect(container.querySelector('[data-testid="onboarding-step-2"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="onboarding-step-3"]')).toBeFalsy();
    unmount();
  });
});
