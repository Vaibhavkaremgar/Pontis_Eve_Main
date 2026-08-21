import React from "react";
import { act } from "react";
import ReactDOM from "react-dom/client";
import axios from "axios";

import Dashboard from "../Dashboard";
import { saveOnboardingState } from "../../lib/onboardingStorage";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

jest.mock("axios");
let lastLivingProfileProps = null;
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
jest.mock("../../components/ChatHub", () => ({ chats = [] }) => (
  <div data-testid="chat-hub">{chats[0]?.content || ""}</div>
));
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
jest.mock("../../components/onboarding/VoiceIntake", () => () => <div data-testid="voice-intake" />);
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

describe("Dashboard voice intake routing", () => {
  let renderResult;

  beforeEach(() => {
    localStorage.clear();
    jest.clearAllMocks();
    lastLivingProfileProps = null;
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
});
