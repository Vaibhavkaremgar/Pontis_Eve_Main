const path = require("path");
const { defineConfig, transformWithEsbuild } = require("vite");
const react = require("@vitejs/plugin-react");

module.exports = defineConfig({
  plugins: [
    {
      name: "jsx-in-js",
      enforce: "pre",
      async transform(code, id) {
        if (id.endsWith(".js") && id.includes("/src/")) {
          return transformWithEsbuild(code, id, { loader: "jsx" });
        }
      },
    },
    react(),
  ],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
  optimizeDeps: {
    esbuildOptions: {
      loader: {
        ".js": "jsx",
      },
    },
  },
  build: {
    outDir: "build",
    sourcemap: false,
  },
  server: {
    watch: {
      ignored: [
        "**/node_modules/**",
        "**/.git/**",
        "**/build/**",
        "**/dist/**",
        "**/coverage/**",
        "**/public/**",
      ],
    },
  },
});
