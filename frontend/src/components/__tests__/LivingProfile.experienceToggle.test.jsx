import React from "react";
import { act } from "react";
import ReactDOM from "react-dom/client";

import LivingProfile from "../LivingProfile";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

jest.mock("axios");
jest.mock("sonner", () => ({
  toast: {
    error: jest.fn(),
    success: jest.fn(),
  },
}));

function renderLivingProfile(props = {}) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container);

  const experience = [
    {
      id: "exp-2021",
      title: "Product Analyst",
      company: "OldCo",
      dates: "2021 - 2022",
      description: "Older role.",
    },
    {
      id: "exp-current",
      title: "Lead Product Manager",
      company: "CurrentCo",
      dates: "2024 - Present",
      description: "Current role.",
    },
    {
      id: "exp-2023",
      title: "Senior Product Manager",
      company: "MidCo",
      dates: "2023 - 2024",
      description: "Newer role.",
    },
    {
      id: "exp-2022",
      title: "Product Manager",
      company: "NextCo",
      dates: "2022 - 2023",
      description: "Middle role.",
    },
  ];

  act(() => {
    root.render(
      <LivingProfile
        activeTab="profile"
        userProfile={{
          name: "Jane Doe",
          strength: "Strong",
          strengthPercent: 82,
          experience,
          education: [],
          keySkills: [],
          availability: "",
          preferred_roles: [],
          certifications: [],
          additional_information: "",
          isOpenToMatches: true,
          ...props.userProfile,
        }}
        jobs={[]}
        documents={{ resume: null, certificates: [] }}
        docsLoading={false}
        candidateId="cand-123"
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
        {...props}
      />
    );
  });

  return {
    container,
    root,
    unmount() {
      act(() => {
        root.unmount();
      });
      container.remove();
    },
  };
}

function getExperienceRows(container) {
  return Array.from(container.querySelectorAll('[data-testid^="experience-row-"]'));
}

function getExperienceTitles(container) {
  return getExperienceRows(container).map((row) => row.querySelector("h4")?.textContent);
}

describe("LivingProfile experience toggle", () => {
  let view;

  beforeAll(() => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date("2026-08-25T00:00:00Z"));
  });

  afterEach(() => {
    view?.unmount?.();
    view = null;
    jest.clearAllMocks();
  });

  afterAll(() => {
    jest.useRealTimers();
  });

  it("shows the default collapsed experience view", () => {
    view = renderLivingProfile();

    expect(view.container.querySelector('[data-testid="experience-toggle"]').textContent).toBe("See all experiences");
    expect(getExperienceRows(view.container)).toHaveLength(3);
  });

  it("clicking See all experiences displays all experiences", () => {
    view = renderLivingProfile();

    act(() => {
      view.container.querySelector('[data-testid="experience-toggle"]').dispatchEvent(
        new MouseEvent("click", { bubbles: true })
      );
    });

    expect(getExperienceRows(view.container)).toHaveLength(4);
  });

  it("changes the label to Show less when expanded", () => {
    view = renderLivingProfile();

    act(() => {
      view.container.querySelector('[data-testid="experience-toggle"]').dispatchEvent(
        new MouseEvent("click", { bubbles: true })
      );
    });

    expect(view.container.querySelector('[data-testid="experience-toggle"]').textContent).toBe("Show less");
  });

  it("clicking Show less collapses the list again", () => {
    view = renderLivingProfile();

    act(() => {
      view.container.querySelector('[data-testid="experience-toggle"]').dispatchEvent(
        new MouseEvent("click", { bubbles: true })
      );
    });

    act(() => {
      view.container.querySelector('[data-testid="experience-toggle"]').dispatchEvent(
        new MouseEvent("click", { bubbles: true })
      );
    });

    expect(view.container.querySelector('[data-testid="experience-toggle"]').textContent).toBe("See all experiences");
    expect(getExperienceRows(view.container)).toHaveLength(3);
  });

  it("keeps reverse-chronological ordering unchanged", () => {
    view = renderLivingProfile();

    expect(getExperienceTitles(view.container)).toEqual([
      "Lead Product Manager",
      "Senior Product Manager",
      "Product Manager",
    ]);

    act(() => {
      view.container.querySelector('[data-testid="experience-toggle"]').dispatchEvent(
        new MouseEvent("click", { bubbles: true })
      );
    });

    expect(getExperienceTitles(view.container)).toEqual([
      "Lead Product Manager",
      "Senior Product Manager",
      "Product Manager",
      "Product Analyst",
    ]);
  });

  it("shows the calculated experience in the profile header instead of the stored value", () => {
    view = renderLivingProfile({
      userProfile: {
        experience_years: 0.6,
        experience: [
          {
            id: "exp-deepija",
            title: "Engineer",
            company: "Deepija Telecom",
            dates: "Nov 2023 - Oct 2024",
          },
          {
            id: "exp-viral",
            title: "Engineer",
            company: "Viral Bug",
            dates: "Aug 2025 - Present",
          },
        ],
      },
    });

    const header = view.container.querySelector('[data-testid="profile-header-card"]');
    expect(header.textContent).toContain("2.1 yrs exp");
    expect(header.textContent).not.toContain("0.6 yrs exp");
  });

  it("renders updated experience dates from the profile API", () => {
    view = renderLivingProfile({
      userProfile: {
        experience: [
          {
            id: "exp-viral",
            title: "Python Developer",
            company: "Viral Bug",
            start_date: "January 2026",
            end_date: "Present",
          },
        ],
      },
    });

    const row = view.container.querySelector('[data-testid="experience-row-exp-viral"]');
    expect(row.textContent).toContain("Viral Bug");
    expect(row.textContent).toContain("Jan 2026 — Present");
  });
});
