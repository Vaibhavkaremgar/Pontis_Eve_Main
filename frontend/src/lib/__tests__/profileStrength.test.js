import { getProfileStrengthLabel, hydrateProfileStrength } from "../profileStrength";

describe("profileStrength", () => {
  it("maps an empty profile to Building and 0%", () => {
    const profile = hydrateProfileStrength({});

    expect(profile.strengthPercent).toBe(0);
    expect(profile.strength).toBe("Building");
    expect(getProfileStrengthLabel(profile.strengthPercent)).toBe("Building");
  });

  it("keeps a partially completed profile in the middle range", () => {
    const profile = hydrateProfileStrength({
      profile_strength_percent: 60,
    });

    expect(profile.strengthPercent).toBe(60);
    expect(profile.strength).toBe("Developing");
    expect(getProfileStrengthLabel(profile.strengthPercent)).toBe("Developing");
  });

  it("keeps a fully completed profile at Strong", () => {
    const profile = hydrateProfileStrength({
      profile_strength_percent: 100,
    });

    expect(profile.strengthPercent).toBe(100);
    expect(profile.strength).toBe("Strong");
    expect(getProfileStrengthLabel(profile.strengthPercent)).toBe("Strong");
  });
});
