import React from "react";
import { act } from "react";
import ReactDOM from "react-dom/client";

jest.mock("axios", () => ({ post: jest.fn() }));
jest.mock("react-router-dom", () => ({
  useNavigate: () => jest.fn(),
  useSearchParams: () => [new URLSearchParams(), jest.fn()],
}), { virtual: true });
jest.mock("../../components/onboarding/VoiceIntake", () => (props) => {
  globalThis.__voiceIntakeProps = props;
  return <div data-testid="voice-intake-mock" />;
});

import Onboarding from "../Onboarding";
import { saveOnboardingState } from "../../lib/onboardingStorage";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

describe("new-candidate voice intake save handoff", () => {
  beforeEach(() => {
    localStorage.clear();
    globalThis.__voiceIntakeProps = null;
    saveOnboardingState({
      step: 4,
      linkedInAuthenticated: true,
      candidateId: "new-candidate-id",
      parsedProfile: {
        candidate_id: "new-candidate-id",
        name: "New Candidate",
        headline: "Software Engineer",
      },
      voiceIntakeCompleted: false,
    });
  });

  it("successful save displays the unchanged Summary page using saved profile data", async () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = ReactDOM.createRoot(container);

    act(() => root.render(<Onboarding />));
    expect(globalThis.__voiceIntakeProps.candidateId).toBe("new-candidate-id");

    await act(async () => {
      globalThis.__voiceIntakeProps.onComplete({
        status: "completed",
        profile: {
          bio: "New Candidate is a Software Engineer professional.",
          voice_intake_resume: { status: "completed" },
        },
      });
    });

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 450));
    });
    let summary = null;
    for (let i = 0; i < 20 && !summary; i += 1) {
      await act(async () => { await Promise.resolve(); });
      summary = container.querySelector('[data-testid="onboarding-summary-list"]');
    }
    expect(summary?.textContent).toContain("New Candidate is a Software Engineer professional.");
    expect(container.querySelector('[data-testid="voice-intake-complete-banner"]')).toBeTruthy();

    act(() => root.unmount());
    container.remove();
  });
});
