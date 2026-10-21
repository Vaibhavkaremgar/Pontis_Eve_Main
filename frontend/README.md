# Pontis Eve frontend

The frontend is the candidate-facing React application for Pontis Eve. It provides sign-in and onboarding, the candidate dashboard and Living Profile, job recommendations and tracking, and resume editing. It is built with React 19, React Router, CRACO/Create React App, Tailwind CSS, and reusable Radix-based UI components.

The backend API lives in [`../backend`](../backend/README.md). For product context, see the repository-level [`README.md`](../README.md) and [`memory/PRD.md`](../memory/PRD.md).

## Requirements

- Node.js and npm
- The Pontis Eve backend running locally or reachable at a configured backend URL

## Local development

From this directory, install dependencies and start the development server:

```sh
npm ci
npm start
```

The application runs at [http://localhost:3000](http://localhost:3000). Configure the backend URL before starting the app. Create a `frontend/.env.local` file:

```dotenv
REACT_APP_BACKEND_URL=http://localhost:8000
```

The checked-in `frontend/.env.production` points to the production API. Create React App embeds variables beginning with `REACT_APP_` in the client bundle; do not put server secrets or OAuth client secrets in frontend environment files.

Voice onboarding uses Vapi. To enable it in a local frontend, set the public Vapi key and assistant ID:

```dotenv
REACT_APP_VAPI_PUBLIC_KEY=
REACT_APP_EVE_VAPI_ASSISTANT_ID=
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

- `src/pages/` contains route-level screens and coordinates larger flows.
- `src/components/` contains shared product components, including the dashboard panels, profile, job cards, billing, and onboarding voice UI.
- `src/components/ui/` contains reusable UI primitives.
- `src/lib/` contains shared API error handling, onboarding storage, profile normalization/strength, job descriptions, and other testable product logic.
- `src/hooks/` contains shared hooks, including Vapi call-state handling.
- `src/constants/testIds/` contains stable test identifiers.

Candidate onboarding/session state is managed by `src/lib/onboardingStorage.js`. The global Axios interceptors in `src/lib/candidateApiAuth.js` attach the current candidate token to candidate API calls, clear invalid candidate sessions, and provide consistent API error notifications. Keep authentication and error behavior consistent with these central helpers.

## Current frontend architecture

This repository currently uses React 19, Create React App through CRACO, JavaScript, and React Router. It is not a Next.js App Router application: it has no Next.js Server Components, `app/` directory, or TypeScript compilation pipeline. The Next.js-specific parts of [`../agents.md`](../agents.md) describe a future architectural target only if the project migrates frameworks; do not introduce Next.js conventions into the current application.

Application state is primarily implemented with component/page React hooks. `QueryClientProvider` is configured in `src/index.js` with TanStack Query, but current application data fetching is mostly manual Axios requests held in component state. When extending or refactoring server-data flows, use TanStack Query for cacheable backend state and keep transient UI state (dialogs, active tabs, drafts, and loading indicators) local to the owning component.

The current onboarding storage helper persists workflow data and the candidate bearer token in `localStorage`. Treat persisted browser data as untrusted and validate/reconcile it with the backend. `localStorage` is accessible to JavaScript; moving candidate authentication to an `HttpOnly; Secure` cookie requires coordinated backend/frontend changes and appropriate CSRF and credentialed-CORS protections. Do not change only one side of the session contract.

## Frontend standards for new and refactored code

Follow the frontend and shared boundary requirements in [`../agents.md`](../agents.md), while preserving the existing React/CRACO application architecture:

- **Separation of concerns:** Keep route/page composition in `src/pages/`, reusable product UI in `src/components/`, atomic presentation primitives in `src/components/ui/`, and shared business/data transformations in `src/lib/`. As features grow, co-locate feature-specific hooks and components rather than expanding global shared directories with feature-only code.
- **State and network boundaries:** Use TanStack Query for server-owned state, scope query keys by candidate identity, and invalidate or update relevant data after mutations. Keep temporary interaction state local. Centralize API behavior through the shared Axios setup; avoid duplicating authentication and error handling in individual components.
- **Runtime validation and contracts:** Validate data at network, form, and browser-storage boundaries. Use the installed Zod dependency for runtime validation when adding schemas. Keep backend field names in `snake_case` and frontend names in `camelCase`; perform explicit mapping where API contracts require it. New TypeScript code should use explicit types and avoid `any`; existing code is JavaScript and must not be described as type-checked.
- **UI and accessibility:** Use the Tailwind and Radix/shadcn-style component foundations already present. Prefer shared tokens and primitives over introducing new one-off styles; preserve semantic markup, accessible labels, keyboard behavior, and clear loading/error states.
- **Errors and sensitive data:** Route API failures through the shared error-notification/interceptor behavior. Do not expose backend diagnostics, secrets, OAuth credentials, or unnecessary candidate data in the UI, console, or persisted client state.
- **Code style:** Use two spaces and UTF-8. Keep new changes consistent with the existing JavaScript/JSX format unless a coordinated TypeScript migration is in scope.

## Tests, build, and lint

Run commands from `frontend/`.

Run the Jest suite once (without watch mode):

```sh
npm test -- --watchAll=false
```

Run a specific test file:

```sh
npm test -- --watchAll=false --runTestsByPath src/lib/__tests__/profileNormalization.test.js
```

Run tests matching a name or substring:

```sh
npm test -- --watchAll=false --runTestsByPath src/lib/__tests__/profileNormalization.test.js -t "test name"
```

Tests are colocated under `__tests__/` directories in `src/`. Jest runs through CRACO/React Scripts; use the `--watchAll=false` flag for a one-shot run.

The current repository is configured for Jest through React Scripts/CRACO, not Vitest. [`../agents.md`](../agents.md) names Vitest/Testing Library as the target for frontend testing; do not assume Vitest commands work until the test tooling is deliberately migrated. Add tests using the configured Jest setup in the meantime.

Create a production build with:

```sh
npm run build
```

The generated static site is written to `build/`. `package.json` does not define a lint script; no frontend lint command is configured.

## Backend integration and authentication

The API base URL is `REACT_APP_BACKEND_URL`, and application requests use the backend's `/api` routes. For local development, the frontend typically runs on port `3000` and the backend on port `8000`; the backend must allow the frontend origin through its `CORS_ORIGINS` setting.

Google and LinkedIn credentials and OAuth callbacks belong on the backend. The sign-in page redirects to backend authorization endpoints; after authentication, the backend redirects back with a short-lived exchange code, which the frontend exchanges for the candidate session. Do not handle OAuth client secrets or provider token exchange in frontend code.

Voice intake additionally requires the backend's Vapi configuration and webhook endpoint. A missing public key or assistant ID prevents the browser voice call from being configured.

The frontend currently stores the backend-issued candidate bearer token with onboarding state in browser `localStorage`. This is existing behavior, not the target session-storage recommendation in `agents.md`. Plan any move to cookie-based sessions together with backend changes, explicit CSRF protections, and production CORS configuration.

## Deployment

Build with `npm run build` and deploy the contents of `build/` to the static hosting service. Set `REACT_APP_BACKEND_URL` to the intended API origin in the build environment; frontend environment variables are baked into the static assets at build time. Configure the hosting service to serve the SPA entry point for application routes such as `/dashboard` and `/resume-editor`.
