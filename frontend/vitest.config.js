const path = require("node:path");
const { defineConfig } = require("vitest/config");
const react = require("@vitejs/plugin-react");

module.exports = defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  test: {
    environment: "jsdom",
    clearMocks: true,
    env: {
      VITE_BACKEND_URL: "http://test-backend.local",
    },
    globals: true,
    setupFiles: [path.resolve(__dirname, "src/setupTests.js")],
    include: ["src/**/*.{test,spec}.{js,jsx,ts,tsx}"],
    exclude: ["node_modules", "build", "coverage"],
    css: true,
    coverage: {
      provider: "v8",
      include: ["src/**/*.{js,jsx,ts,tsx}"],
      // Keep global coverage focused on executable product behavior. These
      // modules are framework/bootstrap glue, fixtures, data-only constants,
      // or generic Radix primitives; they have no independent product flow.
      exclude: [
        "src/App.js",
        "src/index.js",
        "src/mock.js",
        "src/constants/**",
        "src/data/**",
        "src/components/ui/**",
        "src/lib/profileNarrative.js",
      ],
      reporter: ["text", "json", "html"],
      thresholds: {
        statements: 80,
        branches: 80,
        functions: 80,
        lines: 80,
      },
    },
  },
});
