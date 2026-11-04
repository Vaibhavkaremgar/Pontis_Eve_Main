# Here are your Instructions

Contributor setup and repository quality checks are documented in
[`CONTRIBUTING.md`](CONTRIBUTING.md). The root pre-commit configuration is the
sole Git-hook manager; frontend checks include staged-file Biome validation,
the explicitly scoped TypeScript check, Vitest with 80% coverage thresholds,
and Gitleaks. The TypeScript boundary currently covers only
`frontend/src/lib/apiConfig.js`.
# LinkedIn sign-in

The backend implements LinkedIn OpenID Connect at `GET /api/auth/linkedin/login`
with the callback `GET /api/auth/linkedin/callback`. Configure
`LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET`, and `LINKEDIN_REDIRECT_URI` on
the backend only. The redirect URI must exactly match the callback registered in
the LinkedIn Developer Portal (for example,
`https://api.example.com/api/auth/linkedin/callback`). Also configure the
trusted `FRONTEND_URL` used after authentication.

The LinkedIn application must have the **Sign in with LinkedIn using OpenID
Connect** product enabled. The implementation requests only `openid`, `profile`,
and `email`; email is optional and is used only when LinkedIn marks it verified.
Apply `backend/migrations/20261013_oauth_transactions.sql` and
`backend/migrations/20261016_oauth_identities.sql` before enabling the flow.

Railway runs these standalone SQL migrations in order through the backend
service's `preDeployCommand`. For a controlled manual repair, run from the
backend project directory against the intended Railway service database:

```sh
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/20261013_oauth_transactions.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/20261016_oauth_identities.sql
```
