import { calculateExperienceYears, normalizeProfileForDisplay } from "../profileNormalization";

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

describe("calculateExperienceYears", () => {
  beforeAll(() => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date("2026-08-25T00:00:00Z"));
  });

  afterAll(() => {
    jest.useRealTimers();
  });

  it("counts historical and present employment periods from month-name ranges", () => {
    const years = calculateExperienceYears([
      { company: "Deepija Telecom", title: "Engineer", dates: "01-11-2023 â€” 09-10-2024" },
      { company: "Viral Bug", title: "Engineer", dates: "Aug 2025 - Present" },
    ]);

    expect(years).toBeCloseTo(2.00, 2);
  });

  it("sums multiple non-overlapping jobs", () => {
    const years = calculateExperienceYears([
      { start_date: "2018-01-01", end_date: "2019-01-01" },
      { start_date: "2020-01-01", end_date: "2021-01-01" },
      { start_date: "2022-01-01", end_date: "2023-01-01" },
    ]);

    expect(years).toBeCloseTo(3.0, 2);
  });

  it("merges overlapping jobs without double counting", () => {
    const years = calculateExperienceYears([
      { start_date: "2020-01-01", end_date: "2021-01-01" },
      { start_date: "2020-06-01", end_date: "2022-01-01" },
      { start_date: "2021-12-01", end_date: "2023-01-01" },
    ]);

    expect(years).toBeCloseTo(3.0, 2);
  });

  it("counts present employment through today", () => {
    const years = calculateExperienceYears([
      { start_date: "2025-08-01", end_date: "Present" },
    ]);

    expect(years).toBeCloseTo(1.07, 2);
  });

  it("handles missing or invalid dates safely", () => {
    const years = calculateExperienceYears([
      { start_date: "not-a-date", end_date: "also-bad" },
      { dates: "??" },
    ]);

    expect(years).toBe(0);
  });

  it("ignores the stored experience_years value when work history exists", () => {
    const normalized = normalizeProfileForDisplay({
      experience_years: 0.6,
      experience: [
        { company: "Deepija Telecom", title: "Engineer", dates: "01-11-2023 â€” 09-10-2024" },
        { company: "Viral Bug", title: "Engineer", dates: "Aug 2025 - Present" },
      ],
    });

    expect(normalized.calculatedExperienceYears).toBeCloseTo(2.00, 2);
    expect(normalized.experience_years).toBe(0.6);
  });
});
