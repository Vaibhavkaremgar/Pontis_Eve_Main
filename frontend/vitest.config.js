const path = require("path");
const { defineConfig } = require("vitest/config");
const react = require("@vitejs/plugin-react");

module.exports = defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  test: {
    environment: "jsdom",
    env: {
      VITE_BACKEND_URL: "http://test-backend.local",
    },
    globals: true,
    setupFiles: [path.resolve(__dirname, "src/setupTests.js")],
    include: ["src/**/*.{test,spec}.{js,jsx,ts,tsx}"],
    exclude: ["node_modules", "build", "coverage"],
    css: true,
  },
});
