# Security Policy

## Scope

Civitas processes only public federal data (Congress.gov, FEC, GovInfo, etc.) and stores no user PII. There are no user accounts, sessions, or tracking. The attack surface is limited to:

- The FastAPI backend API (publicly accessible endpoints)
- The admin panel (`/admin`) protected by `ADMIN_TOKEN`
- The pipeline trigger endpoint protected by `PIPELINE_TRIGGER_TOKEN`
- SQLite database (local to the server, not network-exposed)

## Reporting a vulnerability

If you find a security issue, please do **not** open a public GitHub issue.

Email: **mack.ryanm@gmail.com** with subject line `[CIVITAS SECURITY]`.

Include:
- Description of the vulnerability and affected component
- Steps to reproduce
- Potential impact

You'll receive a response within 5 business days. We ask for 90 days to address the issue before public disclosure.

## Out of scope

- Issues requiring physical access to the server
- Theoretical vulnerabilities without a working proof of concept
- Rate limiting on public read endpoints (by design — all data is already public)
- Anything in the `/admin` path that requires a valid `ADMIN_TOKEN`
