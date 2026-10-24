# Eve frontend: CRA to Vite migration

Migration reference for the Eve_Main frontend. This documentation describes the completed migration on branch `feature/vite-migration`, commit `fb46dd6`.

## Overview

The frontend was migrated to Vite to simplify the toolchain, improve development-server startup and feedback cycles, reduce build configuration overhead, and align the project with a modern React ecosystem. The test runner was migrated at the same time so that development, production builds, and tests share the same Vite-oriented module and environment conventions.

### Before

- Create React App (CRA) provided the application bootstrap and build pipeline.
- CRACO supplied project-specific CRA overrides.
- Jest was used for automated tests.
- `frontend/package-lock.json` was replaced by the repository's Yarn lockfile workflow.

### After

- Vite serves and bundles the React application.
- Vitest runs the automated test suite with a Vite-compatible configuration.
- Yarn 1.22.22 is the package-manager contract, recorded in `package.json` and `frontend/yarn.lock`.
- The application remains a React 19, JavaScript/JSX, React Router, Tailwind CSS, and Radix/shadcn-style UI application.

## Migration changes

### Toolchain and entry points

The CRA-specific files were removed:

- `frontend/craco.config.js`
- `frontend/public/index.html`
- `frontend/package-lock.json`

`frontend/yarn.lock` was retained as the dependency lockfile. The replacement Vite entry point is `frontend/index.html`. It provides the document metadata, the `root` mount element, and the module entry script `/src/index.js`.

The migration added:

- `frontend/vite.config.js` — Vite development and production configuration.
- `frontend/vitest.config.js` — the test environment, aliases, test discovery, environment variables, and setup-file configuration.
- `frontend/src/setupTests.js` — shared test initialization and browser API compatibility behavior.

### Package scripts and dependencies

The frontend scripts now use Vite and Vitest directly:

```json
{
  "start": "vite",
  "build": "vite build",
  "test": "vitest run"
}
```

CRA, CRACO, and Jest runtime dependencies are no longer the application toolchain. Vite dependencies include `vite` and `@vitejs/plugin-react`; test dependencies include `vitest`, `jsdom`, and Testing Library packages. The project keeps `@testing-library/jest-dom` because its DOM assertions remain useful under Vitest. Yarn resolutions remain in place for the repository's known transitive dependency constraints.

### Environment variables

CRA variables named `REACT_APP_*` were migrated to Vite's `VITE_*` convention and application code now reads values through `import.meta.env`. In particular, the backend base URL is:

```dotenv
VITE_BACKEND_URL=http://localhost:8000
```

The production environment uses the same key with the production API origin. Other browser-facing integration settings, such as `VITE_VAPI_PUBLIC_KEY` and `VITE_EVE_VAPI_ASSISTANT_ID`, follow the same convention. Only variables prefixed with `VITE_` are exposed to client code; secrets must not be placed in frontend environment files. Values are embedded into the static bundle at build time.

### Vite configuration

`frontend/vite.config.js` preserves the existing JavaScript/JSX source layout while using Vite's React plugin. It also:

- Transforms `.js` files under `src/` as JSX so legacy JSX-in-`.js` modules continue to work.
- Configures the `@` source alias to resolve to `frontend/src`.
- Applies the same JSX loader behavior to dependency optimization.
- Writes production output to `frontend/build` with source maps disabled.
- Ignores generated and dependency directories in the development watcher.

The source alias is also reflected in `frontend/jsconfig.json` and is used by imports such as `@/App` and `@/lib/utils`.

### Tailwind CSS and frontend documentation

Tailwind remains the styling foundation. `tailwind.config.js` continues to scan the frontend source files, `postcss.config.js` continues to load Tailwind and Autoprefixer, and `src/index.css` loads Tailwind's base, component, and utility layers. Existing Tailwind utility usage and the Radix/shadcn-style component mappings in `components.json` were kept compatible with the new entry point.

`frontend/README.md` was updated to describe the Vite application, Yarn installation, `VITE_BACKEND_URL`, the port and backend expectations, Vitest commands, the `build/` output directory, and SPA hosting requirements. It also records that the current application is React/Vite rather than a Next.js App Router application.

### JSX and source compatibility

The application contains JSX in both `.jsx` and legacy `.js` files. The Vite JSX transform hook and dependency-optimization loader allow those modules to be compiled without a broad file-renaming exercise. The `@` alias provides stable absolute-style imports from `src`, reducing fragile relative paths and keeping UI-library imports consistent.

### Vitest compatibility changes

Existing tests were adapted to run under Vitest while preserving the Jest-style API used throughout the suite. `src/setupTests.js` exposes Vitest's `vi` implementation as `globalThis.jest`, allowing existing calls such as `jest.fn()`, `jest.spyOn()`, `jest.mock()`, fake timers, and mock cleanup to continue working during the migration.

The setup file also provides browser APIs that are incomplete or inconsistent in jsdom:

- Node's Web Crypto implementation is installed when `crypto.subtle` is unavailable.
- `File.prototype.arrayBuffer` is implemented with `FileReader` when needed.
- A minimal canvas context is supplied for modules that probe canvas capabilities during initialization.

`vitest.config.js` runs tests in `jsdom`, enables globals, loads this setup file, includes `src/**/*.{test,spec}.{js,jsx,ts,tsx}`, enables CSS processing, and supplies a deterministic test value for `VITE_BACKEND_URL`.

## Architecture and development workflow

### Development server

From `frontend/`, install the locked dependencies and start Vite:

```sh
yarn install --frozen-lockfile
yarn start
```

Vite serves the application at `http://localhost:5173` and uses `frontend/index.html` as the HTML shell. The browser loads `/src/index.js`; Vite transforms modules on demand, applies the React JSX transform, resolves the `@` alias, and updates changed modules quickly during development. The backend normally runs separately on port `8000`, and `VITE_BACKEND_URL` points frontend API calls to it.

### Production build

Create the deployable static assets with:

```sh
yarn build
```

Vite resolves and bundles the application and its CSS, applies the configured React and JSX transforms, and writes the optimized output to `frontend/build`. The output is static and should be deployed to a static host or web server configured to return the SPA entry point for client-side routes such as `/dashboard` and `/resume-editor`. Environment values are fixed when this build is created.

### Automated tests

Run the complete suite with:

```sh
yarn test
```

This invokes `vitest run`, which discovers the configured test and spec files, executes them in jsdom, loads `src/setupTests.js`, and uses the Vite resolver and aliases. For focused development, Vitest can run an individual file or test name:

```sh
yarn vitest run src/lib/__tests__/profileNormalization.test.js
yarn vitest run src/lib/__tests__/profileNormalization.test.js -t "test name"
```

Tests are colocated under `src/` and its `__tests__/` directories. When adding tests, prefer Testing Library behavior assertions and use the shared setup rather than duplicating browser-environment shims in individual files.

## Operational notes

- Use Yarn and keep `frontend/yarn.lock` synchronized; do not recreate a `package-lock.json`.
- Use `VITE_` for browser-exposed configuration and never put credentials or server secrets in client environment files.
- Preserve the `build/` output contract and configure the hosting platform for SPA fallback routing.
- Keep Vite configuration changes in `vite.config.js` and test-only behavior in `vitest.config.js` or `src/setupTests.js`.
- When changing API origins or frontend environment values, rebuild the frontend because Vite embeds them into the generated assets.

