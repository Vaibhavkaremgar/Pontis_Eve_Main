import React from "react";
import { act } from "react";
import ReactDOM from "react-dom/client";
import axios from "axios";

import Dashboard from "../Dashboard";
import { saveOnboardingState } from "../../lib/onboardingStorage";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

jest.mock("axios");
jest.mock("react-router-dom", () => ({
  useNavigate: () => jest.fn(),
  useSearchParams: () => [new URLSearchParams(), jest.fn()],
}), { virtual: true });
jest.mock("../../components/Sidebar", () => () => <div data-testid="sidebar" />);
jest.mock("../../components/ChatHub", () => () => <div data-testid="chat-hub" />);
jest.mock("../../components/LivingProfile", () => () => <div data-testid="living-profile" />);
jest.mock("../../components/SwipeJobCard", () => () => <div data-testid="jobs-deck" />);
jest.mock("../../components/onboarding/VoiceIntake", () => () => <div data-testid="voice-intake" />);
jest.mock("react-resizable-panels", () => ({
  PanelGroup: ({ children }) => <div>{children}</div>,
  Panel: ({ children }) => <div>{children}</div>,
  PanelResizeHandle: () => <div />,
}));
jest.mock("sonner", () => ({ Toaster: () => null, toast: { error: jest.fn(), success: jest.fn() } }));

function makeProfile(overrides = {}) {
  return {
    photo_url: null,
    name: "Test User",
    email: "test@example.com",
    phone: "5550000000",
    headline: "Engineer",
    location: "NYC",
    bio: "",
    experience: [],
    education: [],
    keySkills: ["JS"],
    experience_years: 2,
    availability: "Immediate",
    preferred_roles: [],
    certifications: [],
    additional_information: "",
    profile_strength_percent: 82,
    profile_strength_label: "Strong",
    voice_intake_resume: { status: "completed", has_open_question: false, current_question: "", next_question: "" },
    ...overrides,
  };
}

function mockRequests(profileResponses) {
  let callCount = 0;
  axios.get.mockImplementation((url) => {
    if (url.includes("/profile")) {
      const idx = Math.min(callCount, profileResponses.length - 1);
      callCount += 1;
      return Promise.resolve({ data: profileResponses[idx] });
    }
    if (url.includes("/chat")) return Promise.resolve({ data: { messages: [] } });
    if (url.includes("/opportunities") || url.includes("/notifications")) return Promise.resolve({ data: [] });
    if (url.includes("/jobs")) return Promise.resolve({ data: [] });
    if (url.includes("/documents")) return Promise.resolve({ data: { resume: null, certificates: [] } });
    return Promise.resolve({ data: {} });
  });
}

function renderDashboard() {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container);
  act(() => { root.render(<Dashboard />); });
  return {
    container,
    unmount() { act(() => { root.unmount(); }); container.remove(); },
  };
}

async function flush() {
  await act(async () => { await Promise.resolve(); });
}

async function waitFor(predicate, attempts = 30) {
  for (let i = 0; i < attempts; i++) {
    await flush();
    if (predicate()) return true;
  }
  throw new Error("Timed out");
}

beforeEach(() => {
  localStorage.clear();
  jest.clearAllMocks();
  jest.useFakeTimers();
  saveOnboardingState({ candidateId: "cand-1", isOpenToMatches: true });
});

afterEach(() => {
  jest.runAllTimers();
  jest.useRealTimers();
});

describe("Low profile strength popup — session-scoped behavior", () => {
  it("shows popup once after login when profile is below 75%", async () => {
    mockRequests([makeProfile({ profile_strength_percent: 60, profile_strength_label: "Developing" })]);
    const { container, unmount } = renderDashboard();

    await waitFor(() => container.querySelector('[data-testid="jobs-deck"]') !== null);

    // Popup not yet visible (delayed by 800ms)
    expect(container.querySelector('[data-testid="weak-profile-popup"]')).toBeNull();

    act(() => { jest.advanceTimersByTime(900); });

    expect(container.querySelector('[data-testid="weak-profile-popup"]')).not.toBeNull();
    unmount();
  });

  it("does not show popup again after dismissal even if profile stays below 75%", async () => {
    mockRequests([
      makeProfile({ profile_strength_percent: 60, profile_strength_label: "Developing" }),
      makeProfile({ profile_strength_percent: 65, profile_strength_label: "Developing" }),
    ]);
    const { container, unmount } = renderDashboard();

    await waitFor(() => container.querySelector('[data-testid="jobs-deck"]') !== null);
    act(() => { jest.advanceTimersByTime(900); });

    // Dismiss popup
    const dismissBtn = container.querySelector('[data-testid="weak-profile-dismiss-btn"]');
    expect(dismissBtn).not.toBeNull();
    act(() => { dismissBtn.click(); });

    expect(container.querySelector('[data-testid="weak-profile-popup"]')).toBeNull();

    // Simulate profile update (still below 75%) — popup must NOT reappear
    await flush();
    act(() => { jest.advanceTimersByTime(900); });

    expect(container.querySelector('[data-testid="weak-profile-popup"]')).toBeNull();
    unmount();
  });

  it("shows popup again on re-login when profile is still below 75%", async () => {
    // First session
    mockRequests([makeProfile({ profile_strength_percent: 60, profile_strength_label: "Developing" })]);
    const first = renderDashboard();
    await waitFor(() => first.container.querySelector('[data-testid="jobs-deck"]') !== null);
    act(() => { jest.advanceTimersByTime(900); });
    expect(first.container.querySelector('[data-testid="weak-profile-popup"]')).not.toBeNull();
    first.unmount(); // simulates logout (component unmounts, ref is destroyed)

    // Second session (re-login) — profile still below 75%
    jest.clearAllMocks();
    mockRequests([makeProfile({ profile_strength_percent: 60, profile_strength_label: "Developing" })]);
    const second = renderDashboard();
    await waitFor(() => second.container.querySelector('[data-testid="jobs-deck"]') !== null);
    act(() => { jest.advanceTimersByTime(900); });

    expect(second.container.querySelector('[data-testid="weak-profile-popup"]')).not.toBeNull();
    second.unmount();
  });

  it("does not show popup when profile is at or above 75%", async () => {
    mockRequests([makeProfile({ profile_strength_percent: 75, profile_strength_label: "Strong" })]);
    const { container, unmount } = renderDashboard();

    await waitFor(() => container.querySelector('[data-testid="jobs-deck"]') !== null);
    act(() => { jest.advanceTimersByTime(900); });

    expect(container.querySelector('[data-testid="weak-profile-popup"]')).toBeNull();
    unmount();
  });

  it("popup Chat with Eve click opens ChatHub, not Voice Intake, when voice intake is completed", async () => {
    // Profile is completed but weak — popup appears, candidate clicks Chat with Eve
    mockRequests([makeProfile({
      profile_strength_percent: 60,
      profile_strength_label: "Developing",
      voice_intake_resume: {
        status: "completed",
        has_open_question: false,
        current_question: "",
        next_question: "",
      },
    })]);
    const { container, unmount } = renderDashboard();

    await waitFor(() => container.querySelector('[data-testid="jobs-deck"]') !== null);
    act(() => { jest.advanceTimersByTime(900); });
    expect(container.querySelector('[data-testid="weak-profile-popup"]')).not.toBeNull();

    act(() => {
      container.querySelector('[data-testid="weak-profile-chat-btn"]').click();
    });

    // Must show ChatHub — never Voice Intake screen
    expect(container.querySelector('[data-testid="chat-hub"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="voice-intake"]')).toBeNull();
    expect(container.querySelector('[data-testid="jobs-deck"]')).toBeNull();
    unmount();
  });

  it("popup Chat with Eve click is not overridden by the routing effect after a profile refresh", async () => {
    // Completed profile: routing effect would set centerView=swipe.
    // After the user clicks Chat with Eve, a subsequent profile refresh must not revert to swipe.
    mockRequests([
      makeProfile({
        profile_strength_percent: 60,
        profile_strength_label: "Developing",
        voice_intake_resume: {
          status: "completed",
          has_open_question: false,
          current_question: "",
          next_question: "",
        },
      }),
      // Second profile response (simulates a refresh) — still completed
      makeProfile({
        profile_strength_percent: 60,
        profile_strength_label: "Developing",
        voice_intake_resume: {
          status: "completed",
          has_open_question: false,
          current_question: "",
          next_question: "",
        },
      }),
    ]);
    const { container, unmount } = renderDashboard();

    await waitFor(() => container.querySelector('[data-testid="jobs-deck"]') !== null);
    act(() => { jest.advanceTimersByTime(900); });

    // Click Chat with Eve from the popup
    act(() => {
      container.querySelector('[data-testid="weak-profile-chat-btn"]').click();
    });
    expect(container.querySelector('[data-testid="chat-hub"]')).not.toBeNull();

    // Simulate a profile refresh (e.g. photo change triggers refreshProfile)
    await act(async () => { await Promise.resolve(); });

    // centerView must remain "chat" — routing effect must not override the explicit choice
    expect(container.querySelector('[data-testid="chat-hub"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="jobs-deck"]')).toBeNull();
    expect(container.querySelector('[data-testid="voice-intake"]')).toBeNull();
    unmount();
  });
});
