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
jest.mock("../../components/LivingProfile", () => ({
  __esModule: true,
  default: ({ onPhotoChange, userProfile }) => {
    latestProfile = userProfile;
    return <div data-testid="right-living-profile"><button data-testid="refresh-profile" onClick={() => onPhotoChange("photo-url")}>Refresh</button></div>;
  },
  JobsTab: ({ jobs = [], onLockedJobClick }) => (
    <div data-testid="jobs-for-you-list">
      {jobs.map((job) => job.locked ? (
        <button key={job.id} data-testid={`locked-job-card-${job.id}`} onClick={onLockedJobClick}>Locked</button>
      ) : <div key={job.id} data-testid={`job-card-${job.id}`}>Unlocked</div>)}
    </div>
  ),
}));
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
    expect(dashboard.container.querySelector('[data-testid="jobs-for-you-list"]')).toBeNull();
  });

  it("unlocks Jobs for you at exactly 90%", async () => {
    mockRequests([profile(90)]);
    dashboard = renderDashboard();

    await waitFor(() => dashboard.container.querySelector('[data-testid="jobs-for-you-list"]'));
    expect(dashboard.container.querySelector('[data-testid="jobs-tab"]')).not.toBeNull();
    expect(dashboard.container.querySelector('[data-testid="chat-tab"]')).not.toBeNull();
  });

  it("shows both tabs above 90%", async () => {
    mockRequests([profile(91)]);
    dashboard = renderDashboard();

    await waitFor(() => dashboard.container.querySelector('[data-testid="jobs-for-you-list"]'));
    expect(dashboard.container.querySelector('[data-testid="jobs-tab"]')).not.toBeNull();
    expect(dashboard.container.querySelector('[data-testid="chat-tab"]')).not.toBeNull();
  });

  it("hides Jobs for you again when a refreshed profile drops below 90%", async () => {
    mockRequests([profile(90), profile(89)]);
    dashboard = renderDashboard();

    await waitFor(() => dashboard.container.querySelector('[data-testid="jobs-for-you-list"]'));
    act(() => dashboard.container.querySelector('[data-testid="refresh-profile"]').click());

    await waitFor(() => dashboard.container.querySelector('[data-testid="chat-hub"]'));
    expect(dashboard.container.querySelector('[data-testid="jobs-tab"]')).toBeNull();
    expect(dashboard.container.querySelector('[data-testid="jobs-for-you-list"]')).toBeNull();
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

  it("shows the subscription prompt when a locked match is selected", async () => {
    axios.get.mockImplementation((url, config) => {
      if (url.includes("/profile")) return Promise.resolve({ data: profile(90) });
      if (url.includes("/jobs")) return Promise.resolve({
        data: [{ id: "job-4", locked: true }],
        headers: { "x-total-matching-jobs": "100" },
      });
      if (url.includes("/documents")) return Promise.resolve({ data: { resume: null, certificates: [] } });
      return Promise.resolve({ data: [] });
    });
    dashboard = renderDashboard();

    await waitFor(() => dashboard.container.querySelector('[data-testid="locked-job-card-job-4"]'));
    act(() => dashboard.container.querySelector('[data-testid="locked-job-card-job-4"]').click());
    await waitFor(() => dashboard.container.textContent.includes("You’ve reached your 3 free job-match views for today"));
    expect(dashboard.container.textContent).toContain("You have 100 jobs matching your profile");
  });

  it("keeps the first three daily-access jobs unlocked and renders later matches locked", async () => {
    const jobs = Array.from({ length: 5 }, (_, index) => ({
      id: `job-${index + 1}`,
      locked: index >= 3,
    }));
    axios.get.mockImplementation((url) => {
      if (url.includes("/profile")) return Promise.resolve({ data: profile(90) });
      if (url.includes("/jobs")) return Promise.resolve({ data: jobs, headers: { "x-total-matching-jobs": "5" } });
      if (url.includes("/documents")) return Promise.resolve({ data: { resume: null, certificates: [] } });
      return Promise.resolve({ data: [] });
    });
    dashboard = renderDashboard();

    await waitFor(() => dashboard.container.querySelector('[data-testid="job-card-job-3"]'));
    const middleList = dashboard.container.querySelector('[data-testid="jobs-for-you-list"]');
    expect([...middleList.children].map((card) => card.dataset.testid)).toEqual([
      "job-card-job-1",
      "job-card-job-2",
      "job-card-job-3",
      "locked-job-card-job-4",
      "locked-job-card-job-5",
    ]);
    expect(dashboard.container.querySelectorAll('[data-testid^="job-card-"]')).toHaveLength(3);
    expect(dashboard.container.querySelectorAll('[data-testid^="locked-job-card-"]')).toHaveLength(2);
    expect(dashboard.container.querySelector('[data-testid="right-living-profile"]').querySelector('[data-testid^="job-card-"]')).toBeNull();
    expect(dashboard.container.querySelector('[data-testid="right-living-profile"]').querySelector('[data-testid^="locked-job-card-"]')).toBeNull();
  });

  it("opens the existing subscription popup when a locked job is clicked", async () => {
    mockRequests([profile(90)]);
    axios.get.mockImplementation((url) => {
      if (url.includes("/profile")) return Promise.resolve({ data: profile(90) });
      if (url.includes("/jobs")) return Promise.resolve({ data: [{ id: "job-4", locked: true }], headers: { "x-total-matching-jobs": "4" } });
      if (url.includes("/documents")) return Promise.resolve({ data: { resume: null, certificates: [] } });
      return Promise.resolve({ data: [] });
    });
    dashboard = renderDashboard();

    await waitFor(() => dashboard.container.querySelector('[data-testid="locked-job-card-job-4"]'));
    act(() => dashboard.container.querySelector('[data-testid="locked-job-card-job-4"]').click());
    await waitFor(() => dashboard.container.querySelector('[role="dialog"]'));
    expect(dashboard.container.querySelector('[role="dialog"]')).toBeTruthy();
  });

  it("renders every job unlocked when the subscription response marks them accessible", async () => {
    const jobs = Array.from({ length: 5 }, (_, index) => ({ id: `subscriber-job-${index + 1}`, locked: false }));
    axios.get.mockImplementation((url) => {
      if (url.includes("/profile")) return Promise.resolve({ data: profile(90) });
      if (url.includes("/jobs")) return Promise.resolve({ data: jobs, headers: { "x-total-matching-jobs": "5" } });
      if (url.includes("/documents")) return Promise.resolve({ data: { resume: null, certificates: [] } });
      return Promise.resolve({ data: [] });
    });
    dashboard = renderDashboard();

    await waitFor(() => dashboard.container.querySelector('[data-testid="job-card-subscriber-job-5"]'));
    expect(dashboard.container.querySelectorAll('[data-testid^="job-card-"]')).toHaveLength(5);
    expect(dashboard.container.querySelector('[data-testid^="locked-job-card-"]')).toBeNull();
  });
});
