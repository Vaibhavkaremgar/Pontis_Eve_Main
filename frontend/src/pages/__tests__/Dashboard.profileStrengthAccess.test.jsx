import React from "react";
import { act } from "react";
import ReactDOM from "react-dom/client";
import axios from "axios";

import Dashboard from "../Dashboard";
import { saveOnboardingState } from "../../lib/onboardingStorage";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

jest.mock("axios");
let latestProfile = null;
jest.mock("react-router-dom", () => ({
  useNavigate: () => jest.fn(),
  useSearchParams: () => [new URLSearchParams(), jest.fn()],
}), { virtual: true });
jest.mock("../../components/Sidebar", () => () => <div />);
jest.mock("../../components/ChatHub", () => (props) => (
  <div data-testid="chat-hub">
    <input data-testid="chat-text-input" value={props.inputValue || ""} onChange={(e) => props.setInputValue(e.target.value)} />
    <button data-testid="chat-send-btn" onClick={props.onSend}>Send</button>
  </div>
));
jest.mock("../../components/LivingProfile", () => ({ onPhotoChange, userProfile }) => {
  latestProfile = userProfile;
  return <button data-testid="refresh-profile" onClick={() => onPhotoChange("photo-url")}>Refresh</button>;
});
jest.mock("../../components/SwipeJobCard", () => ({ onExhausted }) => (
  <button data-testid="jobs-deck" onClick={onExhausted}>Next job</button>
));
jest.mock("../../components/onboarding/VoiceIntake", () => () => <div data-testid="voice-intake" />);
jest.mock("react-resizable-panels", () => ({
  PanelGroup: ({ children }) => <div>{children}</div>,
  Panel: ({ children }) => <div>{children}</div>,
  PanelResizeHandle: () => <div />,
}));
jest.mock("sonner", () => {
  const toast = jest.fn();
  toast.error = jest.fn();
  toast.success = jest.fn();
  return { Toaster: () => null, toast };
});

function profile(strength) {
  return {
    candidate_id: "candidate-1",
    name: "Test Candidate",
    profile_strength_percent: strength,
    profile_strength_label: "Strong",
    voice_intake_resume: { status: "completed", has_open_question: false },
  };
}

function setInputValue(input, value) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
  setter.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

function mockRequests(profiles) {
  let profileCalls = 0;
  axios.get.mockImplementation((url) => {
    if (url.includes("/profile")) {
      const response = profiles[Math.min(profileCalls, profiles.length - 1)];
      profileCalls += 1;
      return Promise.resolve({ data: response });
    }
    if (url.includes("/chat")) return Promise.resolve({ data: { messages: [] } });
    if (url.includes("/jobs")) return Promise.resolve({ data: [] });
    if (url.includes("/documents")) return Promise.resolve({ data: { resume: null, certificates: [] } });
    return Promise.resolve({ data: [] });
  });
}

function renderDashboard() {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container);
  act(() => root.render(<Dashboard />));
  return { container, unmount: () => { act(() => root.unmount()); container.remove(); } };
}

async function waitFor(predicate, attempts = 30) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    await act(async () => { await Promise.resolve(); });
    if (predicate()) return;
  }
  throw new Error("Timed out waiting for dashboard state");
}

describe("Dashboard profile-strength jobs access", () => {
  let dashboard;

  beforeEach(() => {
    localStorage.clear();
    jest.clearAllMocks();
    latestProfile = null;
    saveOnboardingState({ candidateId: "candidate-1", isOpenToMatches: true });
  });

  afterEach(() => dashboard?.unmount());

  it("shows only Chat with Eve below 90%", async () => {
    mockRequests([profile(89)]);
    dashboard = renderDashboard();

    await waitFor(() => dashboard.container.querySelector('[data-testid="chat-hub"]'));
    expect(dashboard.container.querySelector('[data-testid="jobs-tab"]')).toBeNull();
    expect(dashboard.container.querySelector('[data-testid="jobs-deck"]')).toBeNull();
  });

  it("unlocks Jobs for you at exactly 90%", async () => {
    mockRequests([profile(90)]);
    dashboard = renderDashboard();

    await waitFor(() => dashboard.container.querySelector('[data-testid="jobs-deck"]'));
    expect(dashboard.container.querySelector('[data-testid="jobs-tab"]')).not.toBeNull();
    expect(dashboard.container.querySelector('[data-testid="chat-tab"]')).not.toBeNull();
  });

  it("shows both tabs above 90%", async () => {
    mockRequests([profile(91)]);
    dashboard = renderDashboard();

    await waitFor(() => dashboard.container.querySelector('[data-testid="jobs-deck"]'));
    expect(dashboard.container.querySelector('[data-testid="jobs-tab"]')).not.toBeNull();
    expect(dashboard.container.querySelector('[data-testid="chat-tab"]')).not.toBeNull();
  });

  it("hides Jobs for you again when a refreshed profile drops below 90%", async () => {
    mockRequests([profile(90), profile(89)]);
    dashboard = renderDashboard();

    await waitFor(() => dashboard.container.querySelector('[data-testid="jobs-deck"]'));
    act(() => dashboard.container.querySelector('[data-testid="refresh-profile"]').click());

    await waitFor(() => dashboard.container.querySelector('[data-testid="chat-hub"]'));
    expect(dashboard.container.querySelector('[data-testid="jobs-tab"]')).toBeNull();
    expect(dashboard.container.querySelector('[data-testid="jobs-deck"]')).toBeNull();
  });

  it("refreshes persisted strength and Bio after Eve saves an update, unlocking Jobs at 90%", async () => {
    const savedBio = "Product leader building inclusive software teams.";
    mockRequests([
      { ...profile(55), bio: "" },
      { ...profile(90), bio: savedBio },
    ]);
    axios.post.mockResolvedValue({ data: { reply: "Saved.", profile_updates: { bio: savedBio } } });
    dashboard = renderDashboard();

    await waitFor(() => dashboard.container.querySelector('[data-testid="chat-hub"]'));
    act(() => {
      setInputValue(dashboard.container.querySelector('[data-testid="chat-text-input"]'), "Update my bio");
      dashboard.container.querySelector('[data-testid="chat-send-btn"]').click();
    });

    await waitFor(() => latestProfile?.strengthPercent === 90);
    expect(latestProfile.bio).toBe(savedBio);
    expect(latestProfile.strength).toBe("Strong");
    expect(dashboard.container.querySelector('[data-testid="jobs-tab"]')).toBeTruthy();
    expect(axios.get.mock.calls.filter(([url]) => url.includes("/candidate/candidate-1/profile"))).toHaveLength(2);
  });

  it("shows the subscription prompt when a fourth job is requested", async () => {
    axios.get.mockImplementation((url, config) => {
      if (url.includes("/profile")) return Promise.resolve({ data: profile(90) });
      if (url.includes("/jobs") && config?.params?.request_more) {
        return Promise.reject({ response: { status: 403, data: { detail: { code: "daily_job_limit_reached" } } } });
      }
      if (url.includes("/jobs")) return Promise.resolve({ data: [{ id: "job-3" }] });
      if (url.includes("/documents")) return Promise.resolve({ data: { resume: null, certificates: [] } });
      return Promise.resolve({ data: [] });
    });
    dashboard = renderDashboard();

    await waitFor(() => dashboard.container.querySelector('[data-testid="jobs-deck"]'));
    act(() => dashboard.container.querySelector('[data-testid="jobs-deck"]').click());
    await waitFor(() => dashboard.container.textContent.includes("Unlock more jobs by subscribing."));
  });
});
