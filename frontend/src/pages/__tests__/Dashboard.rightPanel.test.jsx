import React from "react";
import { act } from "react";
import ReactDOM from "react-dom/client";
import axios from "axios";

import Dashboard from "../Dashboard";
import { saveOnboardingState, loadOnboardingState } from "../../lib/onboardingStorage";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

jest.mock("axios");
let lastLivingProfileProps = null;
let mockChatHubOnMicClick = null;

jest.mock("react-router-dom", () => ({
  MemoryRouter: ({ children }) => <div>{children}</div>,
  BrowserRouter: ({ children }) => <div>{children}</div>,
  Routes: ({ children }) => <div>{children}</div>,
  Route: ({ element }) => element ?? null,
  Navigate: () => null,
  useNavigate: () => jest.fn(),
  useSearchParams: () => [new URLSearchParams(), jest.fn()],
}), { virtual: true });

jest.mock("../../components/Sidebar", () => ({ activeTab, setActiveTab }) => (
  <div data-testid="sidebar">
    {["jobs", "tracked", "profile", "documents", "opportunities"].map((id) => (
      <button key={id} data-testid={`nav-tab-${id}`} onClick={() => setActiveTab(id)}>
        {id}
      </button>
    ))}
  </div>
));
jest.mock("../../components/ChatHub", () => (props) => {
  mockChatHubOnMicClick = props.onMicClick;
  return (
    <div data-testid="chat-hub">
      <button data-testid="chat-mic-btn" onClick={props.onMicClick}>
        mic
      </button>
    </div>
  );
});
jest.mock("../../components/LivingProfile", () => (props) => {
  lastLivingProfileProps = props;
  return <div data-testid="living-profile" data-active-tab={props.activeTab} />;
});
jest.mock("../../components/SwipeJobCard", () => () => <div data-testid="jobs-deck" />);
jest.mock("../../components/onboarding/VoiceIntake", () => () => <div data-testid="voice-intake" />);
jest.mock("react-resizable-panels", () => ({
  PanelGroup: ({ children }) => <div>{children}</div>,
  Panel: ({ children }) => <div>{children}</div>,
  PanelResizeHandle: () => <div />,
}));
jest.mock("sonner", () => ({
  Toaster: () => null,
  toast: { error: jest.fn(), success: jest.fn() },
}));

function makeProfile(overrides = {}) {
  return {
    photo_url: null,
    name: "Test User",
    email: "test@example.com",
    phone: "",
    headline: "Engineer",
    location: "NYC",
    bio: "",
    experience: [],
    education: [],
    keySkills: ["React"],
    experience_years: 3,
    availability: "",
    preferred_roles: [],
    certifications: [],
    additional_information: "",
    profile_strength_percent: 80,
    profile_strength_label: "Strong",
    voice_intake_resume: { status: "completed", has_open_question: false, current_question: "", next_question: "" },
    ...overrides,
  };
}

function setupAxios(profile = makeProfile()) {
  axios.get.mockImplementation((url) => {
    if (url.includes("/profile")) return Promise.resolve({ data: profile });
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
    unmount() {
      act(() => { root.unmount(); });
      container.remove();
    },
  };
}

async function flush(n = 10) {
  for (let i = 0; i < n; i++) {
    await act(async () => { await Promise.resolve(); });
  }
}

function getRightPanelTab(container) {
  return container.querySelector('[data-testid="living-profile"]')?.getAttribute("data-active-tab");
}

describe("Dashboard right-panel state restoration", () => {
  beforeEach(() => {
    localStorage.clear();
    jest.clearAllMocks();
    lastLivingProfileProps = null;
    mockChatHubOnMicClick = null;
    saveOnboardingState({ candidateId: "cand-123", isOpenToMatches: true });
  });

  afterEach(() => {
    // unmount handled per-test
  });

  // 1. Default / refresh with no persisted tab → Profile panel
  it("default Dashboard (no persisted tab) shows Profile panel", async () => {
    setupAxios();
    const { container, unmount } = renderDashboard();
    await flush();
    expect(getRightPanelTab(container)).toBe("profile");
    unmount();
  });

  // 2. Chat with Eve center view → right panel shows Profile
  it("Chat with Eve center view → right panel shows Profile", async () => {
    setupAxios(makeProfile({
      voice_intake_resume: { status: "in_progress", has_open_question: true, current_question: "Q?", next_question: "" },
    }));
    saveOnboardingState({ candidateId: "cand-123", isOpenToMatches: true, activeTab: "jobs" });
    const { container, unmount } = renderDashboard();
    await flush();
    // in_progress routes center to chat; right panel must still be profile even when the sidebar tab was jobs.
    expect(container.querySelector('[data-testid="chat-hub"]')).toBeTruthy();
    expect(getRightPanelTab(container)).toBe("profile");
    unmount();
  });

  // 3. Voice Intake center view → right panel shows Profile
  it("Voice Intake center view → right panel shows Profile", async () => {
    setupAxios(makeProfile({
      voice_intake_resume: { status: "in_progress", has_open_question: true, current_question: "Q?", next_question: "" },
    }));
    saveOnboardingState({ candidateId: "cand-123", isOpenToMatches: true, activeTab: "documents" });
    const { container, unmount } = renderDashboard();
    await flush();
    act(() => { container.querySelector('[data-testid="chat-mic-btn"]')?.click(); });
    await flush();
    expect(container.querySelector('[data-testid="voice-intake"]')).toBeTruthy();
    // Right panel must still be profile regardless of the voice center view.
    expect(getRightPanelTab(container)).toBe("profile");
    unmount();
  });

  // 4. New Jobs tab → right panel shows jobs
  it("New Jobs sidebar tab → right panel shows jobs", async () => {
    setupAxios();
    const { container, unmount } = renderDashboard();
    await flush();
    act(() => { container.querySelector('[data-testid="nav-tab-jobs"]').click(); });
    await flush();
    expect(getRightPanelTab(container)).toBe("jobs");
    unmount();
  });

  // 5. Tracked Jobs tab → right panel shows tracked
  it("Tracked Jobs sidebar tab → right panel shows tracked", async () => {
    setupAxios();
    const { container, unmount } = renderDashboard();
    await flush();
    act(() => { container.querySelector('[data-testid="nav-tab-tracked"]').click(); });
    await flush();
    expect(getRightPanelTab(container)).toBe("tracked");
    unmount();
  });

  // 6. Documents tab → right panel shows documents
  it("Documents sidebar tab → right panel shows documents", async () => {
    setupAxios();
    const { container, unmount } = renderDashboard();
    await flush();
    act(() => { container.querySelector('[data-testid="nav-tab-documents"]').click(); });
    await flush();
    expect(getRightPanelTab(container)).toBe("documents");
    unmount();
  });

  // 7. Notifications tab → right panel shows opportunities
  it("Notifications sidebar tab → right panel shows opportunities", async () => {
    setupAxios();
    const { container, unmount } = renderDashboard();
    await flush();
    act(() => { container.querySelector('[data-testid="nav-tab-opportunities"]').click(); });
    await flush();
    expect(getRightPanelTab(container)).toBe("opportunities");
    unmount();
  });

  // 8. Browser refresh restores persisted tab
  it("browser refresh restores persisted tab (tracked) and shows correct right panel", async () => {
    setupAxios();
    // Simulate a previous session that left the user on "tracked"
    saveOnboardingState({ candidateId: "cand-123", isOpenToMatches: true, activeTab: "tracked" });
    const { container, unmount } = renderDashboard();
    await flush();
    expect(getRightPanelTab(container)).toBe("tracked");
    unmount();
  });

  it("browser refresh restores persisted tab (documents) and shows correct right panel", async () => {
    setupAxios();
    saveOnboardingState({ candidateId: "cand-123", isOpenToMatches: true, activeTab: "documents" });
    const { container, unmount } = renderDashboard();
    await flush();
    expect(getRightPanelTab(container)).toBe("documents");
    unmount();
  });

  it("browser refresh restores persisted tab (opportunities) and shows correct right panel", async () => {
    setupAxios();
    saveOnboardingState({ candidateId: "cand-123", isOpenToMatches: true, activeTab: "opportunities" });
    const { container, unmount } = renderDashboard();
    await flush();
    expect(getRightPanelTab(container)).toBe("opportunities");
    unmount();
  });

  it("browser refresh restores persisted tab (jobs) and shows correct right panel", async () => {
    setupAxios();
    saveOnboardingState({ candidateId: "cand-123", isOpenToMatches: true, activeTab: "jobs" });
    const { container, unmount } = renderDashboard();
    await flush();
    expect(getRightPanelTab(container)).toBe("jobs");
    unmount();
  });

  // 9. Tab change is persisted so next refresh restores it
  it("tab change is persisted to localStorage for next refresh", async () => {
    setupAxios();
    const { container, unmount } = renderDashboard();
    await flush();
    act(() => { container.querySelector('[data-testid="nav-tab-documents"]').click(); });
    await flush();
    expect(loadOnboardingState().activeTab).toBe("documents");
    unmount();
  });

  // 10. Invalid/unknown persisted tab falls back to profile
  it("invalid persisted tab falls back to profile panel", async () => {
    setupAxios();
    saveOnboardingState({ candidateId: "cand-123", isOpenToMatches: true, activeTab: "new-jobs" });
    const { container, unmount } = renderDashboard();
    await flush();
    expect(getRightPanelTab(container)).toBe("profile");
    unmount();
  });
});
