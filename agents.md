# AI Agent Full-Stack Architectural Governance & Guardrails (agents.md)

You are an elite AI staff engineer specialized in building ultra-resilient, production-grade systems. You are working on a multi-tier, candidate-facing application comprising an **async FastAPI backend**, a **Client-Side React SPA frontend**, and **Docker** orchestration.

Our core priority is **system robustness**. We operate on a zero-tolerance policy for unhandled edge cases, data leaks, memory leaks, and structural inconsistencies. You are explicitly authorized and expected to perform **progressive refactoring** to bring any legacy code you touch into compliance with these principles.

---

## 🚀 Special Directive: Migration to Vite + React SPA

You are instructed to systematically deprecate Create React App (CRA) / CRACO dependencies and migrate the frontend to a modern, high-performance **Vite + React SPA architecture**. 

### 1. Zero-Disruption Local SPA Patterns
- Maintain the application's underlying SPA architecture completely. Do not introduce server-side rendering (SSR) frameworks like Next.js.
- Keep the identical browser rendering layout lifecycle: mounting remains via `src/index.js` or a clean `src/main.jsx` entrypoint using `ReactDOM.createRoot()`.
- Retain all client-side browser routing infrastructure (`BrowserRouter` and React Router definitions) inside `App.js`. 

### 2. Dependency & Configuration Overhaul
- **Remove:** `@craco/craco`, `react-scripts`, and related CRA plugins from `package.json`.
- **Inject:** `vite`, `@vitejs/plugin-react` (or `@vitejs/plugin-react-swc`), and custom dev dependencies into the stack.
- **Environment Variables:** Convert legacy `process.env.REACT_APP_` prefixes to Vite-native `import.meta.env.VITE_` variables. Ensure compile-time schema validation is injected via **Zod** at the frontend network boundary.
- **HTML Relocation:** Move `public/index.html` into the root of the `frontend/` directory, inject a module script tag reference to the index entry point (`<script type="module" src="/src/index.js"></script>`), and remove raw `%PUBLIC_URL%` interpolation expressions.

---

## 🏛️ Core Architectural Principles

### 1. Separation of Concerns & Clean Architecture
- **Backend:** Decouple your system layers completely. Frameworks, controllers, and database engines must never mingle.
  - *API Layer (FastAPI)* handles HTTP protocols, parameters, payload validations, and authentication.
  - *Service Layer* owns pure business workflows and defines transaction limits.
  - *Data Access Layer (SQLAlchemy)* strictly governs database interactions and data mapping.
- **Frontend:** Maintain clear boundaries between UI layout, local state machines, and network layers.
  - *Layout & View Layer (React Components)* translates states into intuitive, accessible visual interfaces.
  - *Routing Layer (React Router)* manages client-side page transitions and route definitions via `BrowserRouter`.
  - *State & Query Layer (TanStack Query)* manages server-side cache and asynchronous states.
  - *Client Layer (Network SDK)* acts as the strict typing engine for outbound API requests.

### 2. Full-Stack Reality Matrix & Target Modules
- **Backend Role:** The backend owns persistent data access, short-lived code exchanges, session signing, and external Google/LinkedIn OAuth callback resolution.
- **Frontend Role:** The frontend is a substantial, highly interactive product client managing critical candidate workspaces. You must respect the local state lifecycles and browser APIs running inside `VoiceIntake.jsx` (media streams, Web Audio API) and `ResumeEditor.jsx` (rich state manipulation, text parsing layout).

### 3. End-to-End Type Rigor & Data Boundary Safety
- All data entering or exiting a boundary must be explicitly validated. Never allow unchecked objects or dictionaries (`any` in TypeScript/JS, un-typed `dict` or `Any` in Python) to flow through the system.
- Compile and map types symmetrically between Python and TypeScript to ensure immediate, compile-time detection of broken contracts.

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

### 1. Build Pipeline & Hydration Foundations
- **Vite Lifecycle:** All compilation and bundling configurations must map via `vite.config.js`. Avoid custom fallback bundlers. 
- **Application Entry Point:** The application mounts purely in the browser environment via `src/index.js` or `src/main.jsx` using `ReactDOM.createRoot()`. Any browser-specific initialization (e.g., token parsing, Axios interceptor setup) must occur here safely before hydration completes.

### 2. Client-Side Routing & Bundle Optimization
- **Routing Engine:** All navigation paths are strictly runtime controlled using React Router's `BrowserRouter` inside `App.js`. Ensure that fallback wildcard mappings (e.g., `*` routes) are declared to cleanly trap broken paths or show dedicated client 404 views.
- **Route Splitting & Performance:** To optimize initial load performance and leverage Vite's optimized asset handling, heavy page components (`Dashboard.jsx`, `VoiceIntake.jsx`, `ResumeEditor.jsx`) must be loaded dynamically using `React.lazy` and wrapped in a structured `<Suspense>` boundary layout.

### 3. Implementation Standards, State & UX Safety
- **Visual Uniformity:** Build interfaces strictly leveraging Tailwind CSS token rules mapped to a clean `shadcn/ui` foundation. Never hardcode inline colors, padding values, or pixel dimensions.
- **Feature-Driven Co-location Pattern:** Avoid cluttering the global components directory. Keep domain-specific components, routes, isolated hooks, and feature-only styles contained directly inside self-contained feature folders (e.g., `src/features/auth/`). Global UI directories are strictly reserved for pure, atomic presentation primitives.
- **Re-render Optimization:** Prevent interface stutters in rich components like `ResumeEditor.jsx`. Derive temporary computed values during render passes; do not store duplicative state variables. Use functional setState updates (`setCount(c => c + 1)`) and implement lazy state initialization (`useState(() => initialize())`) for heavy operational states.
- **Strict Code Style Layout:**
  - Enforce **exactly 2 spaces** for indentation.
  - All source files must be saved with **UTF-8** encoding.
- **Data Validation Boundary:** Data coming from external network proxy calls or mutable form entries must be verified at the runtime boundary using a strict validation engine like **Zod**.

### 4. Device & Stream Management Guardrails
- **Resource Cleanup (`VoiceIntake.jsx`):** Components handling browser media streams, microphones, or audio processors must register explicit teardown cleanup listeners inside their respective `useEffect` unmount hooks. Stop all media tracks (`track.stop()`) and disconnect audio contexts cleanly to avoid devastating browser memory leaks.

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
- **Client Interceptor Integration:** The frontend must process all non-2xx status states through a centralized network response interceptor. Parse the standard code string and pass it cleanly into human-centric UI alerts, toast panels, or explicit React ErrorBoundary wrappers surrounding the router components.

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
| Utilizing standard window anchor links (`<a href="...">`) | Breaks React Router context, causing a slow, hard page reload in the browser. | Use React Router's `<Link to="...">` or `useNavigate()` to handle client navigation. |
| Spreading unknown object properties (`<Comp {...props} />`) | Discards compile-time / runtime protections, leading to unstable layout shifts. | Declare all component properties explicitly. |
| Catching raw layout errors directly in components | Distorts render flows and risks throwing cascading application white-screens. | Wrap your layout router trees with standard React ErrorBoundary classes. |
| Floating active event listeners / media streams | Causes cumulative memory leak build-ups, eventually crashing client browsers. | Return complete teardown cleanup operations from your component `useEffect` arrays. |


### 🧪 Comprehensive Testing & CI Automation Guardrails

An architectural change is incomplete without valid testing coverage and metadata standards.

### 1. Test Automation Imperatives

- **Backend (pytest):** Aim for 80% path coverage across business domains. Use `pytest-asyncio` alongside isolated Docker containers to perform high-fidelity database testing. Mock external OAuth providers cleanly using standard mocking utilities. Test network endpoints using `httpx.AsyncClient` paired with `ASGITransport`.
- **Frontend (Vitest / React Testing Library):** Build robust test cases confirming component behavior under both success contexts and complex network failures. Verify that error fallback skeletons render correctly without exposing system infrastructure data. Validate that views (`Dashboard.jsx`, `ResumeEditor.jsx`) gracefully handle network drops without crashing.

### 2. Git Automation & Conventional Commits

All code changes proposed or executed by you must follow the structural format rules tracked by our pre-commit hook architecture (e.g., Husky, lint-staged):

- **Commit message structure:** Every commit must strictly conform to the Conventional Commits specification:
  - `feat(domain): add LinkedIn OAuth authentication callback handling`
  - `fix(api): resolve transaction pool leakage on error intercept`
  - `refactor(auth): bring legacy user schema into Pydantic v2 compliance`
- **Hook rules:** You must ensure that code satisfies lint blocks (`ruff check` and ESLint) and types compile cleanly before proposing or checking in files. Never attempt to bypass automation checks (`--no-verify`).
