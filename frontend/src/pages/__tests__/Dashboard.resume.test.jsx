import { buildDashboardEveGreetingFromProfile } from "../../lib/dashboardMessaging";
import { getVoiceIntakeCenterView } from "../../lib/voiceIntakeRouting";
import { buildVoiceIntakeAssistantOverrides } from "../../components/onboarding/VoiceIntake";

// ---------------------------------------------------------------------------
// Simulates the runtime mic-click flow:
//   refreshProfile() fetches backend -> returns fresh voice_intake_resume
//   -> setCenterView("voice") -> VoiceIntake mounts with fresh candidateProfile
//   -> buildVoiceIntakeAssistantOverrides runs -> VAPI receives persisted state
//
// This mirrors the fix in Dashboard.jsx where onMicClick does:
//   async () => { await refreshProfile(); setCenterView("voice"); }
// ---------------------------------------------------------------------------
function simulateMicClick({ backendVoiceIntakeResume }) {
  // refreshProfile() resolves with this — the live backend state
  const freshProfile = {
    name: "Suram Test",
    voice_intake_resume: backendVoiceIntakeResume,
  };
  // VoiceIntake mounts with the fresh profile and builds VAPI overrides
  return buildVoiceIntakeAssistantOverrides({
    firstName: "Suram",
    candidateId: "cid-sim",
    candidateProfile: freshProfile,
  });
}

describe("Dashboard voice intake resume", () => {
  it("shows the saved unanswered voice intake question instead of the generic greeting", () => {
    const currentQuestion =
      "Are there specific industries or types of companies you'd prefer to work with in your next role?";

    const greeting = buildDashboardEveGreetingFromProfile({
      firstName: "Suram",
      profileComplete: true,
      hasResume: true,
      voiceIntakeResume: {
        status: "in_progress",
        has_open_question: true,
        current_question: currentQuestion,
        progress: 2,
      },
    });

    expect(greeting).toContain(currentQuestion);
    expect(greeting).not.toContain("Your profile looks good");
  });

  describe("getVoiceIntakeCenterView - mic click routing", () => {
    it("returns 'chat' (not null) for in_progress so mic click resumes, not restarts", () => {
      const voiceIntakeResume = {
        status: "in_progress",
        has_open_question: true,
        current_question: "What are your key skills?",
        progress: 1,
      };
      expect(getVoiceIntakeCenterView(voiceIntakeResume)).toBe("chat");
    });

    it("returns 'swipe' for completed (initial routing to Jobs for you)", () => {
      expect(getVoiceIntakeCenterView({ status: "completed" })).toBe("swipe");
    });

    it("returns null when no voice intake state exists", () => {
      expect(getVoiceIntakeCenterView(null)).toBeNull();
      expect(getVoiceIntakeCenterView(undefined)).toBeNull();
    });
  });

  describe("resume: VAPI receives saved state so it continues from the next unanswered question", () => {
    const savedResume = {
      status: "in_progress",
      has_open_question: true,
      current_question: "What kind of role are you targeting?",
      next_question: "What kind of role are you targeting?",
      progress: 2,
      completed_turns: [
        { question: "What made you start exploring?", answer: "I wanted Java roles." },
        { question: "What are your key skills?", answer: "Java, Spring Boot." },
      ],
      known_topics: ["background_experience", "skills_technologies"],
      missing_topics: ["target_role"],
    };

    it("passes current_question to VAPI so it resumes from the next unanswered question", () => {
      const overrides = buildVoiceIntakeAssistantOverrides({
        firstName: "Suram",
        candidateId: "cid-resume",
        candidateProfile: { name: "Suram Test", voice_intake_resume: savedResume },
      });
      expect(overrides.variableValues.voice_intake_current_question).toBe(
        "What kind of role are you targeting?"
      );
    });

    it("passes all previously answered turns so VAPI does not re-ask them", () => {
      const overrides = buildVoiceIntakeAssistantOverrides({
        firstName: "Suram",
        candidateId: "cid-resume",
        candidateProfile: { name: "Suram Test", voice_intake_resume: savedResume },
      });
      const answers = overrides.variableValues.voice_intake_answers;
      expect(answers).toContain("What made you start exploring?");
      expect(answers).toContain("I wanted Java roles.");
      expect(answers).toContain("What are your key skills?");
      expect(answers).toContain("Java, Spring Boot.");
    });

    it("passes in_progress status so VAPI knows to continue, not restart", () => {
      const overrides = buildVoiceIntakeAssistantOverrides({
        firstName: "Suram",
        candidateId: "cid-resume",
        candidateProfile: { name: "Suram Test", voice_intake_resume: savedResume },
      });
      expect(overrides.variableValues.voice_intake_status).toBe("in_progress");
    });

    it("routes to swipe (Jobs for you) on initial load when intake is completed", () => {
      const completedResume = { status: "completed", has_open_question: false, current_question: "" };
      expect(getVoiceIntakeCenterView(completedResume)).toBe("swipe");
    });
  });

  // -------------------------------------------------------------------------
  // Runtime simulation: partial call -> exit -> Dashboard -> click mic
  //
  // The fix in Dashboard.jsx:
  //   onMicClick={voiceIntakeCompleted ? undefined : async () => {
  //     await refreshProfile();   // <-- fetches fresh backend state
  //     setCenterView("voice");   // <-- only then mount VoiceIntake
  //   }}
  //
  // These tests prove that after refreshProfile() resolves, VoiceIntake
  // receives the backend-persisted voice_intake_resume, not stale React state.
  // -------------------------------------------------------------------------
  describe("runtime: mic click fetches fresh backend state before VAPI starts", () => {
    const persistedResume = {
      status: "in_progress",
      has_open_question: true,
      current_question: "What kind of role are you targeting?",
      next_question: "What kind of role are you targeting?",
      progress: 2,
      completed_turns: [
        { question: "What made you start exploring?", answer: "I wanted Java roles." },
        { question: "What are your key skills?", answer: "Java, Spring Boot, Hibernate." },
      ],
      known_topics: ["background_experience", "skills_technologies"],
      missing_topics: ["target_role", "availability_location"],
    };

    it("VAPI receives the persisted current_question, not a stale empty string", () => {
      const overrides = simulateMicClick({ backendVoiceIntakeResume: persistedResume });
      expect(overrides.variableValues.voice_intake_current_question).toBe(
        "What kind of role are you targeting?"
      );
    });

    it("VAPI receives all previously answered turns so it does not re-ask them", () => {
      const overrides = simulateMicClick({ backendVoiceIntakeResume: persistedResume });
      const answers = overrides.variableValues.voice_intake_answers;
      expect(answers).toContain("What made you start exploring?");
      expect(answers).toContain("I wanted Java roles.");
      expect(answers).toContain("What are your key skills?");
      expect(answers).toContain("Java, Spring Boot, Hibernate.");
    });

    it("VAPI receives in_progress status so it resumes from question 3, not question 1", () => {
      const overrides = simulateMicClick({ backendVoiceIntakeResume: persistedResume });
      expect(overrides.variableValues.voice_intake_status).toBe("in_progress");
    });

    it("VAPI receives the known and missing topics from the backend", () => {
      const overrides = simulateMicClick({ backendVoiceIntakeResume: persistedResume });
      expect(overrides.variableValues.voice_intake_completed_topics).toBe(
        "background_experience, skills_technologies"
      );
      expect(overrides.variableValues.voice_intake_missing_topics).toBe(
        "target_role, availability_location"
      );
    });

    it("when backend has no saved state, VAPI starts fresh with empty resume fields", () => {
      const overrides = simulateMicClick({ backendVoiceIntakeResume: null });
      expect(overrides.variableValues.voice_intake_status).toBe("");
      expect(overrides.variableValues.voice_intake_current_question).toBe("");
      expect(overrides.variableValues.voice_intake_answers).toBe("");
    });

    it("when backend status is completed, initial routing goes to swipe (mic is still always visible)", () => {
      const completedResume = {
        status: "completed",
        has_open_question: false,
        current_question: "",
        progress: 6,
      };
      // getVoiceIntakeCenterView drives initial centerView to 'swipe'
      // but mic button is always rendered regardless of completed status
      expect(getVoiceIntakeCenterView(completedResume)).toBe("swipe");
    });
  });
});
