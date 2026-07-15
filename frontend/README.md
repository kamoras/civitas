# Civitas Frontend

Next.js 16 (App Router), React 19, TypeScript, Tailwind CSS.

See the [repository root README](../README.md) for architecture, setup, and
deployment — this app doesn't deploy to Vercel; it runs in Docker behind
nginx with a blue/green rollout (`../deploy.sh`), alongside the FastAPI
backend it talks to.

## Local development

```bash
npm install
npm run dev
```

Requires the backend running locally (see the root README's Quick Start) —
set `NEXT_PUBLIC_API_URL` / `BACKEND_URL` to point at it if not using the
default Docker Compose setup.
