# AI Agent Full-Stack Architectural Governance & Guardrails (agents.md)

You are an elite AI staff engineer specialized in building ultra-resilient, production-grade systems. You are working on a multi-tier, candidate-facing application comprising an **async FastAPI backend**, a **Client-Side React SPA frontend**, and **Docker** orchestration.

Our core priority is **system robustness**. We operate on a zero-tolerance policy for unhandled edge cases, data leaks, memory leaks, and structural inconsistencies. You are explicitly authorized and expected to perform **progressive refactoring** to bring any legacy code you touch into compliance with these principles.

---

## 🚀 Frontend Stack Directive: Vite + React SPA

The frontend is a **Vite + React single-page application (SPA)**. Preserve this client-rendered architecture; do not introduce server-side rendering (SSR) frameworks like Next.js. Use the following stack as the standard for frontend work:

- **Build Tool:** Vite.
- **Styling & Primitive UI:** Tailwind CSS, shadcn/ui, and Lucide Icons.
- **Server Data Management:** TanStack Query for server-owned state and Zod for runtime validation at data boundaries.
- **Client Routing:** React Router v7.

### Verified frontend baseline

These are observations about the current implementation, not claims that every recommended pattern below is already in place. Re-check the source and configuration when these facts matter to a task.

- The app uses React 19, JavaScript/JSX, Vite 7, and React Router DOM v7. `src/index.js` mounts with `ReactDOM.createRoot()`; `src/App.js` eagerly imports the four route pages and redirects unknown paths to `/`. There is currently no route-level `React.lazy`/`Suspense` splitting or app-level React error boundary.
- `vite.config.js` aliases `@` to `src/`, transforms `.js` files in `src/` as JSX, writes builds to `build/`, and disables source maps. Vite configuration and `index.html` are the app entry/build path.
- `package.json` defines only `start`, `build`, and `test` scripts. Source is JavaScript/JSX: there is no TypeScript compiler or type-check script. ESLint packages are installed, but no lint script is defined. The repository has not yet configured its chosen shared quality pipeline: `pre-commit` with Biome, `tsc`, and Vitest for frontend checks. This is the intended direction, not a claim that those hooks or commands are already installed.
- The app makes backend requests with Axios and primarily manages request state in React components. TanStack Query is installed and its provider is mounted with default query options, but there are currently no app-level `useQuery` or `useMutation` calls.
- Zod currently validates the configured backend URL in `src/lib/apiConfig.js`; it is not currently used as a general API-response, form, or browser-storage validation layer. The shared Axios response interceptor uses the helper in `src/lib/apiError.js` for notifications and handles candidate-session 401s; it does not parse a standard API error `code` field.
- Onboarding workflow data and the candidate bearer token are stored in `localStorage`; some features also use `sessionStorage`. Restored onboarding JSON is merged with defaults without schema validation, and storage failures are caught by the storage helper.
- Voice intake uses the Vapi browser SDK through `src/hooks/useVapi.js` and stops the SDK call during unmount cleanup. The frontend source does not directly acquire `MediaStream`s or create `AudioContext`s; if direct media/audio resources are added, their ownership and cleanup must be explicit.
- Vitest is configured with JSDOM, React Testing Library, and `src/setupTests.js`; `yarn test` runs the suite. No frontend format, lint, type-check, or coverage command is currently defined in `package.json`.

### 1. Zero-Disruption Local SPA Patterns
- Preserve the browser-rendered SPA architecture and mount through `src/index.js` or `src/main.jsx` using `ReactDOM.createRoot()`.
- Keep client-side route definitions in `App.js` using React Router v7, with a deliberate fallback for unknown paths.
- Keep browser-only APIs and initialization in browser-executed modules; do not add server-component or SSR assumptions.

### 2. Vite Configuration & Environment
- Manage compilation and bundling through `vite.config.js` and the Vite HTML entry point.
- Use Vite-native `import.meta.env.VITE_*` variables for client-exposed configuration. Never put backend credentials, OAuth client secrets, or other server secrets in frontend environment variables.
- Validate configuration and untrusted runtime data with Zod at the relevant boundary; do not treat compile-time configuration as a substitute for runtime validation.

---

## 🏛️ Core Architectural Principles

### 1. Separation of Concerns & Clean Architecture
- **Backend:** Decouple your system layers completely. Frameworks, controllers, and database engines must never mingle.
  - *API Layer (FastAPI)* handles HTTP protocols, parameters, payload validations, and authentication.
  - *Service Layer* owns pure business workflows and defines transaction limits.
  - *Data Access Layer (SQLAlchemy)* strictly governs database interactions and data mapping.
- **Frontend:** Maintain clear boundaries between UI layout, local state machines, and network layers.
  - *Layout & View Layer (React Components)* translates states into intuitive, accessible visual interfaces.
  - *Routing Layer (React Router v7)* manages client-side page transitions and route definitions via `BrowserRouter`.
  - *State & Query Layer (TanStack Query)* should manage server-owned cache and asynchronous states in new or migrated query flows; use local React state for transient UI state. The current screens primarily use Axios and component state.
  - *Runtime Validation (Zod)* should validate untrusted API, form, and persisted-browser data at boundaries as those flows are added or changed. Current Zod use is limited to backend URL configuration.
  - *Client Layer (Axios)* centralizes outbound HTTP behavior. The current JavaScript client does not provide compile-time request/response typing.

### 2. Full-Stack Reality Matrix & Target Modules
- **Backend Role:** The backend owns persistent data access, short-lived code exchanges, session signing, and external Google/LinkedIn OAuth callback resolution.
- **Frontend Role:** The frontend is a substantial, highly interactive product client managing candidate workspaces. Respect the state lifecycles in voice intake and resume editing. Voice intake currently delegates the call lifecycle to the Vapi Web SDK; do not assume the component directly owns `MediaStream` or Web Audio resources unless the implementation adds them.

### 3. End-to-End Type Rigor & Data Boundary Safety
- Validate untrusted data at relevant runtime boundaries; do not treat JavaScript values as statically typed. The current frontend is JavaScript/JSX with no TypeScript compiler, and current Zod coverage is limited. Add validation at the boundary when creating or changing data flows.
- Where TypeScript is introduced with an explicit project configuration, map its types to backend contracts and run the configured type check. Do not claim compile-time contract verification for the current JavaScript frontend.

### 4. Progressive Refactoring Mandate (The Boy Scout Rule)
- When modifying an existing file or component, evaluate its code quality. If it uses legacy anti-patterns (e.g., synchronous I/O in async paths, unhandled database drops, missing type signatures, or improper spacing), you must refactor that block to match these architectural principles. Update adjacent tests instantly.

---

## 🐍 Backend Architecture & Operations (FastAPI & SQLAlchemy)

### 1. Server Operations & Concurrency (Async Rules)
- **Asynchronous Execution:** Every I/O bound activity—including database transactions, network calls via `httpx`, and file tasks—must run asynchronously (`async/await`). Never introduce blocking, synchronous hooks in async context paths.
- **Concurrency Decision Engine:**
  - Route uses awaitable, non-blocking I/O ➡️ Use `async def`.
  - Route uses blocking I/O (no async client exists) ➡️ Use standard `def` (FastAPI automatically runs this in an external threadpool).
  - Mixed paths ➡️ Use `async def` + `from fastapi.concurrency import run_in_threadpool` for the blocking portions.
  - CPU-bound tasks (>50ms compute) ➡️ Offload completely to a background process/task runner.

### 2. Implementation Standards & Design Patterns
- **Repository / Service Pattern:** Business actions must execute inside dedicated service classes. Wrap multi-step mutating operations inside isolated database transactions (`async with db.begin():`). Trap database constraint failures (`IntegrityError`, `UniqueViolation`) within the service layer before they reach the controller.
- **Modern FastAPI Dependency Injection:** Always leverage the modern `Annotated[T, Depends(...)]` form for path injection. Avoid the legacy default-argument assignment (`dep: T = Depends(...)`).
- **Strict Layout Standards:**
  - Enforce **exactly 4 spaces** for indentation. Absolutely no tabs.
  - Source files must be strictly encoded in **UTF-8**. Always define explicit encoding options during text or stream manipulations (`open(..., encoding='utf-8')`).
- **Linting & Quality:** Rely completely on `ruff` for all formatting (`ruff format`) and lint fixes (`ruff check --fix`).

### 3. Data Storage & Query Integrity
- Avoid raw string SQL execution. Utilize SQLAlchemy 2.0's async Select/Insert expression builders exclusively.
- Implement explicit eager-loading rules (`selectinload`, `joinedload`) to aggressively prevent N+1 query execution patterns.

### 4. Enterprise Security Protocols
- **Auth Architecture:** Authenticate via backend-managed OAuth2 providers (Google/LinkedIn) using custom secure sessions.
- **Session Tokens:** Issue tokens via **PyJWT** (`import jwt`), *never* use the unmaintained `python-jose` library. Deliver keys via strictly configured HTTP-only cookies (`HttpOnly`, `Secure`, `SameSite=Lax`).
- **Cryptographic Sanity:** Never write home-grown encryption routines. Rely completely on trusted libraries like `cryptography` or `passlib` with robust algorithms (Bcrypt/Argon2id).

---

## ⚛️ Frontend Architecture & User Experience (React SPA & Vite)

### 1. Build Pipeline & Browser Entry Point
- **Vite Lifecycle:** All compilation and bundling configurations must use `vite.config.js`. Avoid custom fallback bundlers.
- **Application Entry Point:** The application mounts purely in the browser environment via `src/index.js` or `src/main.jsx` using `ReactDOM.createRoot()`. Any browser-specific initialization (e.g., session initialization or API client setup) must occur here before rendering; this is client rendering, not hydration.

### 2. Client-Side Routing & Bundle Optimization
- **Routing Engine:** All navigation paths are strictly runtime controlled using React Router v7's `BrowserRouter` inside `App.js`. Ensure that fallback wildcard mappings (e.g., `*` routes) are declared to cleanly trap broken paths or show dedicated client 404 views.
- **Route Splitting & Performance:** Route pages are currently eager imports in `src/App.js`. Do not describe the app as code-split or add complexity without a demonstrated performance need. If implementing route splitting, use `React.lazy` with a deliberate `<Suspense>` fallback and verify navigation/error behavior. Keep `VoiceIntake.jsx` within its existing page/component ownership rather than treating it as a route.

### 3. Implementation Standards, State & UX Safety
- **Visual Uniformity:** Build interfaces using Tailwind CSS tokens and the shared shadcn/ui component foundation. Use Lucide Icons for interface icons. Avoid one-off styles when an existing design token or shared primitive applies.
- **Server Data Management:** Use TanStack Query for backend-owned data, including query caching, loading/error states, and mutation invalidation. Keep ephemeral interaction state local to the owning component or feature.
- **Runtime Validation:** Use Zod or an equivalent explicit runtime guard to validate untrusted API responses, user-controlled form values, and browser storage before relying on them. Current coverage is limited; add validation incrementally at boundaries touched by a change.
- **Feature-Driven Co-location Pattern:** The current source uses `src/pages/`, `src/components/`, `src/components/ui/`, `src/hooks/`, and `src/lib/`; it is not organized under `src/features/`. Follow the existing structure for focused changes and co-locate feature-only code when introducing or substantially restructuring a feature. Do not move unrelated legacy files solely to enforce a target layout.
- **Re-render Optimization:** Prevent interface stutters in rich components like `ResumeEditor.jsx`. Derive temporary computed values during render passes; do not store duplicative state variables. Use functional setState updates (`setCount(c => c + 1)`) and implement lazy state initialization (`useState(() => initialize())`) for heavy operational states.
- **Strict Code Style Layout:**
  - Enforce **exactly 2 spaces** for indentation.
  - All source files must be saved with **UTF-8** encoding.
- **Data Validation Boundary:** Data coming from external network proxy calls or mutable form entries must be verified at the runtime boundary using a strict validation engine like **Zod**.

### 4. Device & Stream Management Guardrails
- Components that directly acquire browser media streams, microphones, audio nodes, or contexts must release resources they own during teardown (for example, stop acquired tracks and disconnect/close audio resources). Voice intake currently uses Vapi's SDK and stops its active SDK call from `useVapi` cleanup; do not add duplicate direct-media cleanup unless the frontend itself begins acquiring those resources.

---

## 🔌 API Contract, Shared Conventions & Resilient Error Strategy


### 1. Case Alignment & Serialization Contracts

- Python backend variables operate strictly in `snake_case`.
- TypeScript/JavaScript frontend elements operate strictly in `camelCase`.
- **The Serialization Bridge:** Configure Pydantic schemas using a global alias generator to automatically convert backend `snake_case` properties into valid frontend `camelCase` keys during network transit.
- **Modern Serialization:** Do not use deprecated Pydantic v1 functions (`.dict()`, `json_encoders`). Use `@field_serializer` or Pydantic v2 configurations.

### 2. Robust, Leak-Proof Error Handling Contract

We prioritize user empathy and system security. Technical diagnostics must never leak to candidate browsers.

- **Uniform Error Envelope:** All unsuccessful API queries must adhere to a fixed layout contract:

  ```json
  {
    "success": false,
    "code": "SPECIFIC_ERROR_ENUM",
    "message": "A helpful, human-readable message instructing the candidate on what to do next.",
    "details": null
  }
  ```

- **Error Obfuscation Middleware:** Maintain a global catch-all exception handler in FastAPI (`main.py`). If an uncaught exception triggers, trap it, securely log the full traceback to server terminal diagnostics (using Python logging), and send a sanitised 500 Internal Server Error envelope to the client.
- **Client Interceptor Integration:** Route Axios failures through the shared response interceptor and safe notification helper. The current helper maps status codes and cautiously accepts string `detail`/`message` values; it does not consume a uniform `code` envelope, and there is no app-level React error boundary. Preserve the existing API contract when changing either side; add structured error-code parsing or a render error boundary only together with the corresponding contract/UI change.

### 🤖 AI Guardrails: Prohibited Anti-Patterns & Code Smells

When reviewing modifications, you must actively reject and rewrite code that exhibits these full-stack anti-patterns:

#### 🐍 Backend Anti-Patterns (Python & FastAPI)

| Anti-pattern to reject | Why it breaks our app | Required resilient fix |
| --- | --- | --- |
| `requests.get(...)` inside `async def` | Blocks the Python event loop entirely. | Use `httpx.AsyncClient` with `await`. |
| `time.sleep()` / `open()` inside `async def` | Freezes the event loop for all active workers. | Use `asyncio.sleep()`, `aiofiles`, or run via a threadpool. |
| `from jose import jwt` | Library is completely unmaintained. | Use `import jwt` (PyJWT). |
| `Field(ge=18, default=None)` | Direct constraint contradiction. | Define an optional integer and use `Field(default=None, ge=18)`. |
| Catching general `Exception` inside routes | Masks underlying bugs, turning internal 500s into silent failures. | Catch specific domains or let our global middleware format it securely. |
| Calling sync ORM methods inside `async def` | Blocks the loop and can cause pool deadlocks. | Use `AsyncSession` expressions exclusively. |


#### ⚛️ Frontend Anti-Patterns (JavaScript/TypeScript & React Vite SPA)

| Anti-pattern to reject | Why it breaks our app | Required resilient fix |
| --- | --- | --- |
| Introducing `"use client"` blocks | Dead syntax. We are a pure client-side SPA; there are no server components. | Delete the string entirely. Code flows directly inside browser-bound modules. |
| Hardcoded strings like `process.env.VAR` | Deprecated syntax in Vite. Will evaluate to undefined and crash layouts silently. | Convert explicitly to use `import.meta.env.VITE_VAR`. |
| Using full-page navigation for internal SPA routes | Causes unnecessary document reloads and bypasses router state. External OAuth/provider links are not internal routes. | Use React Router `<Link>`/`useNavigate()` for same-app navigation; retain browser navigation where crossing origins or completing an external auth flow requires it. |
| Spreading unvalidated external data into UI props | In JavaScript there is no compile-time prop checking to make such spreads safe; unexpected data can still create incorrect UI behavior. | Validate external data at its boundary and pass the explicitly needed values to components. Spreading known, locally constructed component props is not categorically prohibited. |
| Catching render errors ad hoc inside component render logic | Can obscure the original failure and create inconsistent fallback behavior. | Use a deliberate React error boundary for render failures where needed; note that the current app does not yet have an app-level boundary. |
| Leaving owned event listeners or media/audio resources active after teardown | Can retain component references or hardware resources beyond their intended lifecycle. | Remove listeners and release resources from the lifecycle that created them; use effect cleanup for effect-owned resources. Vapi call cleanup currently lives in `useVapi`. |


### 🧪 Comprehensive Testing & CI Automation Guardrails

An architectural change is incomplete without valid testing coverage and metadata standards.

### 1. Test Automation Imperatives

- **Backend (pytest):** Aim for 80% path coverage across business domains. Use `pytest-asyncio` alongside isolated Docker containers to perform high-fidelity database testing. Mock external OAuth providers cleanly using standard mocking utilities. Test network endpoints using `httpx.AsyncClient` paired with `ASGITransport`.
- **Frontend (Vitest / React Testing Library):** Run the configured `yarn test` suite and add focused tests for changed behavior, including relevant success and failure states. Vitest uses JSDOM and React Testing Library. As the shared quality pipeline is established, use `pre-commit` to run the selected frontend checks: Biome formatting/linting, `tsc` over an explicitly configured application/test scope, and Vitest. Do not claim coverage thresholds or checks as active until they are configured and runnable.

### 2. Git Automation & Conventional Commits

All code changes proposed or executed by you must follow the repository's documented quality gates:

- **Commit message structure:** Every commit must strictly conform to the Conventional Commits specification:
  - `feat(domain): add LinkedIn OAuth authentication callback handling`
  - `fix(api): resolve transaction pool leakage on error intercept`
  - `refactor(auth): bring legacy user schema into Pydantic v2 compliance`
- **Shared pre-commit hooks:** Use [`pre-commit`](https://pre-commit.com) as the common hook framework for frontend and backend checks; do not introduce Husky/lint-staged. The intended frontend checks are Biome formatting/linting, `tsc` for the configured gradual JavaScript/TypeScript scope, and Vitest. The repository does not yet have a `.pre-commit-config.yaml` or configured Biome/TypeScript checks, so these checks must not be reported as active until implemented. Hooks must fail on errors and avoid changing or staging unrelated files.
- **Backend checks:** Run the configured Ruff checks and relevant pytest tests for backend changes.
- **CI parity:** Keep pull-request checks aligned with the selected quality pipeline. Today the frontend defines `yarn build` and `yarn test`; as `pre-commit`, Biome, and TypeScript checks are configured, run the same applicable checks in CI rather than treating local hooks as a replacement.
- **Incremental type coverage:** The frontend currently has no TypeScript type-check configuration and is JavaScript/JSX. Configure `tsc` with an explicit gradual scope (including checked JavaScript and/or migrated TypeScript as appropriate), then report only the files actually checked. Do not imply current type safety or bypass errors with broad suppressions.
- **Hook safety:** Hook commands must propagate non-zero exit codes, run without network access after dependencies are installed, and avoid changing or staging unrelated unstaged files. Never bypass required checks with `--no-verify`.
