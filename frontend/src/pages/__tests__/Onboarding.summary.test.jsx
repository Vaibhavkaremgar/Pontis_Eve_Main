import { buildSummary } from "../Onboarding";
import { mergeProfilesForDisplay } from "../../lib/profileNormalization";

jest.mock("react-router-dom", () => ({
  useNavigate: () => jest.fn(),
  useSearchParams: () => [new URLSearchParams(), jest.fn()],
}), { virtual: true });

jest.mock("../../components/onboarding/VoiceIntake", () => () => null);

describe("Onboarding voice intake summary", () => {
  it("shows merged skills, certifications, and latest experience from resume and voice intake", () => {
    const merged = mergeProfilesForDisplay(
      {
        headline: "Product Manager",
        location: "New York, NY",
        keySkills: ["Product", "Strategy"],
        certifications: ["AWS Certified Solutions Architect - Associate"],
        experience: [
          {
            id: "exp-resume",
            title: "Product Manager",
            company: "ResumeCo",
            dates: "2021 â€” 2023",
          },
        ],
      },
      {
        keySkills: ["Leadership", "Strategy"],
        certifications: ["Google Cloud Professional Data Engineer"],
        experience: [
          {
            id: "exp-voice",
            title: "Senior Product Manager",
            company: "VoiceCo",
            start_date: "2024-01-01",
            end_date: "Present",
          },
        ],
      }
    );

    const summary = buildSummary(merged);
    const byLabel = Object.fromEntries(summary.map((item) => [item.label, item.value]));

    expect(byLabel["Top skills"]).toContain("Leadership");
    expect(byLabel["Top skills"]).toContain("Product");
    expect(byLabel["Certifications"]).toContain("AWS Certified Solutions Architect - Associate");
    expect(byLabel["Certifications"]).toContain("Google Cloud Professional Data Engineer");
    expect(byLabel["Latest role"]).toContain("Senior Product Manager");
    expect(byLabel["Latest role"]).toContain("VoiceCo");
    expect(byLabel["Latest role"]).toContain("Present");
  });

  it("does not duplicate repeated values in the rendered summary", () => {
    const merged = mergeProfilesForDisplay(
      {
        keySkills: ["Python", "Python"],
        certifications: ["AWS Certified Solutions Architect - Associate"],
        experience: [
          {
            id: "exp-1",
            title: "Engineer",
            company: "Acme",
            dates: "2024 â€” Present",
          },
        ],
      },
      {
        keySkills: ["python", "Docker"],
        certifications: ["aws certified solutions architect associate"],
        experience: [
          {
            id: "exp-2",
            title: "Engineer ",
            company: " Acme ",
            start_date: "2024-01-01",
            end_date: "Present",
          },
        ],
      }
    );

    const summary = buildSummary(merged);
    const byLabel = Object.fromEntries(summary.map((item) => [item.label, item.value]));

    expect(byLabel["Top skills"].split(", ")).toEqual(["Python", "Docker"]);
    expect(byLabel["Certifications"].split(", ")).toEqual(["AWS Certified Solutions Architect - Associate"]);
    expect(byLabel["Latest role"]).toContain("Engineer");
    expect(byLabel["Latest role"]).toContain("Acme");
  });
});
