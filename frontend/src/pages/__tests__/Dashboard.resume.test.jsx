import { buildDashboardEveGreetingFromProfile } from "../../lib/dashboardMessaging";
import { getVoiceIntakeCenterView } from "../../lib/voiceIntakeRouting";
import { buildVoiceIntakeAssistantOverrides } from "../../components/onboarding/VoiceIntake";

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

  describe("getVoiceIntakeCenterView — mic click routing", () => {
    it("returns 'chat' (not null) for in_progress so mic click resumes, not restarts", () => {
      const voiceIntakeResume = {
        status: "in_progress",
        has_open_question: true,
        current_question: "What are your key skills?",
        progress: 1,
      };
      expect(getVoiceIntakeCenterView(voiceIntakeResume)).toBe("chat");
    });

    it("returns 'swipe' for completed so mic button is hidden", () => {
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

    it("does not route to voice view when intake is completed — mic button is hidden", () => {
      const completedResume = { status: "completed", has_open_question: false, current_question: "" };
      // getVoiceIntakeCenterView returns 'swipe' for completed, so Dashboard hides the mic
      expect(getVoiceIntakeCenterView(completedResume)).toBe("swipe");
    });
  });
});
