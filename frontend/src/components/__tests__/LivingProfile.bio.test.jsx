import { generateBio } from "../LivingProfile";
jest.mock("react-router-dom", () => ({
  useNavigate: () => jest.fn(),
  useSearchParams: () => [new URLSearchParams(), jest.fn()],
}), { virtual: true });
jest.mock("../../components/onboarding/VoiceIntake", () => () => null);

const completedProfile = {
  name: "Jane Doe",
  headline: "Product Manager",
  experience_years: 7,
  keySkills: ["Product Strategy", "Roadmapping"],
  certifications: ["AWS Certified"],
  education: [{ degree: "B.Sc CS", institution: "MIT" }],
  experience: [
    { id: "current", title: "Product Manager", company: "Acme" },
    { id: "previous", title: "Business Analyst", company: "OldCo" },
  ],
  preferred_roles: ["Senior Product Manager", "Head of Product"],
  additional_information: "Looking for remote-first companies with mission-driven work.",
  voice_intake_resume: { status: "completed" },
};

function sentences(text) {
  return text.split(/(?<=[.!?])\s+/).filter(Boolean);
}

describe("candidate Bio", () => {
  it("derives a concise, non-repetitive Bio from the persisted profile", () => {
    const bio = generateBio(completedProfile);
    expect(sentences(bio)).toHaveLength(3);
    expect(bio).toContain("Product Manager");
    expect(bio).toContain("7 years");
    expect(bio).toContain("Acme");
    expect(bio).toContain("Senior Product Manager");
    expect(bio).toContain("Product Strategy");
    expect(bio).not.toContain("Jane Doe");
    expect(bio.match(/Product Manager at Acme/g)).toHaveLength(1);
    expect(bio.match(/Acme/g)).toHaveLength(1);
  });

  it("uses relevant skills without pulling in unrelated education or certifications", () => {
    const bio = generateBio(completedProfile);
    expect(bio).toContain("Product Strategy");
    expect(bio).not.toContain("AWS Certified");
    expect(bio).not.toContain("MIT");
    expect(bio).not.toContain("B.Sc");
  });

  it("uses normalized work history and the explicit current role/company", () => {
    const bio = generateBio({
      ...completedProfile,
      experience_years: 0.6,
      headline: "Software Engineer",
      current_role: "Python Developer",
      current_company: "Viral Bug",
      experience: [
        { id: "viral", title: "Python Developer", company: "Viral Bug", start_date: "2025-08", end_date: "Present" },
        { id: "deepija", title: "Software Engineer", company: "Deepija Telecom Private Limited", start_date: "2023-11", end_date: "2024-10" },
      ],
    });

    expect(bio).toContain("2.1 years");
    expect(bio).toContain("Python Developer at Viral Bug");
    expect(bio).not.toContain("currently works as Software Engineer at Deepija Telecom Private Limited");
  });

  it("regenerates from Voice Intake profile updates and differs for different candidates", () => {
    const beforeVoiceUpdate = generateBio(completedProfile);
    const voiceUpdatedProfile = {
      ...completedProfile,
      current_role: "Platform Engineer",
      current_company: "Northstar Systems",
      headline: "Platform Engineer",
      experience_years: 4,
      keySkills: ["Kubernetes", "Go"],
      preferred_roles: ["Staff Platform Engineer"],
      experience: [{ id: "current", title: "Platform Engineer", company: "Northstar Systems" }],
    };
    const afterVoiceUpdate = generateBio(voiceUpdatedProfile);

    expect(afterVoiceUpdate).toContain("Platform Engineer at Northstar Systems");
    expect(afterVoiceUpdate).toContain("4 years");
    expect(afterVoiceUpdate).toContain("Kubernetes and Go");
    expect(afterVoiceUpdate).toContain("Staff Platform Engineer");
    expect(afterVoiceUpdate).not.toContain("Product Manager");
    expect(afterVoiceUpdate).not.toEqual(beforeVoiceUpdate);
  });
});
