import { generateBio } from "../LivingProfile";
import { buildSummary } from "../../pages/Onboarding";

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

describe("candidate Bio and Summary", () => {
  it("generates a meaningful five-line Bio once voice intake is completed", () => {
    const bio = generateBio(completedProfile);
    expect(sentences(bio)).toHaveLength(5);
    expect(bio).toContain("Product Manager");
    expect(bio).toContain("7 years");
    expect(bio).toContain("Acme");
    expect(bio).toContain("Senior Product Manager");
  });

  it("keeps skills, education, and certifications out of the Bio", () => {
    const bio = generateBio(completedProfile);
    expect(bio).not.toContain("Product Strategy");
    expect(bio).not.toContain("AWS Certified");
    expect(bio).not.toContain("MIT");
    expect(bio).not.toContain("B.Sc");
  });

  it("uses a concise three-line narrative before voice intake is complete", () => {
    const bio = generateBio({
      ...completedProfile,
      voice_intake_resume: { status: "in_progress" },
    });
    expect(sentences(bio)).toHaveLength(3);
  });

  it("keeps the onboarding Summary identical to the Bio and regenerates it from saved updates", () => {
    const initialBio = generateBio(completedProfile);
    const initialSummary = buildSummary(completedProfile);
    expect(initialSummary).toEqual([{ label: "Summary", value: initialBio }]);

    const updated = {
      ...completedProfile,
      preferred_roles: ["Director of Product"],
      additional_information: "Interested in leading product organisations through growth.",
    };
    const updatedBio = generateBio(updated);
    expect(updatedBio).toContain("Director of Product");
    expect(updatedBio).not.toEqual(initialBio);
    expect(buildSummary(updated)).toEqual([{ label: "Summary", value: updatedBio }]);
  });
});
