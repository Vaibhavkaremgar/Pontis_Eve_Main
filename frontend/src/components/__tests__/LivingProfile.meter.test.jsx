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
});
