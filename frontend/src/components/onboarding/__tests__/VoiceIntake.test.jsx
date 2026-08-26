import { buildVoiceIntakeAssistantOverrides } from "../VoiceIntake";

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
