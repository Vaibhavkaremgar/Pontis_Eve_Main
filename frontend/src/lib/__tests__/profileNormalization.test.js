import { normalizeProfileForDisplay } from "../profileNormalization";

describe("profileNormalization", () => {
  it("deduplicates exact, case-insensitive, and whitespace-normalized skill duplicates", () => {
    const profile = normalizeProfileForDisplay({
      keySkills: ["Python", " python ", "PYTHON", "FastAPI", " fastapi  "],
      certifications: [],
    });

    expect(profile.keySkills).toEqual(["Python", "FastAPI"]);
  });

  it("keeps genuinely different skills separate", () => {
    const profile = normalizeProfileForDisplay({
      keySkills: ["Java", "JavaScript"],
      certifications: [],
    });

    expect(profile.keySkills).toEqual(["Java", "JavaScript"]);
  });

  it("deduplicates near-duplicate certifications and keeps clearly certified items out of skills", () => {
    const profile = normalizeProfileForDisplay({
      keySkills: ["AWS Certified Solutions Architect - Associate", "Python"],
      certifications: [
        "aws certified solutions architect associate",
        "AWS Certified Solutions Architect - Associate",
      ],
    });

    expect(profile.certifications).toEqual([
      "aws certified solutions architect associate",
    ]);
    expect(profile.keySkills).toEqual(["Python"]);
  });
});
