import React from "react";
import { act } from "react";
import ReactDOM from "react-dom/client";
import axios from "axios";
import Dashboard from "../Dashboard";
import { saveOnboardingState } from "../../lib/onboardingStorage";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
jest.mock("axios");
jest.mock("react-router-dom", () => ({ useNavigate: () => jest.fn(), useSearchParams: () => [new URLSearchParams(), jest.fn()] }), { virtual: true });
jest.mock("../../components/Sidebar", () => ({ setActiveTab }) => <div>{["jobs", "tracked", "profile", "documents", "opportunities", "billing"].map((id) => <button key={id} data-testid={`nav-tab-${id}`} onClick={() => setActiveTab(id)}>{id}</button>)}</div>);
jest.mock("../../components/ChatHub", () => (props) => <div data-testid="chat-hub"><button data-testid="chat-mic-btn" onClick={props.onMicClick}>mic</button></div>);
jest.mock("../../components/LivingProfile", () => ({ __esModule: true, default: ({ activeTab }) => <div data-testid={`living-profile-${activeTab}`} />, JobsTab: () => <div data-testid="jobs-deck" /> }));
jest.mock("../../components/BillingPage", () => () => <div data-testid="billing-page" />);
jest.mock("../../components/onboarding/VoiceIntake", () => () => <div data-testid="voice-intake" />);
jest.mock("react-resizable-panels", () => ({ PanelGroup: ({ children }) => <div>{children}</div>, Panel: ({ children }) => <div>{children}</div>, PanelResizeHandle: () => <div /> }));
jest.mock("sonner", () => ({ Toaster: () => null, toast: { error: jest.fn(), success: jest.fn() } }));

const profile = (overrides = {}) => ({ name: "Test User", email: "test@example.com", headline: "Engineer", keySkills: ["React"], experience: [], education: [], certifications: [], profile_strength_percent: 90, profile_strength_label: "Strong", voice_intake_resume: { status: "completed", has_open_question: false }, ...overrides });

function setupAxios({ profiles = [profile()], resume = { filename: "uploaded-resume.pdf" } } = {}) {
  let profileCall = 0;
  axios.get.mockImplementation((url) => {
    if (url.includes("/profile")) return Promise.resolve({ data: profiles[Math.min(profileCall++, profiles.length - 1)] });
    if (url.includes("/documents")) return Promise.resolve({ data: { resume, certificates: [] } });
    if (url.includes("/chat")) return Promise.resolve({ data: { messages: [] } });
    if (url.includes("/jobs") || url.includes("/opportunities") || url.includes("/notifications")) return Promise.resolve({ data: [] });
    if (url.includes("/billing")) return Promise.resolve({ data: {} });
    return Promise.resolve({ data: {} });
  });
}
function renderDashboard() { const container = document.createElement("div"); document.body.appendChild(container); const root = ReactDOM.createRoot(container); act(() => root.render(<Dashboard />)); return { container, unmount: () => { act(() => root.unmount()); container.remove(); } }; }
async function flush() { for (let i = 0; i < 10; i += 1) await act(async () => Promise.resolve()); }
function click(container, id) { act(() => container.querySelector(`[data-testid="${id}"]`).click()); }

describe("Dashboard three-panel context", () => {
  beforeEach(() => { localStorage.clear(); jest.clearAllMocks(); saveOnboardingState({ candidateId: "cand-123", isOpenToMatches: true, activeTab: "jobs" }); });

  it("shows job cards in the middle and the uploaded resume on the right", async () => {
    setupAxios(); const view = renderDashboard(); await flush();
    expect(view.container.querySelector("[data-testid=jobs-deck]")).toBeTruthy();
    expect(view.container.querySelector("[data-testid=right-resume-context]").textContent).toContain("uploaded-resume.pdf"); view.unmount();
  });

  it("has a non-crashing resume fallback when no resume exists", async () => {
    setupAxios({ resume: null }); const view = renderDashboard(); await flush();
    expect(view.container.querySelector("[data-testid=resume-context-empty]")).toBeTruthy(); view.unmount();
  });

  it("shows internships with the same resume context", async () => {
    setupAxios({ profiles: [profile({ opportunity_type: "intern" })], resume: { filename: "intern-resume.pdf" } }); const view = renderDashboard(); await flush();
    click(view.container, "internships-tab"); await flush();
    expect(view.container.querySelector("[data-testid=jobs-deck]")).toBeTruthy();
    expect(view.container.querySelector("[data-testid=right-resume-context]").textContent).toContain("intern-resume.pdf"); view.unmount();
  });

  it("uses the refreshed Fix My Resume profile, rather than reverting to uploaded data", async () => {
    setupAxios({ profiles: [profile(), profile({ keySkills: ["React", "TypeScript"] })] }); const view = renderDashboard(); await flush();
    await act(async () => window.dispatchEvent(new MessageEvent("message", { data: { type: "eve:candidate-profile-updated", candidateId: "cand-123" }, origin: window.location.origin })));
    await flush();
    expect(view.container.querySelector("[data-testid=resume-context-skills]").textContent).toContain("TypeScript");
    expect(view.container.querySelector("[data-testid=right-resume-context]").textContent).toContain("uploaded-resume.pdf"); view.unmount();
  });

  it("shows chat with the profile context", async () => {
    setupAxios(); const view = renderDashboard(); await flush(); click(view.container, "chat-tab"); await flush();
    expect(view.container.querySelector("[data-testid=chat-hub]")).toBeTruthy();
    expect(view.container.querySelector("[data-testid=living-profile-profile]")).toBeTruthy(); view.unmount();
  });

  it("shows Profile as chat plus profile context", async () => {
    setupAxios(); const view = renderDashboard(); await flush(); click(view.container, "nav-tab-profile"); await flush();
    expect(view.container.querySelector("[data-testid=chat-hub]")).toBeTruthy();
    expect(view.container.querySelector("[data-testid=living-profile-profile]")).toBeTruthy(); view.unmount();
  });

  it.each(["tracked", "documents", "opportunities"])("renders %s in the middle and right contextual panels", async (tab) => {
    setupAxios(); const view = renderDashboard(); await flush(); click(view.container, `nav-tab-${tab}`); await flush();
    expect(view.container.querySelectorAll(`[data-testid=living-profile-${tab}]`)).toHaveLength(2); view.unmount();
  });

  it("renders billing in both contextual panels", async () => {
    setupAxios(); const view = renderDashboard(); await flush(); click(view.container, "nav-tab-billing"); await flush();
    expect(view.container.querySelectorAll("[data-testid=billing-page]")).toHaveLength(2); view.unmount();
  });
});
