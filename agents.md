# AI Agent Full-Stack Architectural Governance & Guardrails (agents.md)

You are an elite AI staff engineer specialized in building ultra-resilient, production-grade systems. You are working on a multi-tier stack comprising a **FastAPI backend (Python 3.11+)** with **Async SQLAlchemy 2.0**, a **Next.js 14+ App Router frontend (TypeScript)**, and **Docker** orchestration.

Our core priority is **system robustness**. We operate on a zero-tolerance policy for unhandled edge cases, technical data leaks, and structural inconsistencies. You are explicitly authorized and expected to perform **progressive refactoring** to bring any legacy code you touch into compliance with these principles.

---

## 🏛️ Core Architectural Principles

### 1. Separation of Concerns & Clean Architecture
- **Backend:** Decouple your system layers completely. Frameworks, controllers, and database engines must never mingle.
  - *API Layer (FastAPI)* handles HTTP protocols, parameters, payload validations, and authentication.
  - *Service Layer* owns pure business workflows and defines transaction limits.
  - *Data Access Layer (SQLAlchemy)* strictly governs database interactions and data mapping.
- **Frontend:** Maintain clear boundaries between UI layout, state machine orchestration, and network layers.
  - *Layout & View Layer (React Components)* translates states into intuitive, accessible visual interfaces.
  - *State & Query Layer (TanStack Query)* manages server-side cache and asynchronous states.
  - *Client Layer (Network SDK)* acts as the strict typing engine for outbound API requests.

### 2. End-to-End Type Rigor & Data Boundary Safety
- All data entering or exiting a boundary must be explicitly validated. Never allow unchecked objects or dictionaries (`any` in TypeScript, un-typed `dict` or `Any` in Python) to flow through the system.
- Compile and map types symmetrically between Python and TypeScript to ensure immediate, compile-time detection of broken contracts.

### 3. Progressive Refactoring Mandate
- **The Boy Scout Rule:** When modifying an existing file or component, evaluate its code quality. If it uses legacy anti-patterns (e.g., synchronous I/O, missing type signatures, improper indentation, or direct SQL execution in routes), you must refactor that block to match these architectural principles. Ensure adjacent tests are updated seamlessly to reflect your improvements.

---

## 🐍 Backend Architecture & Operations (FastAPI & SQLAlchemy)

### 1. Server Operations & Concurrency (Async Rules)
- **Asynchronous Execution:** Every I/O bound activity—including database transactions, network calls, and file tasks—must run asynchronously (`async/await`). Never introduce blocking, synchronous hooks in async context paths.
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

## ⚛️ Frontend Architecture & User Experience (Next.js App Router)

### 1. Visual Layout, Styling & UI Foundations
- **Design Uniformity:** Build interfaces strictly leveraging Tailwind CSS token rules mapped to a clean `shadcn/ui` foundation. Never hardcode inline colors, padding values, or pixel dimensions.
- **Accessibility & UX Safety:** All dynamic components must fulfill basic accessible layout rules (ARIA standard targets, strict semantic tags). Layout changes must use skeleton loaders to eliminate jarring Layout Shifts (CLS).
- **Feature-Driven Co-location Pattern:** Avoid cluttering the global components directory. Keep domain-specific logic, isolated hooks, and feature-only subcomponents contained directly inside self-contained feature folders (e.g., `src/features/auth/`). Global UI directories are strictly reserved for pure, atomic presentation primitives.

### 2. Framework Implementation Standards
- **Server-First Execution:** Default page layouts to Next.js Server Components to minimize hydration overhead and pre-render contents.
- **Client Transitions:** Restrict `"use client"` scopes to terminal interactive controls, forms, or view hooks. 
- **Strict Code Style Layout:**
  - Enforce **exactly 2 spaces** for indentation.
  - All source files must be saved with **UTF-8** encoding.
- **Data Validation Boundary:** Data coming from or pushing to external points (including backend proxy calls, local storage caches, or mutable form entries) must be verified at the runtime boundary using a strict validation engine like **Zod**.

### 3. State Management & Resilient Data Fetching
- Maintain server state synchronization via TanStack Query. Utilize declarative parameters (`searchParams`) for filter states to preserve bookmarkable browser URL states.
- Wrap mutations with optimistic UI adjustments where viable to ensure interactions feel instantaneous.

---

## 🔌 API Contract, Shared Conventions & Resilient Error Strategy

### 1. Case Alignment & Serialization Contracts
- Python backend variables operate strictly in `snake_case`.
- TypeScript frontend elements operate strictly in `camelCase`.
- **The Serialization Bridge:** Configure Pydantic schemas using a global alias generator to automatically convert backend `snake_case` properties into valid frontend `camelCase` keys during network transit. 
- **Modern Serialization:** Do not use deprecated Pydantic v1 functions (`.dict()`, `json_encoders`). Use `@field_serializer` or Pydantic v2 configurations.

### 2. Robust, Leak-Proof Error Handling Contract
We prioritize user empathy and system security. Technical diagnostics must never leak to client browsers.

- **Uniform Error Envelope:** All unsuccessful API queries must adhere to a fixed layout contract:
  ```json
  {
    "success": false,
    "code": "SPECIFIC_ERROR_ENUM",
    "message": "A helpful, human-readable message instructing the user on what to do next.",
    "details": null
  }
  ```
- **Error Obfuscation Middleware:** Maintain a global catch-all exception handler in FastAPI (`main.py`). If an uncaught exception triggers, trap it, securely log the full traceback to server terminal diagnostics (using Python `logging`), and send a sanitised `500 Internal Server Error` envelope to the client.
- **Client Interceptor Integration:** The frontend must process all non-2xx status states through a centralized network response interceptor. Parse the standard code string and pass it cleanly into human-centric UI alerts, Toast panels, or explicit React `ErrorBoundary` views.

---

## 🤖 AI Guardrails: Prohibited Anti-Patterns & Code Smells

When reviewing modifications, you must actively reject and rewrite code that exhibits these full-stack anti-patterns:

### 🐍 Backend Anti-Patterns (Python & FastAPI)

| Anti-Pattern to Reject | Why It Breaks Our App | The Required Resilient Fix |
| --- | --- | --- |
| `requests.get(...)` inside `async def` | Blocks the Python event loop entirely. | Use `httpx.AsyncClient` with `await`. |
| `time.sleep()` / `open()` inside `async def` | Freezes the event loop for all active workers. | Use `asyncio.sleep()`, `aiofiles`, or run via a threadpool. |
| `from jose import jwt` | Library is completely unmaintained. | Use `import jwt` (PyJWT). |
| `Field(ge=18, default=None)` | Direct constraint contradiction. | Define cleanly as `int \| None = Field(default=None, ge=18)`. |
| Catching general `Exception` inside routes | Masks underlying bugs, turning internal 500s into silent failures. | Catch specific domains or let our global middleware format it securely. |
| Calling sync ORM methods inside `async def` | Blocks the loop and can cause pool deadlocks. | Use `AsyncSession` expressions exclusively. |

### ⚛️ Frontend Anti-Patterns (TypeScript & Next.js)

| Anti-Pattern to Reject | Why It Breaks Our App | The Required Resilient Fix |
| --- | --- | --- |
| Injecting "use client" at page/layout roots | Defeats the server-rendering paradigm, slowing down the layout paint. | Keep layouts as Server Components. Pass client components down as children. |
| Hardcoded environment strings (process.env.VAR) | Causes silent runtime breakages if configurations change or variables drop out. | Route variables through a validation wrapper file (src/config/env.ts) using Zod. |
| Spreading unknown object properties (<Comp {...props} />) | Discards TypeScript compile-time protections, leading to unstable layout shifts. | Declare all properties explicitly. Use typed variant systems like cva. |
| Catching raw layout errors inside components | Distorts layout flows and risks throwing cascading application white-screens. | Use Next.js standard error.tsx file paths paired with React ErrorBoundary. |

## 🧪 Comprehensive Testing & CI Automation Guardrails

An architectural change is incomplete without valid testing coverage and metadata standards.

### 1. Test Automation Imperatives

- **Backend (pytest):** Aim for 100% path coverage across business domains. Use pytest-asyncio alongside isolated Docker containers to perform high-fidelity database testing. Mock external OAuth providers cleanly using standard mocking utilities. Test network endpoints using httpx.AsyncClient paired with ASGITransport—never use unmaintained third-party async test clients.
- **Frontend (Vitest / Playwright):** Build robust test cases confirming component behavior under both success contexts and complex network failures. Verify that error fallback skeletons render correctly without exposing system infrastructure data.

### 2. Git Automation & Conventional Commits

All code changes proposed or executed by you must follow the structural format rules tracked by our pre-commit hook architecture (e.g., Husky, lint-staged):

- **Commit message structure:** Every commit must strictly conform to the Conventional Commits specification:
  - `feat(domain): add LinkedIn OAuth authentication callback handling`
  - `fix(api): resolve transaction pool leakage on error intercept`
  - `refactor(auth): bring legacy user schema into Pydantic v2 compliance`
- **Hook rules:** Ensure that code satisfies lint blocks (`ruff check` and ESLint) and types compile cleanly (`tsc --noEmit`) before proposing or checking in files. Never attempt to bypass automation checks (`--no-verify`).
