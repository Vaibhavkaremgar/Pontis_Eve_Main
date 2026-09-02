import React from "react";
import { act } from "react";
import ReactDOM from "react-dom/client";
import axios from "axios";

import Dashboard from "../Dashboard";
import { saveOnboardingState } from "../../lib/onboardingStorage";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

jest.mock("axios");
let lastLivingProfileProps = null;
let mockVoiceIntakeOnComplete = null;
jest.mock("react-router-dom", () => ({
  MemoryRouter: ({ children }) => <div>{children}</div>,
  BrowserRouter: ({ children }) => <div>{children}</div>,
  Routes: ({ children }) => <div>{children}</div>,
  Route: ({ element }) => element ?? null,
  Navigate: () => null,
  useNavigate: () => jest.fn(),
  useSearchParams: () => [new URLSearchParams(), jest.fn()],
}), { virtual: true });

jest.mock("../../components/Sidebar", () => () => <div data-testid="sidebar" />);
let mockChatHubOnMicClick = null;
jest.mock("../../components/ChatHub", () => ({ chats = [], onMicClick }) => (
  <div data-testid="chat-hub">
    {chats[0]?.content || ""}
    {onMicClick && (
      <button data-testid="chat-mic-btn" onClick={onMicClick}>Mic</button>
    )}
  </div>
));
let mockVoiceIntakeCompletionResult = null;
jest.mock("../../components/LivingProfile", () => (props) => {
  lastLivingProfileProps = props;
  return (
    <div data-testid="living-profile" data-candidate-id={props.userProfile?.candidate_id || ""}>
      <button data-testid="refresh-profile-btn" onClick={() => props.onPhotoChange("new-photo-url")}>
        Refresh profile
      </button>
    </div>
  );
});
jest.mock("../../components/SwipeJobCard", () => () => <div data-testid="jobs-deck" />);
jest.mock("../../components/onboarding/VoiceIntake", () => (props) => {
  mockVoiceIntakeOnComplete = props.onComplete;
  return <div data-testid="voice-intake" />;
});
jest.mock("react-resizable-panels", () => ({
  PanelGroup: ({ children }) => <div>{children}</div>,
  Panel: ({ children }) => <div>{children}</div>,
  PanelResizeHandle: ({ children }) => <div>{children}</div>,
}));
jest.mock("sonner", () => ({
  Toaster: () => null,
  toast: {
    error: jest.fn(),
    success: jest.fn(),
  },
}));

const currentQuestion =
  "Are there specific industries or types of companies you'd prefer to work with in your next role?";

function makeProfile(overrides = {}) {
  return {
    photo_url: null,
    name: "Suram Test",
    email: "suram@example.com",
    phone: "5551234567",
    headline: "Product Manager",
    location: "New York, NY",
    bio: "",
    experience: [],
    education: [],
    keySkills: ["Product", "Strategy"],
    experience_years: 5,
    availability: "Immediate",
    preferred_roles: ["Product Manager"],
    certifications: [],
    additional_information: "",
    profile_strength_percent: 82,
    profile_strength_label: "Strong",
    voice_intake_resume: {
      status: "completed",
      has_open_question: false,
      current_question: "",
      next_question: "",
    },
    ...overrides,
  };
}

function mockDashboardRequests(profileResponses) {
  let profileCallCount = 0;

  axios.get.mockImplementation((url) => {
    if (url.includes("/profile")) {
      const index = Math.min(profileCallCount, profileResponses.length - 1);
      profileCallCount += 1;
      return Promise.resolve({ data: profileResponses[index] });
    }

    if (url.includes("/chat")) {
      return Promise.resolve({ data: { messages: [] } });
    }

    if (url.includes("/opportunities") || url.includes("/notifications")) {
      return Promise.resolve({ data: [] });
    }

    if (url.includes("/jobs")) {
      return Promise.resolve({ data: [{ id: "job-1", company: "Acme" }] });
    }

    if (url.includes("/documents")) {
      return Promise.resolve({ data: { resume: null, certificates: [] } });
    }

    return Promise.resolve({ data: {} });
  });
}

function renderDashboard() {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container);

  act(() => {
    root.render(<Dashboard />);
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

async function flush() {
  await act(async () => {
    await Promise.resolve();
  });
}

async function waitForSelector(container, selector, attempts = 20) {
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

describe("Dashboard voice intake routing", () => {
  let renderResult;

  beforeEach(() => {
    localStorage.clear();
    jest.clearAllMocks();
    lastLivingProfileProps = null;
    mockVoiceIntakeCompletionResult = null;
    mockVoiceIntakeOnComplete = null;
    mockChatHubOnMicClick = null;
    saveOnboardingState({
      candidateId: "cand-123",
      voiceIntakeCompleted: true,
      isOpenToMatches: true,
    });
  });

  afterEach(() => {
    renderResult?.unmount?.();
    renderResult = null;
  });

  it("shows Jobs for you by default when the backend profile is completed", async () => {
    mockDashboardRequests([makeProfile()]);

    renderResult = renderDashboard();

    expect(await waitForSelector(renderResult.container, '[data-testid="jobs-deck"]')).toBeTruthy();
    expect(renderResult.container.querySelector('[data-testid="chat-hub"]')).toBeNull();
    expect(renderResult.container.querySelector('[data-testid="voice-intake"]')).toBeNull();
  });

  it("opens Chat with Eve and restores the current question when the backend profile is in progress", async () => {
    mockDashboardRequests([
      makeProfile({
        voice_intake_resume: {
          status: "in_progress",
          has_open_question: true,
          current_question: currentQuestion,
          next_question: "",
        },
      }),
    ]);

    renderResult = renderDashboard();

    const chatHub = await waitForSelector(renderResult.container, '[data-testid="chat-hub"]');
    expect(chatHub.textContent).toContain(currentQuestion);
    expect(renderResult.container.querySelector('[data-testid="jobs-deck"]')).toBeNull();
    expect(renderResult.container.querySelector('[data-testid="voice-intake"]')).toBeNull();
  });

  it("does not let stale localStorage completed state override a backend in-progress resume", async () => {
    mockDashboardRequests([
      makeProfile({
        voice_intake_resume: {
          status: "in_progress",
          has_open_question: true,
          current_question: currentQuestion,
          next_question: "",
        },
      }),
    ]);

    saveOnboardingState({
      candidateId: "cand-123",
      voiceIntakeCompleted: true,
      isOpenToMatches: true,
    });

    renderResult = renderDashboard();

    const chatHub = await waitForSelector(renderResult.container, '[data-testid="chat-hub"]');
    expect(chatHub.textContent).toContain(currentQuestion);
    expect(renderResult.container.querySelector('[data-testid="jobs-deck"]')).toBeNull();
  });

  it("switches back to Jobs for you after a refreshed profile reports voice intake completed", async () => {
    mockDashboardRequests([
      makeProfile({
        voice_intake_resume: {
          status: "in_progress",
          has_open_question: true,
          current_question: currentQuestion,
          next_question: "",
        },
      }),
      makeProfile({
        voice_intake_resume: {
          status: "completed",
          has_open_question: false,
          current_question: "",
          next_question: "",
        },
      }),
    ]);

    renderResult = renderDashboard();

    const chatHub = await waitForSelector(renderResult.container, '[data-testid="chat-hub"]');
    expect(chatHub.textContent).toContain(currentQuestion);

    const refreshBtn = renderResult.container.querySelector('[data-testid="refresh-profile-btn"]');
    expect(refreshBtn).toBeTruthy();

    act(() => {
      refreshBtn.click();
    });

    expect(await waitForSelector(renderResult.container, '[data-testid="jobs-deck"]')).toBeTruthy();
    expect(renderResult.container.querySelector('[data-testid="voice-intake"]')).toBeNull();
  });

  it("routes an incomplete voice intake back to Chat with Eve", async () => {
    mockVoiceIntakeCompletionResult = { status: "in_progress" };
    mockDashboardRequests([
      makeProfile({
        strengthPercent: 62,
        profile_strength_percent: 62,
        voice_intake_resume: null,
      }),
      makeProfile({
        strengthPercent: 62,
        profile_strength_percent: 62,
        voice_intake_resume: {
          status: "in_progress",
          has_open_question: true,
          current_question: currentQuestion,
          next_question: "",
        },
      }),
    ]);

    renderResult = renderDashboard();

    const chatToggle = Array.from(renderResult.container.querySelectorAll("button")).find((button) =>
      button.textContent?.includes("Chat with Eve")
    );
    expect(chatToggle).toBeTruthy();

    act(() => {
      chatToggle.click();
    });

    await act(async () => {
      mockVoiceIntakeOnComplete?.(mockVoiceIntakeCompletionResult);
      await Promise.resolve();
    });

    expect(await waitForSelector(renderResult.container, '[data-testid="chat-hub"]')).toBeTruthy();
    expect(renderResult.container.querySelector('[data-testid="jobs-deck"]')).toBeNull();
  });

  it("routes a completed voice intake to Jobs for you", async () => {
    mockVoiceIntakeCompletionResult = { status: "completed" };
    mockDashboardRequests([
      makeProfile({
        strengthPercent: 62,
        profile_strength_percent: 62,
        voice_intake_resume: null,
      }),
      makeProfile({
        strengthPercent: 82,
        profile_strength_percent: 82,
        voice_intake_resume: {
          status: "completed",
          has_open_question: false,
          current_question: "",
          next_question: "",
        },
      }),
    ]);

    renderResult = renderDashboard();

    const chatToggle = Array.from(renderResult.container.querySelectorAll("button")).find((button) =>
      button.textContent?.includes("Chat with Eve")
    );
    expect(chatToggle).toBeTruthy();

    act(() => {
      chatToggle.click();
    });

    await act(async () => {
      mockVoiceIntakeOnComplete?.(mockVoiceIntakeCompletionResult);
      await Promise.resolve();
    });

    expect(await waitForSelector(renderResult.container, '[data-testid="jobs-deck"]')).toBeTruthy();
    expect(renderResult.container.querySelector('[data-testid="chat-hub"]')).toBeNull();
  });

  it("preserves the backend candidate id on the profile object so photo uploads can use it", async () => {
    mockDashboardRequests([
      makeProfile({
        candidate_id: "cand-456",
      }),
    ]);

    renderResult = renderDashboard();

    await waitForSelector(renderResult.container, '[data-testid="living-profile"]');

    expect(lastLivingProfileProps).toBeTruthy();
    expect(lastLivingProfileProps.userProfile.candidate_id).toBe("cand-456");
    expect(lastLivingProfileProps.userProfile.candidateId).toBe("cand-456");
  });

  it("keeps Voice Intake additions in the dashboard profile after refresh", async () => {
    mockDashboardRequests([
      makeProfile({
        keySkills: ["Product", "Strategy", "Leadership"],
        certifications: ["AWS Certified Solutions Architect - Associate"],
        experience: [
          {
            id: "exp-voice",
            title: "Senior Product Manager",
            company: "VoiceCo",
            dates: "2024 â€” Present",
            description: "Led platform expansion.",
          },
        ],
        voice_intake_resume: null,
      }),
    ]);

    renderResult = renderDashboard();
    await waitForCondition(
      () => Array.isArray(lastLivingProfileProps?.userProfile?.keySkills) &&
        lastLivingProfileProps.userProfile.keySkills.length > 0
    );
    expect(lastLivingProfileProps.userProfile.keySkills).toEqual(["Product", "Strategy", "Leadership"]);
    expect(lastLivingProfileProps.userProfile.certifications).toEqual(["AWS Certified Solutions Architect - Associate"]);
    expect(lastLivingProfileProps.userProfile.experience.map((exp) => exp.company)).toEqual(["VoiceCo"]);
  });
});

describe("Voice Intake completion/routing regression tests", () => {
  let renderResult;

  beforeEach(() => {
    localStorage.clear();
    jest.clearAllMocks();
    lastLivingProfileProps = null;
    mockVoiceIntakeOnComplete = null;
    mockChatHubOnMicClick = null;
  });

  afterEach(() => {
    renderResult?.unmount?.();
    renderResult = null;
  });

  it("backend returns completed while localStorage says incomplete → Jobs for you", async () => {
    // localStorage says incomplete, backend says completed
    saveOnboardingState({
      candidateId: "cand-123",
      voiceIntakeCompleted: false,
    });
    mockDashboardRequests([
      makeProfile({
        voice_intake_resume: {
          status: "completed",
          has_open_question: false,
          current_question: "",
          next_question: "",
        },
      }),
    ]);

    renderResult = renderDashboard();

    expect(await waitForSelector(renderResult.container, '[data-testid="jobs-deck"]')).toBeTruthy();
    expect(renderResult.container.querySelector('[data-testid="chat-hub"]')).toBeNull();
  });

  it("backend returns completed while voice_intake_resume says in_progress → Jobs for you", async () => {
    // First profile call returns in_progress, second (after refresh) returns completed
    saveOnboardingState({ candidateId: "cand-123", voiceIntakeCompleted: false });
    mockDashboardRequests([
      makeProfile({
        voice_intake_resume: {
          status: "in_progress",
          has_open_question: true,
          current_question: currentQuestion,
          next_question: "",
        },
      }),
      makeProfile({
        voice_intake_resume: {
          status: "completed",
          has_open_question: false,
          current_question: "",
          next_question: "",
        },
      }),
    ]);

    renderResult = renderDashboard();

    // Initially shows chat (in_progress)
    await waitForSelector(renderResult.container, '[data-testid="chat-hub"]');

    // Trigger refresh (simulates VoiceIntake onComplete calling refreshProfile)
    const refreshBtn = renderResult.container.querySelector('[data-testid="refresh-profile-btn"]');
    act(() => { refreshBtn.click(); });

    // After refresh with completed status → Jobs for you
    expect(await waitForSelector(renderResult.container, '[data-testid="jobs-deck"]')).toBeTruthy();
    expect(renderResult.container.querySelector('[data-testid="chat-hub"]')).toBeNull();
  });

  it("backend returns in_progress → Chat with Eve", async () => {
    saveOnboardingState({ candidateId: "cand-123", voiceIntakeCompleted: true });
    mockDashboardRequests([
      makeProfile({
        voice_intake_resume: {
          status: "in_progress",
          has_open_question: true,
          current_question: currentQuestion,
          next_question: "",
        },
      }),
    ]);

    renderResult = renderDashboard();

    const chatHub = await waitForSelector(renderResult.container, '[data-testid="chat-hub"]');
    expect(chatHub.textContent).toContain(currentQuestion);
    expect(renderResult.container.querySelector('[data-testid="jobs-deck"]')).toBeNull();
  });

  it("completed Voice Intake onComplete → refreshProfile → Jobs for you (never bypasses via local status)", async () => {
    saveOnboardingState({ candidateId: "cand-123", voiceIntakeCompleted: false });
    mockDashboardRequests([
      makeProfile({ voice_intake_resume: null }),
      makeProfile({
        voice_intake_resume: {
          status: "completed",
          has_open_question: false,
          current_question: "",
          next_question: "",
        },
      }),
    ]);

    renderResult = renderDashboard();

    // Click Chat with Eve to show voice intake
    const chatToggle = Array.from(renderResult.container.querySelectorAll("button")).find(
      (b) => b.textContent?.includes("Chat with Eve")
    );
    act(() => { chatToggle.click(); });

    // Simulate VoiceIntake calling onComplete with completed status
    await act(async () => {
      mockVoiceIntakeOnComplete?.({ status: "completed" });
      await Promise.resolve();
    });

    // After refreshProfile resolves with completed backend status → Jobs for you
    expect(await waitForSelector(renderResult.container, '[data-testid="jobs-deck"]')).toBeTruthy();
    expect(renderResult.container.querySelector('[data-testid="chat-hub"]')).toBeNull();
  });

  it("incomplete Voice Intake onComplete → refreshProfile → Chat with Eve", async () => {
    saveOnboardingState({ candidateId: "cand-123", voiceIntakeCompleted: false });
    mockDashboardRequests([
      makeProfile({ voice_intake_resume: null }),
      makeProfile({
        voice_intake_resume: {
          status: "in_progress",
          has_open_question: true,
          current_question: currentQuestion,
          next_question: "",
        },
      }),
    ]);

    renderResult = renderDashboard();

    const chatToggle = Array.from(renderResult.container.querySelectorAll("button")).find(
      (b) => b.textContent?.includes("Chat with Eve")
    );
    act(() => { chatToggle.click(); });

    await act(async () => {
      mockVoiceIntakeOnComplete?.({ status: "in_progress" });
      await Promise.resolve();
    });

    expect(await waitForSelector(renderResult.container, '[data-testid="chat-hub"]')).toBeTruthy();
    expect(renderResult.container.querySelector('[data-testid="jobs-deck"]')).toBeNull();
  });

  it("call ended during/before Eve's introduction (no candidate speech) → Chat with Eve UI", async () => {
    // Candidate starts call, Eve begins speaking, candidate hangs up before saying anything
    saveOnboardingState({ candidateId: "cand-123", voiceIntakeCompleted: false });
    mockDashboardRequests([
      makeProfile({ voice_intake_resume: null }),
      makeProfile({ voice_intake_resume: null }),
    ]);

    renderResult = renderDashboard();

    const chatToggle = Array.from(renderResult.container.querySelectorAll("button")).find(
      (b) => b.textContent?.includes("Chat with Eve")
    );
    act(() => { chatToggle.click(); });

    // Simulate VoiceIntake calling onComplete with no_interaction status
    // (triggered when call ends with no candidate speech)
    await act(async () => {
      mockVoiceIntakeOnComplete?.({ status: "no_interaction" });
      await Promise.resolve();
    });

    // Must show normal ChatHub with suggestion chips and input — never voice-intake screen
    expect(await waitForSelector(renderResult.container, '[data-testid="chat-hub"]')).toBeTruthy();
    expect(renderResult.container.querySelector('[data-testid="voice-intake"]')).toBeNull();
    expect(renderResult.container.querySelector('[data-testid="jobs-deck"]')).toBeNull();
  });

  it("call ended with 0 answers → normal Chat with Eve UI (no voice UI)", async () => {
    // Backend returns null voice_intake_resume (no data captured)
    saveOnboardingState({ candidateId: "cand-123", voiceIntakeCompleted: false });
    mockDashboardRequests([
      makeProfile({ voice_intake_resume: null }),
      makeProfile({ voice_intake_resume: null }),
    ]);

    renderResult = renderDashboard();

    const chatToggle = Array.from(renderResult.container.querySelectorAll("button")).find(
      (b) => b.textContent?.includes("Chat with Eve")
    );
    act(() => { chatToggle.click(); });

    // Simulate call ended with no answers (empty/partial result)
    await act(async () => {
      mockVoiceIntakeOnComplete?.({ status: "partial" });
      await Promise.resolve();
    });

    // Must show ChatHub, not voice-intake, not jobs-deck
    expect(await waitForSelector(renderResult.container, '[data-testid="chat-hub"]')).toBeTruthy();
    expect(renderResult.container.querySelector('[data-testid="voice-intake"]')).toBeNull();
    expect(renderResult.container.querySelector('[data-testid="jobs-deck"]')).toBeNull();
  });

  it("call ended after partial answers → normal Chat with Eve UI (no voice UI)", async () => {
    // Backend returns in_progress but without has_open_question (partial capture)
    saveOnboardingState({ candidateId: "cand-123", voiceIntakeCompleted: false });
    mockDashboardRequests([
      makeProfile({ voice_intake_resume: null }),
      makeProfile({
        voice_intake_resume: {
          status: "in_progress",
          has_open_question: false,
          current_question: "",
          next_question: "",
        },
      }),
    ]);

    renderResult = renderDashboard();

    const chatToggle = Array.from(renderResult.container.querySelectorAll("button")).find(
      (b) => b.textContent?.includes("Chat with Eve")
    );
    act(() => { chatToggle.click(); });

    await act(async () => {
      mockVoiceIntakeOnComplete?.({ status: "in_progress" });
      await Promise.resolve();
    });

    expect(await waitForSelector(renderResult.container, '[data-testid="chat-hub"]')).toBeTruthy();
    expect(renderResult.container.querySelector('[data-testid="voice-intake"]')).toBeNull();
    expect(renderResult.container.querySelector('[data-testid="jobs-deck"]')).toBeNull();
  });

  it("completed Voice Intake → existing completed flow routes to Jobs for you", async () => {
    saveOnboardingState({ candidateId: "cand-123", voiceIntakeCompleted: false });
    mockDashboardRequests([
      makeProfile({ voice_intake_resume: null }),
      makeProfile({
        voice_intake_resume: {
          status: "completed",
          has_open_question: false,
          current_question: "",
          next_question: "",
        },
      }),
    ]);

    renderResult = renderDashboard();

    const chatToggle = Array.from(renderResult.container.querySelectorAll("button")).find(
      (b) => b.textContent?.includes("Chat with Eve")
    );
    act(() => { chatToggle.click(); });

    await act(async () => {
      mockVoiceIntakeOnComplete?.({ status: "completed" });
      await Promise.resolve();
    });

    expect(await waitForSelector(renderResult.container, '[data-testid="jobs-deck"]')).toBeTruthy();
    expect(renderResult.container.querySelector('[data-testid="voice-intake"]')).toBeNull();
    expect(renderResult.container.querySelector('[data-testid="chat-hub"]')).toBeNull();
  });

  it("refreshing the dashboard after completed Voice Intake still shows Jobs for you", async () => {
    // Simulate a reload: localStorage has voiceIntakeCompleted=true, backend confirms completed
    saveOnboardingState({
      candidateId: "cand-123",
      voiceIntakeCompleted: true,
    });
    mockDashboardRequests([
      makeProfile({
        voice_intake_resume: {
          status: "completed",
          has_open_question: false,
          current_question: "",
          next_question: "",
        },
      }),
    ]);

    renderResult = renderDashboard();

    expect(await waitForSelector(renderResult.container, '[data-testid="jobs-deck"]')).toBeTruthy();
    expect(renderResult.container.querySelector('[data-testid="chat-hub"]')).toBeNull();
    expect(renderResult.container.querySelector('[data-testid="voice-intake"]')).toBeNull();
  });
});

describe("Dashboard mic button — voice intake from chat panel", () => {
  let renderResult;

  beforeEach(() => {
    localStorage.clear();
    jest.clearAllMocks();
    lastLivingProfileProps = null;
    mockVoiceIntakeOnComplete = null;
    mockChatHubOnMicClick = null;
    saveOnboardingState({
      candidateId: "cand-123",
      voiceIntakeCompleted: false,
      isOpenToMatches: true,
    });
  });

  afterEach(() => {
    renderResult?.unmount?.();
    renderResult = null;
  });

  it("mic button is present in ChatHub when centerView is chat", async () => {
    mockDashboardRequests([
      makeProfile({
        voice_intake_resume: {
          status: "in_progress",
          has_open_question: true,
          current_question: currentQuestion,
          next_question: "",
        },
      }),
    ]);
    renderResult = renderDashboard();

    await waitForSelector(renderResult.container, '[data-testid="chat-hub"]');
    expect(renderResult.container.querySelector('[data-testid="chat-mic-btn"]')).toBeTruthy();
  });

  it("clicking mic button shows VoiceIntake inline and hides ChatHub", async () => {
    mockDashboardRequests([
      makeProfile({
        voice_intake_resume: {
          status: "in_progress",
          has_open_question: true,
          current_question: currentQuestion,
          next_question: "",
        },
      }),
    ]);
    renderResult = renderDashboard();

    await waitForSelector(renderResult.container, '[data-testid="chat-hub"]');
    act(() => {
      renderResult.container.querySelector('[data-testid="chat-mic-btn"]').click();
    });

    expect(await waitForSelector(renderResult.container, '[data-testid="voice-intake"]')).toBeTruthy();
    expect(renderResult.container.querySelector('[data-testid="chat-hub"]')).toBeNull();
    expect(renderResult.container.querySelector('[data-testid="jobs-deck"]')).toBeNull();
  });

  it("after mic voice intake completes, stays in dashboard (no summary) and shows ChatHub", async () => {
    mockDashboardRequests([
      makeProfile({
        voice_intake_resume: {
          status: "in_progress",
          has_open_question: true,
          current_question: currentQuestion,
          next_question: "",
        },
      }),
      makeProfile({
        bio: "Updated bio from voice",
        voice_intake_resume: {
          status: "in_progress",
          has_open_question: true,
          current_question: currentQuestion,
          next_question: "",
        },
      }),
    ]);
    renderResult = renderDashboard();

    await waitForSelector(renderResult.container, '[data-testid="chat-hub"]');
    act(() => {
      renderResult.container.querySelector('[data-testid="chat-mic-btn"]').click();
    });
    await waitForSelector(renderResult.container, '[data-testid="voice-intake"]');

    await act(async () => {
      mockVoiceIntakeOnComplete?.({ status: "completed", profile: { bio: "Updated bio from voice" } });
      await Promise.resolve();
    });

    expect(await waitForSelector(renderResult.container, '[data-testid="chat-hub"]')).toBeTruthy();
    expect(renderResult.container.querySelector('[data-testid="voice-intake"]')).toBeNull();
    expect(renderResult.container.querySelector('[data-testid="jobs-deck"]')).toBeNull();
  });

  it("profile panel refreshes after mic voice intake completes with profile_updates", async () => {
    mockDashboardRequests([
      makeProfile({
        keySkills: ["Product"],
        voice_intake_resume: {
          status: "in_progress",
          has_open_question: true,
          current_question: currentQuestion,
          next_question: "",
        },
      }),
      makeProfile({
        keySkills: ["Product", "Leadership", "Strategy"],
        voice_intake_resume: {
          status: "in_progress",
          has_open_question: true,
          current_question: currentQuestion,
          next_question: "",
        },
      }),
    ]);
    renderResult = renderDashboard();

    await waitForSelector(renderResult.container, '[data-testid="chat-hub"]');
    act(() => {
      renderResult.container.querySelector('[data-testid="chat-mic-btn"]').click();
    });
    await waitForSelector(renderResult.container, '[data-testid="voice-intake"]');

    await act(async () => {
      mockVoiceIntakeOnComplete?.({
        status: "completed",
        profile_updates: { keySkills: ["Product", "Leadership", "Strategy"] },
      });
      await Promise.resolve();
    });

    await waitForCondition(
      () =>
        Array.isArray(lastLivingProfileProps?.userProfile?.keySkills) &&
        lastLivingProfileProps.userProfile.keySkills.length >= 3
    );
    expect(lastLivingProfileProps.userProfile.keySkills).toContain("Leadership");
  });

  it("mic voice intake with partial result stays in ChatHub (not jobs, not voice)", async () => {
    mockDashboardRequests([
      makeProfile({
        voice_intake_resume: {
          status: "in_progress",
          has_open_question: true,
          current_question: currentQuestion,
          next_question: "",
        },
      }),
      makeProfile({
        voice_intake_resume: {
          status: "in_progress",
          has_open_question: true,
          current_question: currentQuestion,
          next_question: "",
        },
      }),
    ]);
    renderResult = renderDashboard();

    await waitForSelector(renderResult.container, '[data-testid="chat-hub"]');
    act(() => {
      renderResult.container.querySelector('[data-testid="chat-mic-btn"]').click();
    });
    await waitForSelector(renderResult.container, '[data-testid="voice-intake"]');

    await act(async () => {
      mockVoiceIntakeOnComplete?.({ status: "partial" });
      await Promise.resolve();
    });

    expect(await waitForSelector(renderResult.container, '[data-testid="chat-hub"]')).toBeTruthy();
    expect(renderResult.container.querySelector('[data-testid="voice-intake"]')).toBeNull();
    expect(renderResult.container.querySelector('[data-testid="jobs-deck"]')).toBeNull();
  });
});
