import React from "react";
import { act } from "react";
import ReactDOM from "react-dom/client";

import LivingProfile, { generateBio } from "../LivingProfile";
import { buildSummary } from "../../pages/Onboarding";
import { mergeProfilesForDisplay } from "../../lib/profileNormalization";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

jest.mock("react-router-dom", () => ({
  useNavigate: () => jest.fn(),
  useSearchParams: () => [new URLSearchParams(), jest.fn()],
}), { virtual: true });

jest.mock("../../components/onboarding/VoiceIntake", () => () => null);

jest.mock("axios");
jest.mock("sonner", () => ({ toast: { error: jest.fn(), success: jest.fn() } }));

function renderProfile(userProfile) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container);
  act(() => {
    root.render(
      <LivingProfile
        activeTab="profile"
        userProfile={{
          name: "Test User",
          strength: "Strong",
          strengthPercent: 80,
          experience: [],
          education: [],
          keySkills: [],
          preferred_roles: [],
          certifications: [],
          additional_information: "",
          isOpenToMatches: true,
          ...userProfile,
        }}
        jobs={[]}
        documents={{ resume: null, certificates: [] }}
        docsLoading={false}
        candidateId="cand-1"
        selectedJob={null}
        setSelectedJob={jest.fn()}
        onTrackJob={jest.fn()}
        onDismissJob={jest.fn()}
        onToggleOpenToMatches={jest.fn()}
        onResumeReplaced={jest.fn()}
        onCertUploaded={jest.fn()}
        onCertReplaced={jest.fn()}
        onResumeDeleted={jest.fn()}
        onCertDeleted={jest.fn()}
        onInterested={jest.fn()}
        onPhotoChange={jest.fn()}
        onJobViewed={jest.fn()}
      />
    );
  });
  return {
    container,
    unmount() {
      act(() => root.unmount());
      container.remove();
    },
  };
}

afterEach(() => {
  document.body.innerHTML = "";
  jest.clearAllMocks();
});

// ─── Present — Present fix ────────────────────────────────────────────────────

describe("Experience date display — Present never shows as Present — Present", () => {
  it("shows Start Date — Present when a valid start date exists", () => {
    const { container, unmount } = renderProfile({
      experience: [
        {
          id: "exp-1",
          title: "Python Developer",
          company: "Viralbug",
          start_date: "January 2026",
          end_date: "Present",
        },
      ],
    });
    const row = container.querySelector('[data-testid="experience-row-exp-1"]');
    expect(row.textContent).toContain("Jan 2026 — Present");
    expect(row.textContent).not.toMatch(/Present\s*[—\-]\s*Present/);
    unmount();
  });

  it("shows just Present when only end_date=Present and no start date", () => {
    const { container, unmount } = renderProfile({
      experience: [
        {
          id: "exp-2",
          title: "Backend Developer",
          company: "Acme",
          end_date: "Present",
        },
      ],
    });
    const row = container.querySelector('[data-testid="experience-row-exp-2"]');
    expect(row.textContent).not.toMatch(/Present\s*[—\-]\s*Present/);
    unmount();
  });

  it("merged profile with only end_date=Present never produces Present — Present", () => {
    const merged = mergeProfilesForDisplay(
      { experience: [{ id: "r1", title: "Dev", company: "Acme", end_date: "Present" }] },
      { experience: [{ id: "v1", title: "Dev", company: "Acme", end_date: "Present" }] }
    );
    const exp = merged.experience[0];
    expect(exp.dates).not.toMatch(/Present\s*[—\-]\s*Present/);
  });
});

// ─── Bio generation ───────────────────────────────────────────────────────────

describe("generateBio", () => {
  const profile = {
    name: "Jane Doe",
    headline: "Product Manager",
    keySkills: ["Product Strategy", "Roadmapping", "Stakeholder Management", "Agile"],
    preferred_roles: ["Senior Product Manager", "Head of Product"],
    experience: [
      { id: "e1", title: "Product Manager", company: "Acme", dates: "2022 — Present" },
      { id: "e2", title: "Business Analyst", company: "OldCo", dates: "2019 — 2022" },
    ],
    additional_information: "Passionate about building user-centric products.",
  };

  it("generates a bio with 4–5 sentences", () => {
    const bio = generateBio(profile);
    const sentences = bio.split(/(?<=[.!?])\s+/).filter(Boolean);
    expect(sentences.length).toBeGreaterThanOrEqual(4);
    expect(sentences.length).toBeLessThanOrEqual(5);
  });

  it("includes current/previous professional background", () => {
    const bio = generateBio(profile);
    expect(bio).toMatch(/Product Manager/i);
  });

  it("includes target roles from preferred_roles", () => {
    const bio = generateBio(profile);
    expect(bio).toMatch(/Senior Product Manager|Head of Product/i);
  });

  it("does not include company names", () => {
    const bio = generateBio(profile);
    expect(bio).not.toContain("Acme");
    expect(bio).not.toContain("OldCo");
  });

  it("does not include education, exact durations, or skill dumps", () => {
    const bioProfile = {
      ...profile,
      education: [{ degree: "B.Sc CS", institution: "MIT" }],
    };
    const bio = generateBio(bioProfile);
    expect(bio).not.toContain("MIT");
    expect(bio).not.toContain("B.Sc");
    // No raw year ranges
    expect(bio).not.toMatch(/\d{4}\s*[—\-]\s*\d{4}/);
  });

  it("renders the generated bio in the profile tab when profile.bio is empty", () => {
    const profileData = {
      name: "Test User",
      bio: "",
      headline: "Software Engineer",
      keySkills: ["Python", "Django", "AWS", "Docker"],
      preferred_roles: ["Senior Engineer", "Tech Lead"],
      experience: [
        { id: "e1", title: "Software Engineer", company: "TechCo", dates: "2023 — Present" },
      ],
    };
    const { container, unmount } = renderProfile(profileData);
    const content = container.querySelector('[data-testid="living-profile-content"]');
    const bioText = generateBio(profileData);
    expect(bioText.length).toBeGreaterThan(0);
    // First sentence of generated bio should appear in the rendered profile
    expect(content.textContent).toContain(bioText.split(".")[0]);
    unmount();
  });

  it("uses explicit profile.bio when provided, not the generated one", () => {
    const { container, unmount } = renderProfile({
      bio: "This is my custom bio.",
      headline: "Engineer",
      keySkills: ["Python"],
      preferred_roles: ["Lead Engineer"],
    });
    const content = container.querySelector('[data-testid="living-profile-content"]');
    expect(content.textContent).toContain("This is my custom bio.");
    unmount();
  });
});

// ─── Summary preserves existing info and adds Voice Intake info ───────────────

describe("buildSummary — preserves resume info and adds Voice Intake info", () => {
  const merged = mergeProfilesForDisplay(
    {
      headline: "Product Manager",
      location: "New York, NY",
      keySkills: ["Product", "Strategy"],
      certifications: ["AWS Certified Solutions Architect - Associate"],
      experience: [
        { id: "r1", title: "Product Manager", company: "ResumeCo", dates: "2021 — 2023" },
      ],
      education: [{ degree: "B.Sc CS", institution: "MIT" }],
    },
    {
      preferred_roles: ["Senior Product Manager", "Director of Product"],
      additional_information: "Looking for remote-first companies.",
      keySkills: ["Leadership"],
      experience: [
        {
          id: "v1",
          title: "Senior Product Manager",
          company: "VoiceCo",
          start_date: "2024-01-01",
          end_date: "Present",
        },
      ],
    }
  );

  it("preserves resume headline, location, skills, certifications, education", () => {
    const summary = buildSummary(merged);
    const byLabel = Object.fromEntries(summary.map((i) => [i.label, i.value]));
    expect(byLabel["Positioning"]).toBe("Product Manager");
    expect(byLabel["Location"]).toBe("New York, NY");
    expect(byLabel["Top skills"]).toContain("Product");
    expect(byLabel["Certifications"]).toContain("AWS Certified Solutions Architect - Associate");
    expect(byLabel["Education"]).toContain("MIT");
  });

  it("adds Voice Intake target roles", () => {
    const summary = buildSummary(merged);
    const byLabel = Object.fromEntries(summary.map((i) => [i.label, i.value]));
    expect(byLabel["Target roles"]).toContain("Senior Product Manager");
    expect(byLabel["Target roles"]).toContain("Director of Product");
  });

  it("adds Voice Intake career context", () => {
    const summary = buildSummary(merged);
    const byLabel = Object.fromEntries(summary.map((i) => [i.label, i.value]));
    expect(byLabel["Career context"]).toContain("remote-first");
  });

  it("latest role reflects the most recent experience (Voice Intake role)", () => {
    const summary = buildSummary(merged);
    const byLabel = Object.fromEntries(summary.map((i) => [i.label, i.value]));
    expect(byLabel["Latest role"]).toContain("Senior Product Manager");
    expect(byLabel["Latest role"]).toContain("Present");
  });

  it("does not duplicate existing resume fields when Voice Intake is merged", () => {
    const summary = buildSummary(merged);
    const labels = summary.map((i) => i.label);
    const uniqueLabels = [...new Set(labels)];
    expect(labels).toEqual(uniqueLabels);
  });
});
