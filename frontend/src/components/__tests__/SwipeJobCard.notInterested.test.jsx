import React from "react";
import ReactDOM from "react-dom/client";
import { act } from "react";

import { NotInterestedReasonModal, NOT_INTERESTED_REASONS } from "../SwipeJobCard";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function renderModal(props = {}) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container);
  const onConfirm = jest.fn();

  act(() => {
    root.render(
      <NotInterestedReasonModal
        open
        job={{ id: "job-1", title: "Senior Backend Engineer", company: "Acme" }}
        onClose={jest.fn()}
        onConfirm={onConfirm}
        {...props}
      />
    );
  });

  return {
    container,
    root,
    onConfirm,
    unmount() {
      act(() => {
        root.unmount();
      });
      container.remove();
    },
  };
}

describe("NotInterestedReasonModal", () => {
  it("lets the candidate choose a reason before confirming", () => {
    const view = renderModal();

    expect(view.container.textContent).toContain("Salary is too low");
    const button = Array.from(view.container.querySelectorAll("button")).find((el) =>
      el.textContent?.includes("Location is not suitable")
    );
    expect(button).toBeTruthy();

    act(() => {
      button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    const ok = Array.from(view.container.querySelectorAll("button")).find((el) => el.textContent === "OK");
    act(() => {
      ok.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(view.onConfirm).toHaveBeenCalledWith("Location is not suitable");
    expect(NOT_INTERESTED_REASONS).toContain("Other");
    view.unmount();
  });
});
