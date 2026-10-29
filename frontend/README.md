# Pontis Eve frontend

The frontend is the candidate-facing React application for Pontis Eve. It provides sign-in and onboarding, the candidate dashboard and Living Profile, job recommendations and tracking, and resume editing. It is built with React 19, React Router, Vite, Tailwind CSS, and reusable Radix-based UI components.

The backend API lives in [`../backend`](../backend/README.md). For product context, see the repository-level [`README.md`](../README.md) and [`memory/PRD.md`](../memory/PRD.md).

## Requirements

- Node.js and Yarn 1.22.22 (the package metadata pins Yarn but does not pin a Node.js version)
- The Pontis Eve backend running locally or reachable at a configured backend URL

## Tools and frameworks

This inventory is cross-checked against `package.json`, the frontend configuration, and imports in `src/`. A declared dependency is not necessarily used by the current application.

- **Application and build:** React 19, React DOM, Vite 7, and `@vitejs/plugin-react`. The browser-rendered SPA uses React Router DOM v7. Vite treats `.js` files under `src/` as JSX, provides the `@` alias for `src/`, writes production output to `build/`, and disables source maps.
- **Styling and UI:** Tailwind CSS, PostCSS, Autoprefixer, locally maintained shadcn/ui-style components built on Radix UI primitives, and `lucide-react` icons. Supporting utilities include `class-variance-authority`, `clsx`, `tailwind-merge`, and `tailwindcss-animate`.
- **Server data and validation:** `src/index.js` mounts a TanStack Query `QueryClientProvider` with a 60-second default query stale time and window-focus refetch disabled. The current app source has no `useQuery` or `useMutation` calls; screens primarily make Axios requests and keep request state in React components. Zod currently validates `VITE_BACKEND_URL` in `src/lib/apiConfig.js`; it is not a general API-response validation layer.
- **Forms and interaction:** The app uses Sonner notifications, Framer Motion, `react-resizable-panels`, and `lucide-react`. Shared UI components also import React Hook Form, Embla Carousel, `input-otp`, and `next-themes`; the latter is used by a reusable toast wrapper and does not mean the app uses Next.js.
- **Product integrations:** Voice intake uses Vapi through the `useVapi` hook, which imports the Web SDK. `libphonenumber-js` supports phone input, and DOMPurify sanitizes job-description content. Although `pdfjs-dist` is declared in `package.json`, there are no direct imports of it in `src/`; the current source does not establish that PDF.js is used for document preview.
- **Testing:** Vitest, JSDOM, and React Testing Library (`@testing-library/react`, `@testing-library/dom`, `@testing-library/user-event`, and `@testing-library/jest-dom`).
- **Additional tooling:** ESLint and React/accessibility/hooks plugins are dependencies, but `package.json` does not currently define a lint script. There is no TypeScript compiler or configured TypeScript type-check command; application source is currently JavaScript/JSX. The intended quality-gate direction is to use `pre-commit` as the shared frontend/backend hook framework, with Biome for frontend formatting/linting, `tsc` for an explicitly scoped gradual JS/TS check, and Vitest for tests. This pipeline is planned, not configured yet.

`package.json` also declares dependencies for which no direct imports were found in `src/`, including `@hookform/resolvers`, `date-fns`, `dayjs`, `html2canvas`, `jspdf`, `lodash`, `pdfjs-dist`, `recharts`, and `swr`. This does not rule out indirect use by dependencies.

## Local development

From this directory, install dependencies and start the development server:

```sh
yarn install --frozen-lockfile
yarn start
```

The application runs at [http://localhost:5173](http://localhost:5173). Configure the backend URL before starting the app. Create a `frontend/.env.local` file:

```dotenv
VITE_BACKEND_URL=http://localhost:8000
```

The checked-in `frontend/.env.production` supplies `VITE_BACKEND_URL` for production builds. `VITE_BACKEND_URL` is required and validated as an HTTP(S) URL when the API configuration module loads; the API client appends `/api`. Vite exposes variables beginning with `VITE_` in the client bundle, so do not put server secrets or OAuth client secrets in frontend environment files.

Voice onboarding uses Vapi. To enable it in a local frontend, set the public Vapi key and assistant ID:

```dotenv
VITE_VAPI_PUBLIC_KEY=
VITE_EVE_VAPI_ASSISTANT_ID=
```

These are browser-facing integration settings, not substitutes for backend webhook configuration. See the backend README for the corresponding server settings.

## Routes and application structure

Routes are composed in `src/App.js`:

| Route | Page | Purpose |
| --- | --- | --- |
| `/` | `src/pages/GoogleAuth.jsx` | Google and LinkedIn sign-in; exchanges the backend's short-lived exchange code for a candidate session |
| `/onboarding` | `src/pages/Onboarding.jsx` | Candidate profile creation and voice intake |
| `/dashboard` | `src/pages/Dashboard.jsx` | Main candidate workspace, job discovery, chat, and Living Profile |
| `/resume-editor` | `src/pages/ResumeEditor.jsx` | Resume and job-match improvement flow |

The frontend falls back to the sign-in route for unknown paths.

The route pages are currently imported directly by `src/App.js`; there is no `React.lazy`/`Suspense` route splitting in the current implementation.

- `src/pages/` contains route-level screens and coordinates larger flows.
- `src/components/` contains shared product components, including the dashboard panels, profile, job cards, billing, and onboarding voice UI.
- `src/components/ui/` contains reusable UI primitives.
- `src/lib/` contains shared API error handling, onboarding storage, profile normalization/strength, job descriptions, and other testable product logic.
- `src/hooks/` contains shared hooks, including Vapi call-state handling.
- `src/constants/testIds/` contains stable test identifiers.

Candidate onboarding/session state is managed by `src/lib/onboardingStorage.js`. The shared Axios interceptors in `src/lib/candidateApiAuth.js` attach the stored candidate token to `/api/` requests, clear the candidate session when a recognized candidate request receives a 401, and send request failures through the shared API error notification helper. Keep authentication and error behavior consistent with these central helpers.

## Current frontend architecture

This repository currently uses React 19, Vite, JavaScript, and React Router v7. It is a client-rendered SPA mounted with `ReactDOM.createRoot()` in `src/index.js`, not a Next.js App Router application: it has no Next.js Server Components, `app/` directory, or TypeScript compilation pipeline. Follow the Vite + React SPA guidance in [`../agents.md`](../agents.md); do not introduce Next.js conventions.

Application state is primarily implemented with component/page React hooks, and current application data fetching is mostly manual Axios requests held in component state. When extending or refactoring server-data flows, use TanStack Query for cacheable backend state and keep transient UI state (dialogs, active tabs, drafts, and loading indicators) local to the owning component.

The current onboarding storage helper persists workflow data and the candidate bearer token in `localStorage`. It parses stored JSON and merges it with defaults, but does not schema-validate the restored values; storage read/write errors are caught and can result in defaults or unsaved state without a notification. Treat persisted browser data as untrusted and validate/reconcile it with the backend before relying on it. `localStorage` is accessible to JavaScript; moving candidate authentication to an `HttpOnly; Secure` cookie requires coordinated backend/frontend changes and appropriate CSRF and credentialed-CORS protections. Do not change only one side of the session contract.

## Frontend standards for new and refactored code

Follow the frontend and shared boundary requirements in [`../agents.md`](../agents.md), while preserving the existing React/Vite application architecture:

- **Separation of concerns:** Keep route/page composition in `src/pages/`, reusable product UI in `src/components/`, atomic presentation primitives in `src/components/ui/`, and shared business/data transformations in `src/lib/`. As features grow, co-locate feature-specific hooks and components rather than expanding global shared directories with feature-only code.
- **State and network boundaries:** Use TanStack Query for server-owned state, scope query keys by candidate identity, and invalidate or update relevant data after mutations. Keep temporary interaction state local. Centralize API behavior through the shared Axios setup; avoid duplicating authentication and error handling in individual components.
- **Runtime validation and contracts:** Validate data at network, form, and browser-storage boundaries. Use the installed Zod dependency for runtime validation when adding schemas. Keep backend field names in `snake_case` and frontend names in `camelCase`; perform explicit mapping where API contracts require it. New TypeScript code should use explicit types and avoid `any`; existing code is JavaScript and must not be described as type-checked.
- **UI and accessibility:** Use the Tailwind and Radix/shadcn-style component foundations already present. Prefer shared tokens and primitives over introducing new one-off styles; preserve semantic markup, accessible labels, keyboard behavior, and clear loading/error states.
- **Errors and sensitive data:** Route API failures through the shared error-notification/interceptor behavior. Do not expose backend diagnostics, secrets, OAuth credentials, or unnecessary candidate data in the UI, console, or persisted client state.
- **Code style:** Use two spaces and UTF-8. Keep new changes consistent with the existing JavaScript/JSX format unless a coordinated TypeScript migration is in scope.

## Tests, build, and lint

Run commands from `frontend/`.

Run the Vitest suite once:

```sh
yarn test
```

Run a specific test file:

```sh
yarn vitest run src/lib/__tests__/profileNormalization.test.js
```

Run tests matching a name or substring:

```sh
yarn vitest run src/lib/__tests__/profileNormalization.test.js -t "test name"
```

`vitest.config.js` runs tests in JSDOM, loads `src/setupTests.js`, includes `src/**/*.{test,spec}.{js,jsx,ts,tsx}`, and enables CSS processing. Tests are colocated in source `__tests__` directories and use React Testing Library where component interaction is under test.

Create a production build with:

```sh
yarn build
```

The generated static site is written to `build/`. `package.json` defines `start`, `build`, and `test` scripts only. ESLint and its React-related plugins are installed, but there is no configured lint script or TypeScript type-check script. The planned shared `pre-commit` pipeline will add the frontend Biome and scoped `tsc` checks alongside Vitest; do not treat these as available commands until they are configured.

## Backend integration and authentication

The API base URL is `VITE_BACKEND_URL`, and application requests use the backend's `/api` routes. For local development, the frontend typically runs on Vite's port `5173` and the backend on port `8000`; the backend must allow the frontend origin through its `CORS_ORIGINS` setting.

Google and LinkedIn credentials and OAuth callbacks belong on the backend. The sign-in page redirects to backend authorization endpoints; after authentication, the backend redirects back with a short-lived exchange code, which the frontend exchanges for the candidate session. Do not handle OAuth client secrets or provider token exchange in frontend code.

Voice intake additionally requires the backend's Vapi configuration and webhook endpoint. A missing public key or assistant ID prevents the browser voice call from being configured.

The frontend currently stores the backend-issued candidate bearer token with onboarding state in browser `localStorage`. This is existing behavior, not the target session-storage recommendation in `agents.md`. Plan any move to cookie-based sessions together with backend changes, explicit CSRF protections, and production CORS configuration.

## Deployment

Build with `yarn build` and deploy the contents of `build/` to the static hosting service. Set the configured Vite backend URL to the intended API origin in the build environment; frontend environment variables are baked into the static assets at build time. Configure the hosting service to serve the SPA entry point for application routes such as `/dashboard` and `/resume-editor`.
