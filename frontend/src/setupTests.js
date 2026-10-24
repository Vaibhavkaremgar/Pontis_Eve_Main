const { webcrypto } = require("node:crypto");

// Vitest's jsdom environment does not consistently expose Web Crypto. Keep the
// browser-compatible implementation available to every test without changing
// application code or individual test files.
if (!globalThis.crypto?.subtle) {
  Object.defineProperty(globalThis, "crypto", {
    configurable: true,
    value: webcrypto,
  });
}

import { vi } from "vitest";

globalThis.jest = vi;

// Vitest's jsdom File implementation may lack Blob.arrayBuffer.
// FileReader preserves the file's actual bytes, so hashing remains real.
if (!globalThis.File.prototype.arrayBuffer) {
  Object.defineProperty(globalThis.File.prototype, "arrayBuffer", {
    configurable: true,
    value() {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.addEventListener("load", () => resolve(reader.result));
        reader.addEventListener("error", () => reject(reader.error));
        reader.readAsArrayBuffer(this);
      });
    },
  });
}

// daily-js probes canvas capabilities during module initialization. JSDOM does
// not implement a canvas context, but these tests do not exercise rendering.
if (globalThis.HTMLCanvasElement) {
  Object.defineProperty(globalThis.HTMLCanvasElement.prototype, "getContext", {
    configurable: true,
    value() {
      return {
        canvas: this,
        getImageData: () => ({ data: new Uint8ClampedArray(4) }),
        putImageData: () => {},
        createImageData: () => ({ data: new Uint8ClampedArray(4) }),
      };
    },
  });
}
