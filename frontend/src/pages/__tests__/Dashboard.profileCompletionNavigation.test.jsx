import React from "react";
import { act } from "react";
import ReactDOM from "react-dom/client";
import axios from "axios";
import Dashboard from "../Dashboard";
import { saveOnboardingState } from "../../lib/onboardingStorage";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
jest.mock("axios");
jest.mock("react-router-dom", () => ({ useNavigate: () => jest.fn(), useSearchParams: () => [new URLSearchParams(), jest.fn()] }), { virtual: true });
jest.mock("../../components/Sidebar", () => ({ activeTab, setActiveTab }) => <nav>{["jobs", "profile"].map((tab) => <button key={tab} type="button" data-testid={`nav-tab-${tab}`} data-active={activeTab === tab ? "true" : "false"} onClick={() => setActiveTab(tab)}>{tab}</button>)}</nav>);
jest.mock("../../components/ChatHub", () => () => <div data-testid="chat-hub" />);
jest.mock("../../components/LivingProfile", () => ({ __esModule: true, default: ({ activeTab }) => <div data-testid={`living-profile-${activeTab}`} />, JobsTab: () => <div data-testid="jobs-deck" /> }));
jest.mock("../../components/onboarding/VoiceIntake", () => () => <div />);
jest.mock("react-resizable-panels", () => ({ PanelGroup: ({ children }) => <div>{children}</div>, Panel: ({ children, "data-testid": testId, "data-single-content-panel": single }) => <div data-testid={testId} data-single-content-panel={single}>{children}</div>, PanelResizeHandle: () => <div /> }));
jest.mock("sonner", () => ({ Toaster: () => null, toast: { error: jest.fn(), success: jest.fn() } }));

const profile = (percent) => ({ name: "Test User", email: "test@example.com", profile_strength_percent: percent, voice_intake_resume: { status: "completed", has_open_question: false } });
function mockRequests(percent) { axios.get.mockImplementation((url) => Promise.resolve({ data: url.includes("/profile") ? profile(percent) : url.includes("/chat") ? { messages: [] } : url.includes("/documents") ? { resume: null, certificates: [] } : [] })); }
function renderDashboard() { const container = document.createElement("div"); document.body.appendChild(container); const root = ReactDOM.createRoot(container); act(() => root.render(<Dashboard />)); return { container, unmount: () => { act(() => root.unmount()); container.remove(); } }; }
async function waitFor(predicate) { for (let i = 0; i < 30; i += 1) { await act(async () => Promise.resolve()); if (predicate()) return; } throw new Error("Timed out waiting for dashboard state"); }

describe("Dashboard profile completion navbar guidance", () => {
  let view;
  beforeEach(() => { localStorage.clear(); jest.clearAllMocks(); saveOnboardingState({ candidateId: "candidate-1", isOpenToMatches: true }); });
  afterEach(() => view?.unmount());

  it.each([[74, "profile"], [75, "jobs"], [76, "jobs"]])("routes %i%% completion to %s", async (percent, destination) => {
    mockRequests(percent);
    view = renderDashboard();
    await waitFor(() => view.container.querySelector('[data-testid="profile-90-navbar-guidance"]'));
    const guidance = view.container.querySelector('[data-testid="profile-90-navbar-guidance"]');
    expect(guidance.tagName).toBe("BUTTON");
    expect(guidance.getAttribute("aria-label")).toContain("Complete your profile");
    guidance.focus();
    expect(document.activeElement).toBe(guidance);
    act(() => guidance.click());
    await waitFor(() => view.container.querySelector(`[data-testid="nav-tab-${destination}"]`)?.dataset.active === "true");
    expect(view.container.querySelector('[data-testid="chat-hub"]')).toBeTruthy();
    expect(view.container.querySelector('[data-testid="right-panel"]')).toBeTruthy();
    expect(view.container.querySelector('[data-testid="dashboard-content-panel"]').dataset.singleContentPanel).toBe("false");
    if (percent >= 75) expect(view.container.querySelector('[data-testid="chat-tab"]').className).toContain("bg-black/[0.06]");
  });
});
