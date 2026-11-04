# Contributing

This repository uses a root-managed `pre-commit` configuration. The frontend is
a Vite/React SPA; the quality checks are incremental and do not imply that the
whole JavaScript application is type-checked.

## One-time setup

From the repository root, install the frontend dependencies with the checked-in
Corepack/Yarn workflow:

```powershell
corepack yarn --cwd frontend install --frozen-lockfile
```

Using the repository's established Python environment, install `pre-commit`
if it is not already available:

```powershell
python -m pip install pre-commit
python -m pre_commit --version
python -m pre_commit install
```

The last command installs the Git hook. Do not add Husky or lint-staged.

## Pre-commit checks

Run the complete configured hook set manually with:

```powershell
python -m pre_commit run --all-files
```

The root `.pre-commit-config.yaml` is the source of truth:

- Frontend JavaScript, JSX, TypeScript, JSON, and CSS files run Biome only for
  the filenames supplied by pre-commit (normally staged frontend files).
- Frontend changes run the TypeScript helper. `frontend/tsconfig.json` includes
  only `src/lib/apiConfig.js`; the rest of the frontend is not type-checked.
- Changed frontend test files run only those staged test files in pre-commit.
  Frontend configuration/source changes without staged tests do not trigger a
  test run. This keeps the commit hook focused on the staged change and avoids
  attributing unrelated baseline failures to an infrastructure-only commit.
- Gitleaks runs for every commit and scans staged Git content with
  `gitleaks git --staged --redact`.
- Backend-only changes do not run frontend Biome, TypeScript, or Vitest hooks.
  Gitleaks still runs. This configuration does not replace backend test or
  lint commands.

Hooks fail on non-zero exit codes and the helper scripts do not stage or modify
files.

## Biome

Check the frontend source without modifying files:

```powershell
corepack yarn --cwd frontend lint
```

The staged-file equivalent is:

```powershell
python scripts/biome_staged.py frontend/src/App.js
```

To intentionally apply Biome fixes to frontend source files, run this command
only after reviewing its scope. It modifies files:

```powershell
Push-Location frontend
node node_modules/@biomejs/biome/bin/biome check --write src
Pop-Location
```

Do not use `--write` in the pre-commit hook or as a substitute for reviewing
unrelated baseline violations.

## TypeScript boundary

Run the configured check with:

```powershell
corepack yarn --cwd frontend typecheck
```

The exact boundary is `frontend/tsconfig.json`:

```json
"include": ["src/lib/apiConfig.js"]
```

This is a strict checked-JavaScript boundary for that file only. It is not a
claim that the entire frontend is type-checked.

## Vitest

Run the staged-test pre-commit behavior:

```powershell
python scripts/frontend_quality.py staged-test frontend/src/lib/__tests__/apiError.test.js
```

Run the full suite and enforce coverage:

```powershell
corepack yarn --cwd frontend test
```

Run one test file:

```powershell
corepack yarn --cwd frontend exec vitest run src/pages/__tests__/Dashboard.routing.test.jsx
```

Filter by test name:

```powershell
corepack yarn --cwd frontend exec vitest run -t "routing"
```

Run coverage separately:

```powershell
corepack yarn --cwd frontend test:coverage
```

`frontend/vitest.config.js` configures the V8 provider and requires 80% for
statements, branches, functions, and lines. The coverage boundary excludes
bootstrap/fixture modules, data-only constants, generic Radix UI primitives,
and the unused profile-narrative helper; these modules do not represent an
independent product flow. User-facing pages and product components remain in
the boundary. The pre-commit hook runs relevant staged tests; the command above
is the required full-validation gate for coverage.
The latest working-tree full-suite result is 81.88% statements, 80.43% branches,
80.04% functions, and 81.88% lines. The configured requirement is 80% for all
four metrics. Do not lower thresholds or add exclusions for user-facing
behavior.

## Gitleaks

Gitleaks must be installed and available on `PATH`:

```powershell
gitleaks version
python scripts/run_gitleaks.py
```

The helper uses the repository `.gitleaks.toml` when present and otherwise
relies on Gitleaks defaults. Do not add broad allowlists; investigate and
narrowly document any verified false positive.

If Gitleaks is missing, install it using the approved development environment
and confirm that `gitleaks version` works in the same PowerShell session. If a
scan reports a secret, remove or rotate the secret and investigate its history;
do not bypass the hook.

## Production build

Validate the frontend production build with:

```powershell
corepack yarn --cwd frontend build
```

The generated static output is written to `frontend/build/`. The build does not
replace the test, type-check, or pre-commit checks.

## Troubleshooting

- **Missing dependencies:** rerun `corepack yarn --cwd frontend install --frozen-lockfile`.
- **Biome violations:** inspect the reported staged file. Use the check command
  first; use `--write` only for an intentional, reviewed change.
- **TypeScript failures:** confirm that the failure is within the single
  configured `src/lib/apiConfig.js` boundary and inspect the reported type.
- **Failing tests:** run the affected file or `-t` filter first, then the full
  suite. Existing failures are not evidence that coverage thresholds should be
  weakened.
- **Gitleaks failures:** verify `gitleaks version`, inspect the redacted finding,
  remove or rotate real secrets, and use only narrowly justified configuration
  exceptions.
- **Hook not running:** confirm the hook exists with `python -m pre_commit install`
  and inspect applicable filenames with `python -m pre_commit run --all-files`.

Before opening a change, run the relevant staged checks, the frontend build, and
the tests that apply to the change. Do not commit or push from automated review
work.
