import { buildVoiceIntakeAssistantOverrides } from "../VoiceIntake";

describe("VoiceIntake resume behavior", () => {
  it("passes in_progress resume state so VAPI resumes from the saved question", () => {
    const currentQuestion = "What are your key skills?";
    const completedTurns = [
      { question: "Tell me about your background.", answer: "I build APIs." },
    ];
    const candidateProfile = {
      name: "Jane Doe",
      voice_intake_resume: {
        status: "in_progress",
        has_open_question: true,
        current_question: currentQuestion,
        next_question: currentQuestion,
        progress: 1,
        completed_turns: completedTurns,
        known_topics: ["background_experience"],
        missing_topics: ["skills_technologies"],
      },
    };

    const overrides = buildVoiceIntakeAssistantOverrides({
      firstName: "Jane",
      candidateId: "cid-1",
      candidateProfile,
    });

    // VAPI must receive the current question so it resumes, not restarts
    expect(overrides.variableValues.voice_intake_status).toBe("in_progress");
    expect(overrides.variableValues.voice_intake_current_question).toBe(currentQuestion);
    expect(overrides.variableValues.voice_intake_answers).toContain("Tell me about your background.");
    expect(overrides.variableValues.voice_intake_completed_topics).toBe("background_experience");
    expect(overrides.variableValues.voice_intake_missing_topics).toBe("skills_technologies");
  });

  it("does not pass completed_turns or current_question when intake is not started", () => {
    const overrides = buildVoiceIntakeAssistantOverrides({
      firstName: "Jane",
      candidateId: "cid-2",
      candidateProfile: { name: "Jane Doe", voice_intake_resume: null },
    });

    expect(overrides.variableValues.voice_intake_status).toBe("");
    expect(overrides.variableValues.voice_intake_current_question).toBe("");
    expect(overrides.variableValues.voice_intake_answers).toBe("");
  });

  it("passes completed status so VAPI knows intake is done and does not re-ask questions", () => {
    const overrides = buildVoiceIntakeAssistantOverrides({
      firstName: "Jane",
      candidateId: "cid-3",
      candidateProfile: {
        name: "Jane Doe",
        voice_intake_resume: {
          status: "completed",
          has_open_question: false,
          current_question: "",
          progress: 6,
          completed_turns: [
            { question: "Q1", answer: "A1" },
            { question: "Q2", answer: "A2" },
          ],
          known_topics: ["background_experience", "target_role"],
          missing_topics: [],
        },
      },
    });

    expect(overrides.variableValues.voice_intake_status).toBe("completed");
    expect(overrides.variableValues.voice_intake_current_question).toBe("");
    expect(overrides.variableValues.voice_intake_completed_topics).toBe(
      "background_experience, target_role"
    );
  });

  it("preserves all previously answered turns in voice_intake_answers", () => {
    const completedTurns = [
      { question: "What made you start exploring?", answer: "I wanted Java roles." },
      { question: "What are your key skills?", answer: "Java, Spring Boot." },
    ];
    const overrides = buildVoiceIntakeAssistantOverrides({
      firstName: "Jane",
      candidateId: "cid-4",
      candidateProfile: {
        name: "Jane Doe",
        voice_intake_resume: {
          status: "in_progress",
          progress: 2,
          completed_turns: completedTurns,
          current_question: "What kind of role are you targeting?",
          known_topics: ["background_experience", "skills_technologies"],
          missing_topics: ["target_role"],
        },
      },
    });

    const answers = overrides.variableValues.voice_intake_answers;
    expect(answers).toContain("What made you start exploring?");
    expect(answers).toContain("I wanted Java roles.");
    expect(answers).toContain("What are your key skills?");
    expect(answers).toContain("Java, Spring Boot.");
  });
});

describe("VoiceIntake candidate context", () => {
  it("passes the candidate resume/profile data into the assistant overrides", () => {
    const candidateProfile = {
      name: "Jane Doe",
      email: "jane@example.com",
      phone: "+15551234567",
      location: "New York",
      headline: "Backend Developer",
      current_company: "",
      experience_years: 5,
      keySkills: ["Python", "FastAPI"],
      experience: [
        {
          title: "Backend Developer",
          company: "Acme",
          dates: "2022 — Present",
        },
      ],
      education: [
        {
          degree: "B.Sc CS",
          institution: "MIT",
        },
      ],
      preferred_roles: ["Senior Backend Engineer"],
      availability: "Immediately",
      notice_period: "2 weeks",
      work_type_preference: "Remote",
      voice_intake_resume: {
        status: "in_progress",
        known_topics: ["background_experience", "skills_technologies"],
        missing_topics: ["availability_location"],
        completed_turns: [
          { question: "Tell me about your background.", answer: "I build APIs." },
        ],
        current_question: "What are your key skills?",
      },
    };

    const overrides = buildVoiceIntakeAssistantOverrides({
      firstName: "Jane",
      candidateId: "candidate-1",
      candidateProfile,
    });

    expect(overrides.variableValues.candidate_name).toBe("Jane Doe");
    expect(overrides.variableValues.current_company).toBe("Acme");
    expect(overrides.variableValues.skills).toBe("Python, FastAPI");
    expect(overrides.variableValues.work_experience).toContain("Backend Developer at Acme");
    expect(overrides.variableValues.education).toContain("B.Sc CS at MIT");
    expect(overrides.variableValues.preferred_roles).toBe("Senior Backend Engineer");
    expect(overrides.variableValues.availability).toBe("Immediately");
    expect(overrides.variableValues.voice_intake_status).toBe("in_progress");
    expect(overrides.variableValues.voice_intake_completed_topics).toBe("background_experience, skills_technologies");
    expect(overrides.variableValues.voice_intake_missing_topics).toBe("availability_location");
    expect(overrides.variableValues.voice_intake_answers).toContain("Tell me about your background.");
    expect(overrides.variableValues.voice_intake_current_question).toBe("What are your key skills?");
    expect(overrides.metadata.candidateId).toBe("candidate-1");
  });
});
