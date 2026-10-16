# Here are your Instructions
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
