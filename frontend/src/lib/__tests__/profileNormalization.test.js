import { normalizeProfileForDisplay } from "../profileNormalization";

describe("normalizeProfileForDisplay experience ordering", () => {
  it("puts the present job before the previous job", () => {
    const profile = {
      experience: [
        {
          id: "exp-1",
          company: "Deepija Telecom Private Limited",
          title: "Senior Engineer",
          dates: "01-11-2023 — 09-10-2024",
        },
        {
          id: "exp-2",
          company: "Viral Bug",
          title: "Co-Founder",
          dates: "2024 — Present",
        },
      ],
    };

    const normalized = normalizeProfileForDisplay(profile);

    expect(normalized.experience.map((exp) => exp.company)).toEqual([
      "Viral Bug",
      "Deepija Telecom Private Limited",
    ]);
  });

  it("sorts multiple historical jobs from newest to oldest", () => {
    const profile = {
      experience: [
        {
          id: "exp-1",
          company: "Older Co",
          title: "Analyst",
          dates: "2019 — 2021",
        },
        {
          id: "exp-2",
          company: "Newest Co",
          title: "Lead",
          dates: "2024 — 2025",
        },
        {
          id: "exp-3",
          company: "Middle Co",
          title: "Senior Analyst",
          dates: "2022 — 2023",
        },
      ],
    };

    const normalized = normalizeProfileForDisplay(profile);

    expect(normalized.experience.map((exp) => exp.company)).toEqual([
      "Newest Co",
      "Middle Co",
      "Older Co",
    ]);
  });

  it("treats missing or blank end dates as the most recent experience", () => {
    const profile = {
      experience: [
        {
          id: "exp-1",
          company: "Current Co",
          title: "Founder",
          start_date: "2024-01-01",
          end_date: "",
          dates: "2024",
        },
        {
          id: "exp-2",
          company: "Previous Co",
          title: "Engineer",
          dates: "2022 — 2023",
        },
      ],
    };

    const normalized = normalizeProfileForDisplay(profile);

    expect(normalized.experience.map((exp) => exp.company)).toEqual([
      "Current Co",
      "Previous Co",
    ]);
  });

  it("deduplicates the same role and company using normalized values while keeping the most complete entry", () => {
    const profile = {
      experience: [
        {
          id: "exp-1",
          title: "Senior Engineer ",
          company: " Acme Corp",
          dates: "2024 - Present",
          description: "Built core services.",
        },
        {
          id: "exp-2",
          title: " senior engineer",
          company: "acme corp ",
          dates: "2024 - Present",
          description: "",
          location: "Remote",
          summary: "Led platform work.",
        },
      ],
    };

    const normalized = normalizeProfileForDisplay(profile);

    expect(normalized.experience).toHaveLength(1);
    expect(normalized.experience[0]).toMatchObject({
      title: "senior engineer",
      company: "acme corp",
      description: "Built core services.",
      location: "Remote",
      summary: "Led platform work.",
    });
  });
});
