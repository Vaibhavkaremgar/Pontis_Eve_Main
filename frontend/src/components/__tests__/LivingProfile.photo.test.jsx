import React from "react";
import { act } from "react";
import ReactDOM from "react-dom/client";
import axios from "axios";

import { ProfilePhotoUpload } from "../LivingProfile";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

jest.mock("axios");

function renderPhotoUpload(props = {}) {
  const {
    wrapInForm = false,
    onSubmit = jest.fn(),
    user = { name: "Jane Doe", avatar: null, candidate_id: "cand-123" },
    candidateId = "cand-123",
    ...photoProps
  } = props;
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container);

  act(() => {
    const content = (
      <ProfilePhotoUpload
        user={user}
        candidateId={candidateId}
        onPhotoChange={jest.fn()}
        {...photoProps}
      />
    );

    root.render(
      wrapInForm ? (
        <form onSubmit={onSubmit}>{content}</form>
      ) : (
        content
      )
    );
  });

  return {
    container,
    root,
    onSubmit,
    unmount() {
      act(() => {
        root.unmount();
      });
      container.remove();
    },
  };
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
  });
}

describe("ProfilePhotoUpload", () => {
  let alerts;
  let confirms;
  let debugSpy;

  beforeEach(() => {
    jest.clearAllMocks();
    alerts = jest.spyOn(window, "alert").mockImplementation(() => {});
    confirms = jest.spyOn(window, "confirm").mockReturnValue(true);
    debugSpy = jest.spyOn(console, "debug").mockImplementation(() => {});
  });

  afterEach(() => {
    alerts.mockRestore();
    confirms.mockRestore();
    debugSpy.mockRestore();
  });

  it("shows the default placeholder when no photo exists", () => {
    const view = renderPhotoUpload();
    expect(view.container.querySelector('[data-testid="candidate-photo-placeholder"]')).toBeTruthy();
    expect(view.container.querySelector("img")).toBeNull();
    view.unmount();
  });

  it("resolves relative photo urls against the backend base url on dashboard reload", () => {
    const view = renderPhotoUpload({
      user: {
        name: "Jane Doe",
        avatar: "/api/candidate/cand-123/photo/view",
        candidate_id: "cand-123",
      },
    });

    const img = view.container.querySelector("img");
    expect(img).toBeTruthy();
    expect(img.getAttribute("src")).toBe("http://localhost:8001/api/candidate/cand-123/photo/view");
    expect(debugSpy).toHaveBeenCalledWith(
      "[ProfilePhotoUpload] resolved image src:",
      "http://localhost:8001/api/candidate/cand-123/photo/view"
    );
    view.unmount();
  });

  it("keeps absolute photo urls unchanged", () => {
    const view = renderPhotoUpload({
      user: {
        name: "Jane Doe",
        avatar: "https://cdn.example.com/photos/cand-123.webp",
        candidate_id: "cand-123",
      },
    });

    const img = view.container.querySelector("img");
    expect(img).toBeTruthy();
    expect(img.getAttribute("src")).toBe("https://cdn.example.com/photos/cand-123.webp");
    view.unmount();
  });

  it("rejects unsupported image types before uploading", async () => {
    axios.post.mockResolvedValue({ data: { photo_url: "/api/candidate/cand-123/photo/view" } });
    const onPhotoChange = jest.fn();
    const view = renderPhotoUpload({ onPhotoChange });

    const input = view.container.querySelector('input[type="file"]');
    const file = new File(["gif"], "avatar.gif", { type: "image/gif" });

    await act(async () => {
      Object.defineProperty(input, "files", { value: [file], configurable: true });
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });

    expect(axios.post).not.toHaveBeenCalled();
    expect(onPhotoChange).not.toHaveBeenCalled();
    expect(alerts).toHaveBeenCalledWith("Please upload a JPG, JPEG, PNG, or WebP image.");
    view.unmount();
  });

  it("uploads a first photo, renders the returned url, and then deletes it", async () => {
    axios.post.mockResolvedValue({ data: { photo_url: "/api/candidate/cand-123/photo/view?rev=first" } });
    axios.delete.mockResolvedValue({ data: { status: "deleted" } });
    const onPhotoChange = jest.fn();
    const view = renderPhotoUpload({ onPhotoChange });

    const input = view.container.querySelector('input[type="file"]');
    const file = new File(["png"], "avatar.png", { type: "image/png" });

    await act(async () => {
      Object.defineProperty(input, "files", { value: [file], configurable: true });
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });

    await flush();

    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(onPhotoChange).toHaveBeenCalledWith("/api/candidate/cand-123/photo/view?rev=first");
    expect(view.container.querySelector('[data-testid="candidate-photo-placeholder"]')).toBeNull();
    const img = view.container.querySelector("img");
    expect(img).toBeTruthy();
    expect(img.getAttribute("src")).toBe("http://localhost:8001/api/candidate/cand-123/photo/view?rev=first");
    expect(debugSpy).toHaveBeenCalledWith(
      "[ProfilePhotoUpload] resolved image src:",
      "http://localhost:8001/api/candidate/cand-123/photo/view?rev=first"
    );

    const deleteBtn = view.container.querySelector('button[aria-label="Delete profile photo"]');
    expect(deleteBtn).toBeTruthy();

    await act(async () => {
      deleteBtn.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await flush();

    expect(axios.delete).toHaveBeenCalledWith("http://localhost:8001/api/candidate/cand-123/photo");
    expect(onPhotoChange).toHaveBeenLastCalledWith(null);
    expect(view.container.querySelector('[data-testid="candidate-photo-placeholder"]')).toBeTruthy();
    view.unmount();
  });

  it("replaces an existing photo without requiring deletion first", async () => {
    axios.post.mockResolvedValue({ data: { photo_url: "/api/candidate/cand-123/photo/view?rev=replacement" } });
    const onPhotoChange = jest.fn();
    const view = renderPhotoUpload({
      onPhotoChange,
      user: {
        name: "Jane Doe",
        avatar: "/api/candidate/cand-123/photo/view?rev=old",
        candidate_id: "cand-123",
      },
    });

    const existingImg = view.container.querySelector("img");
    expect(existingImg).toBeTruthy();
    expect(existingImg.getAttribute("src")).toBe("http://localhost:8001/api/candidate/cand-123/photo/view?rev=old");

    const input = view.container.querySelector('input[type="file"]');
    const file = new File(["png"], "replacement.png", { type: "image/png" });

    await act(async () => {
      Object.defineProperty(input, "files", { value: [file], configurable: true });
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });

    await flush();

    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(axios.delete).not.toHaveBeenCalled();
    expect(onPhotoChange).toHaveBeenCalledWith("/api/candidate/cand-123/photo/view?rev=replacement");
    const img = view.container.querySelector("img");
    expect(img.getAttribute("src")).toBe("http://localhost:8001/api/candidate/cand-123/photo/view?rev=replacement");
    view.unmount();
  });

  it("keeps the replacement visible after a remount with the refreshed profile data", () => {
    const replacementUrl = "/api/candidate/cand-123/photo/view?rev=replacement";
    const view = renderPhotoUpload({
      user: {
        name: "Jane Doe",
        avatar: replacementUrl,
        candidate_id: "cand-123",
      },
    });

    const img = view.container.querySelector("img");
    expect(img).toBeTruthy();
    expect(img.getAttribute("src")).toBe(`http://localhost:8001${replacementUrl}`);
    view.unmount();
  });

  it("keeps the upload trigger from submitting a parent form and uploads a selected photo", async () => {
    axios.post.mockResolvedValue({ data: { photo_url: "/api/candidate/cand-123/photo/view" } });
    const onPhotoChange = jest.fn();
    const view = renderPhotoUpload({ onPhotoChange, wrapInForm: true });

    const uploadButton = view.container.querySelector('button[aria-label="Upload profile photo"]');
    const input = view.container.querySelector('input[type="file"]');
    const file = new File(["webp"], "avatar.webp", { type: "image/webp" });

    expect(uploadButton.getAttribute("type")).toBe("button");

    await act(async () => {
      uploadButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(view.onSubmit).not.toHaveBeenCalled();

    await act(async () => {
      Object.defineProperty(input, "files", { value: [file], configurable: true });
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });

    await flush();

    expect(axios.post).toHaveBeenCalledWith(
      "http://localhost:8001/api/candidate/cand-123/photo",
      expect.any(FormData)
    );
    expect(onPhotoChange).toHaveBeenCalledWith("/api/candidate/cand-123/photo/view");
    view.unmount();
  });

  it("uses the candidate id from the user object when the prop is missing", async () => {
    axios.post.mockResolvedValue({ data: { photo_url: "/api/candidate/cand-456/photo/view" } });
    const onPhotoChange = jest.fn();
    const view = renderPhotoUpload({
      onPhotoChange,
      candidateId: null,
      user: { name: "Jane Doe", avatar: null, candidate_id: "cand-456" },
    });

    const input = view.container.querySelector('input[type="file"]');
    const file = new File(["png"], "avatar.png", { type: "image/png" });

    await act(async () => {
      Object.defineProperty(input, "files", { value: [file], configurable: true });
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });

    await flush();

    expect(axios.post).toHaveBeenCalledWith(
      "http://localhost:8001/api/candidate/cand-456/photo",
      expect.any(FormData)
    );
    expect(onPhotoChange).toHaveBeenCalledWith("/api/candidate/cand-456/photo/view");
    view.unmount();
  });
});
