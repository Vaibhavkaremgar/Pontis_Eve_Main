import React from "react";
import { act } from "react";
import ReactDOM from "react-dom/client";
import GoogleAuth from "../GoogleAuth";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("framer-motion", () => ({ motion: { div: ({ children, ...props }) => <div {...props}>{children}</div> } }));
vi.mock("react-router-dom", () => ({
  useNavigate: () => jest.fn(),
  useSearchParams: () => [new URLSearchParams(), jest.fn()],
}), { virtual: true });

describe("LinkedIn login integration", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = ReactDOM.createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it("starts LinkedIn login at the backend endpoint", () => {
    const assign = jest.fn();
    Object.defineProperty(window, "location", { configurable: true, value: { assign } });
    act(() => root.render(<GoogleAuth />));
    const button = [...container.querySelectorAll("button")].find((item) => item.textContent.includes("LinkedIn"));
    act(() => button.click());
    expect(assign).toHaveBeenCalledWith(expect.stringMatching(/\/api\/auth\/linkedin\/login$/));
  });
});
