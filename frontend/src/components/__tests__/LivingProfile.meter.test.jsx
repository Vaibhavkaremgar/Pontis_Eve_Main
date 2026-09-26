import React from "react";
import ReactDOM from "react-dom/client";
import { act } from "react";

import LivingProfile from "../LivingProfile";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function renderLivingProfile(props = {}) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container);

  act(() => {
    root.render(
      <LivingProfile
        activeTab="profile"
        userProfile={{
          name: "Jane Doe",
          strength: "Strong",
          strengthPercent: 82,
          experience: [],
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

describe("LivingProfile meter label", () => {
  it("renders Profile Meter in the right-side panel without changing the value", () => {
    const view = renderLivingProfile();
    const meter = view.container.querySelector('[data-testid="profile-strength-bar"]');
    expect(meter).toBeTruthy();
    expect(meter.textContent).toContain("Profile Meter");
    expect(meter.textContent).toContain("Strong 82%");
    view.unmount();
  });

  it("briefly highlights the meter when the score increases", () => {
    const view = renderLivingProfile();

    act(() => {
      view.root.render(
        <LivingProfile
          activeTab="profile"
          userProfile={{
            name: "Jane Doe", strength: "Strong", strengthPercent: 88,
            experience: [], education: [], keySkills: [], availability: "",
            preferred_roles: [], certifications: [], additional_information: "", isOpenToMatches: true,
          }}
          jobs={[]} documents={{ resume: null, certificates: [] }} docsLoading={false}
          candidateId="cand-123" selectedJob={null} setSelectedJob={jest.fn()} onTrackJob={jest.fn()}
          onDismissJob={jest.fn()} onToggleOpenToMatches={jest.fn()} onResumeReplaced={jest.fn()}
          onCertUploaded={jest.fn()} onCertReplaced={jest.fn()} onResumeDeleted={jest.fn()}
          onCertDeleted={jest.fn()} onInterested={jest.fn()} onPhotoChange={jest.fn()} onJobViewed={jest.fn()}
        />
      );
    });

    expect(view.container.querySelector('[data-testid="profile-strength-bar"]').classList.contains("profile-strength-meter--improving")).toBe(true);
    view.unmount();
  });

  it("does not render 90% guidance in the right-side profile panel", () => {
    const view = renderLivingProfile({
      userProfile: {
        profile_strength_detail: {
          ninety_percent_guidance: {
            current_percent: 82,
            remaining_percent_to_90: 8,
            items: [{
              title: "Key skills",
              action: "Add your key skills to your profile",
              section: "skills",
            }],
          },
        },
      },
    });

    expect(view.container.querySelector('[data-testid="profile-90-guidance"]')).toBeNull();
    expect(view.container.querySelector("#profile-skills")).toBeTruthy();
    view.unmount();
  });
});
