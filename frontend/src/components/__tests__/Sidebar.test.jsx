import React from "react";
import { act } from "react";
import ReactDOM from "react-dom/client";

import Sidebar from "../Sidebar";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function renderSidebar(props = {}) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container);

  act(() => {
    root.render(
      <Sidebar
        activeTab="profile"
        setActiveTab={jest.fn()}
        userProfile={{
          name: "Jane Doe",
          email: "jane@example.com",
          avatar: "https://cdn.example.com/photos/jane.webp",
        }}
        jobsCount={0}
        opportunitiesCount={0}
        recentActivity={[]}
        onLogout={jest.fn()}
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

describe("Sidebar user footer", () => {
  let renderResult;

  afterEach(() => {
    renderResult?.unmount?.();
    renderResult = null;
  });

  it("always renders the default identity icon instead of an uploaded profile photo", () => {
    renderResult = renderSidebar();

    const card = renderResult.container.querySelector('[data-testid="user-profile-card"]');
    expect(card).toBeTruthy();
    expect(card.querySelector('[data-testid="user-profile-icon"]')).toBeTruthy();
    expect(card.querySelector("img")).toBeNull();
    expect(card.textContent).toContain("Jane Doe");
    expect(card.textContent).toContain("jane@example.com");
  });
});
