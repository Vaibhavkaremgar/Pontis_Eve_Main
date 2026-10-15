import React from "react";
import { act } from "react";
import ReactDOM from "react-dom/client";
import axios from "axios";
import Dashboard from "../Dashboard";
import { saveOnboardingState } from "../../lib/onboardingStorage";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
jest.mock("axios");
jest.mock("pdfjs-dist/build/pdf", () => ({
  GlobalWorkerOptions: {},
  getDocument: jest.fn(() => ({
    promise: Promise.resolve({
      numPages: 3,
      getPage: jest.fn(() => Promise.resolve({
        getViewport: () => ({ width: 612, height: 792 }),
        render: () => ({ promise: Promise.resolve() }),
      })),
    }),
  })),
}));
jest.mock("pdfjs-dist/build/pdf.worker.entry", () => "pdf-worker");
jest.mock("react-router-dom", () => ({ useNavigate: () => jest.fn(), useSearchParams: () => [new URLSearchParams(), jest.fn()] }), { virtual: true });
jest.mock("../../components/Sidebar", () => ({ setActiveTab, collapsed, onToggleCollapsed }) => <div data-testid="sidebar" data-collapsed={collapsed ? "true" : "false"}><button data-testid="sidebar-collapse-btn" onClick={onToggleCollapsed}>toggle</button>{["jobs", "tracked", "profile", "documents", "opportunities", "billing"].map((id) => <button key={id} data-testid={`nav-tab-${id}`} onClick={() => setActiveTab(id)}>{id}</button>)}</div>);
jest.mock("../../components/ChatHub", () => (props) => <div data-testid="chat-hub"><button data-testid="chat-mic-btn" onClick={props.onMicClick}>mic</button></div>);
jest.mock("../../components/LivingProfile", () => ({ __esModule: true, default: ({ activeTab }) => <div data-testid={`living-profile-${activeTab}`} />, JobsTab: () => <div data-testid="jobs-deck" /> }));
jest.mock("../../components/BillingPage", () => () => <div data-testid="billing-page" />);
jest.mock("../../components/onboarding/VoiceIntake", () => () => <div data-testid="voice-intake" />);
jest.mock("react-resizable-panels", () => ({
  PanelGroup: ({ children }) => <div>{children}</div>,
  Panel: ({ children, id, className, "data-testid": testId, "data-single-content-panel": singleContentPanel, "data-collapsed": collapsed }) => <div id={id} className={className} data-testid={testId} data-single-content-panel={singleContentPanel} data-collapsed={collapsed}>{children}</div>,
  PanelResizeHandle: ({ "data-testid": testId }) => <div data-testid={testId} />,
}));
jest.mock("sonner", () => ({ Toaster: () => null, toast: { error: jest.fn(), success: jest.fn() } }));

const profile = (overrides = {}) => ({ name: "Test User", email: "test@example.com", headline: "Engineer", keySkills: ["React"], experience: [], education: [], certifications: [], profile_strength_percent: 90, profile_strength_label: "Strong", voice_intake_resume: { status: "completed", has_open_question: false }, ...overrides });

function setupAxios({ profiles = [profile()], resume = { filename: "uploaded-resume.pdf" } } = {}) {
  let profileCall = 0;
  axios.get.mockImplementation((url) => {
    if (url.includes("/profile")) return Promise.resolve({ data: profiles[Math.min(profileCall++, profiles.length - 1)] });
    if (url.includes("/documents")) return Promise.resolve({ data: { resume, certificates: [] } });
    if (url.includes("/resume/view")) return Promise.resolve({ data: new Blob(["%PDF-1.4"], { type: "application/pdf" }) });
    if (url.includes("/chat")) return Promise.resolve({ data: { messages: [] } });
    if (url.includes("/jobs") || url.includes("/opportunities") || url.includes("/notifications")) return Promise.resolve({ data: [] });
    if (url.includes("/billing")) return Promise.resolve({ data: {} });
    return Promise.resolve({ data: {} });
  });
}
function renderDashboard() { const container = document.createElement("div"); document.body.appendChild(container); const root = ReactDOM.createRoot(container); act(() => root.render(<Dashboard />)); return { container, unmount: () => { act(() => root.unmount()); container.remove(); } }; }
async function flush() { for (let i = 0; i < 20; i += 1) await act(async () => Promise.resolve()); }
function click(container, id) { act(() => container.querySelector(`[data-testid="${id}"]`).click()); }

describe("Dashboard three-panel context", () => {
  beforeEach(() => { localStorage.clear(); jest.clearAllMocks(); global.URL.createObjectURL = jest.fn(() => "blob:resume"); global.URL.revokeObjectURL = jest.fn(); global.fetch = jest.fn(() => Promise.resolve({ arrayBuffer: () => Promise.resolve(new ArrayBuffer(8)) })); saveOnboardingState({ candidateId: "cand-123", isOpenToMatches: true, activeTab: "jobs" }); });

  it("emphasizes the candidate's primary tab while preserving selected-tab styling", async () => {
    setupAxios({ profiles: [profile()] });
    const view = renderDashboard();
    await flush();
    const jobsTab = view.container.querySelector("[data-testid=jobs-tab]");
    const internshipsTab = view.container.querySelector("[data-testid=internships-tab]");
    const chatTab = view.container.querySelector("[data-testid=chat-tab]");
    expect(chatTab.className).toContain("font-semibold");
    expect(jobsTab.className).toContain("font-semibold");
    expect(jobsTab.className).toContain("bg-black/[0.06]");
    expect(chatTab.className).toContain("text-[#9A9A98]");
    expect(internshipsTab.className).toContain("font-normal");
    click(view.container, "chat-tab");
    expect(chatTab.className).toContain("font-semibold");
    expect(chatTab.className).toContain("bg-black/[0.06]");
    expect(jobsTab.className).toContain("font-normal");
    expect(jobsTab.className).toContain("text-[#9A9A98]");
    view.unmount();
  });

  it.each([
    ["jobs", "jobs-tab"],
    ["intern", "internships-tab"],
  ])("emphasizes %s preferences", async (opportunityType, primaryTabId) => {
    setupAxios({ profiles: [profile({ opportunity_type: opportunityType })] });
    const view = renderDashboard();
    await flush();
    const activeTab = view.container.querySelector(`[data-testid=${primaryTabId}]`);
    expect(activeTab.className).toContain("font-semibold");
    expect(activeTab.className).toContain("bg-black/[0.06]");
    expect(activeTab.className).toContain("text-[#1F1F1F]");
    expect(view.container.querySelector("[data-testid=chat-tab]").className).toContain("font-normal");
    expect([...view.container.querySelectorAll("[data-testid$=-tab]")]
      .filter((tab) => tab.className.includes("bg-black/[0.06]"))).toHaveLength(1);
    view.unmount();
  });

  it("shows job cards in the middle and the uploaded resume on the right", async () => {
    setupAxios(); const view = renderDashboard(); await flush();
    expect(view.container.querySelector("[data-testid=jobs-deck]")).toBeTruthy();
    expect(view.container.querySelector("[data-testid=resume-inline-preview]")).toBeTruthy();
    expect(view.container.querySelector("iframe[title='Resume preview']")).toBeNull();
    expect(view.container.querySelector("[data-testid=dashboard-content-panel]").dataset.singleContentPanel).toBe("false");
    expect(view.container.querySelector("[data-testid=right-panel]")).toBeTruthy();
    expect(view.container.querySelector("[data-testid=right-resume-context]").textContent).toContain("uploaded-resume.pdf"); view.unmount();
  });

  it("keeps the content and right panels mounted while the sidebar is collapsed", async () => {
    setupAxios(); const view = renderDashboard(); await flush();
    expect(view.container.querySelector("[data-testid=left-panel]").dataset.collapsed).toBe("false");
    click(view.container, "sidebar-collapse-btn"); await flush();
    expect(view.container.querySelector("[data-testid=left-panel]").dataset.collapsed).toBe("true");
    expect(view.container.querySelector("[data-testid=dashboard-content-panel]")).toBeTruthy();
    expect(view.container.querySelector("[data-testid=right-panel]")).toBeTruthy();
    click(view.container, "sidebar-collapse-btn"); await flush();
    expect(view.container.querySelector("[data-testid=left-panel]").dataset.collapsed).toBe("false");
    view.unmount();
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

  it("refreshes the canonical resume document after Fix My Resume saves", async () => {
    setupAxios({ profiles: [profile(), profile({ keySkills: ["React", "TypeScript"] })] }); const view = renderDashboard(); await flush();
    await act(async () => window.dispatchEvent(new MessageEvent("message", { data: { type: "eve:candidate-profile-updated", candidateId: "cand-123" }, origin: window.location.origin })));
    await flush();
    expect(axios.get).toHaveBeenCalledWith(expect.stringContaining("/documents"), expect.any(Object));
    expect(view.container.querySelector("[data-testid=resume-inline-preview]")).toBeTruthy(); view.unmount();
  });

  it("shows chat with the profile context", async () => {
    setupAxios(); const view = renderDashboard(); await flush(); click(view.container, "chat-tab"); await flush();
    expect(view.container.querySelector("[data-testid=chat-hub]")).toBeTruthy();
    expect(view.container.querySelector("[data-testid=right-panel]")).toBeTruthy();
    expect(view.container.querySelector("[data-testid=living-profile-profile]")).toBeTruthy(); view.unmount();
  });

  it("shows Profile as chat plus profile context", async () => {
    setupAxios(); const view = renderDashboard(); await flush(); click(view.container, "nav-tab-profile"); await flush();
    expect(view.container.querySelector("[data-testid=chat-hub]")).toBeTruthy();
    expect(view.container.querySelector("[data-testid=right-panel]")).toBeTruthy();
    expect(view.container.querySelector("[data-testid=living-profile-profile]")).toBeTruthy(); view.unmount();
  });

  it.each(["tracked", "documents", "opportunities"])("renders %s in one full-width content panel", async (tab) => {
    setupAxios(); const view = renderDashboard(); await flush(); click(view.container, `nav-tab-${tab}`); await flush();
    expect(view.container.querySelectorAll(`[data-testid=living-profile-${tab}]`)).toHaveLength(1);
    expect(view.container.querySelector("[data-testid=dashboard-content-panel]").dataset.singleContentPanel).toBe("true");
    expect(view.container.querySelector("[data-testid=right-panel]")).toBeNull();
    expect(view.container.querySelector("[data-testid=resize-handle-right]")).toBeNull(); view.unmount();
  });

  it("renders billing in one full-width content panel", async () => {
    setupAxios(); const view = renderDashboard(); await flush(); click(view.container, "nav-tab-billing"); await flush();
    expect(view.container.querySelectorAll("[data-testid=billing-page]")).toHaveLength(1);
    expect(view.container.querySelector("[data-testid=dashboard-content-panel]").dataset.singleContentPanel).toBe("true");
    expect(view.container.querySelector("[data-testid=right-panel]")).toBeNull(); view.unmount();
  });
});
