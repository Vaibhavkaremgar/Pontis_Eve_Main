import React from "react";
import { act } from "react";
import ReactDOM from "react-dom/client";

import ChatHub from "../ChatHub";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

describe("ChatHub composer", () => {
  let container;
  let root;
  let onSend;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = ReactDOM.createRoot(container);
    onSend = jest.fn((event) => event.preventDefault());
    act(() => {
      root.render(
        <ChatHub
          chats={[]}
          inputValue=""
          setInputValue={jest.fn()}
          onSend={onSend}
          sending={false}
          quickActions={[]}
        />
      );
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it("uses a wrapping multiline textarea that caps its growth", () => {
    const input = container.querySelector('[data-testid="chat-text-input"]');
    expect(input.tagName).toBe("TEXTAREA");
    expect(input.getAttribute("wrap")).toBe("soft");

    Object.defineProperty(input, "scrollHeight", { configurable: true, value: 240 });
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
    act(() => {
      setter.call(input, "A long message");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });

    expect(input.style.height).toBe("160px");
    expect(input.style.overflowY).toBe("auto");
  });

  it("keeps Enter for a newline and submits with Ctrl/Cmd + Enter", () => {
    const input = container.querySelector('[data-testid="chat-text-input"]');
    act(() => {
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    });
    expect(onSend).not.toHaveBeenCalled();

    act(() => {
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", ctrlKey: true, bubbles: true }));
    });
    expect(onSend).toHaveBeenCalledTimes(1);

    act(() => {
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", metaKey: true, bubbles: true }));
    });
    expect(onSend).toHaveBeenCalledTimes(2);
  });
});
