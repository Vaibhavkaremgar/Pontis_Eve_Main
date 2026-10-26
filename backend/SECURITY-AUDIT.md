# Security Audit: Google OAuth account takeover (Issue #1)

## Vulnerability

Google sign-in must not identify a candidate by email alone. An attacker who
could present a Google identity with an email matching an existing candidate
could otherwise receive that candidate's session or link the attacker’s
provider identity to the candidate account.

## Root cause

The OAuth identity table migration allowed only `linkedin`, even though the
application’s Google identity flow had already been implemented. The security
boundary therefore depended on an incomplete database constraint. Email is
also an untrusted account-selection key for this purpose.

## Fix

- Google profile parsing requires a non-empty stable provider subject (`sub`).
- Returning Google users resolve only through
  `(provider = 'google', provider_subject = sub)`.
- A new Google subject is never merged with an existing candidate by matching
  email. A verified email is used only for the new onboarding flow and its
  resume-email binding.
- Unverified Google email claims are discarded and cannot start or link an
  account.
- Candidate creation and Google identity insertion occur in the onboarding
  transaction. The unique `(provider, provider_subject)` constraint and
  conflict-resolution path handle concurrent first sign-ins without
  reassigning an identity.
- The identity migration now permits both `google` and `linkedin`, and
  idempotently replaces the previously deployed LinkedIn-only check constraint
  without dropping identity data.

## Validation performed

Focused regression coverage exists for missing `sub`, unverified email,
email-only subject mismatch, stable-subject resolution, concurrent identity
creation, conflicting identity mapping, and the migration constraints. The
focused suite should be run with the backend dependencies installed:

```text
python -m pytest backend/tests/test_google_oauth_identity.py backend/tests/test_oauth_identities_migration.py backend/tests/test_linkedin_oidc.py -q
```

The migration SQL should also be applied against a PostgreSQL database that
contains the existing `oauth_identities` table to validate the idempotent
constraint replacement and preserve existing LinkedIn rows.
