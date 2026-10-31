# Development and deployment

## Local development

Requirements: Python 3.10+, Node.js supported by the installed Next.js release, and npm.

```powershell
npm.cmd install
python -m pip install -r requirements.txt
python scripts/preflight.py
npm.cmd run dev:all
```

Open the Next.js UI at `http://localhost:3000`; FastAPI docs are at `http://localhost:8000/docs`. `npm run dev` starts only the frontend; `npm run dev:api` starts only FastAPI. The Next.js rewrite target defaults to `http://127.0.0.1:8000` and can be set using `BANKEASE_API_ORIGIN`. `PORT` changes the API port; frontend dev uses port 3000. Shell `.env` files are not auto-loaded.

The Python environment supports `APP_ENV`, `BANKEASE_STORAGE_BACKEND`, `MONGODB_URI`, `MONGODB_DATABASE`, `MONGODB_CONNECT_TIMEOUT_MS`, `MAX_UPLOAD_MB`, `BANKEASE_RETENTION_HOURS`, `BANKEASE_RETENTION_SWEEP_SECONDS`, `BANKEASE_ASYNC_DOCUMENTS`, `BANKEASE_ENABLE_DOCUMENT_WORKER`, `BANKEASE_JOB_MAX_ATTEMPTS`, `BANKEASE_JOB_LEASE_SECONDS`, `BANKEASE_DATA`, `CORS_ORIGINS`, and `DEV_EMAIL_LINKS`. Retention sweeps run in the document-worker loop, are bounded to 100 documents per sweep, and are also run for the uploading owner before a new upload is stored. Set `BANKEASE_RETENTION_HOURS=0` to disable expiration. Local verification/reset links are development conveniences; no email provider is configured. The current Mongo claim path supports multiple API workers; local JSON mode is intended for a single API process only. Live Mongo retention and concurrency have not been verified.

MongoDB development mode pings the configured server and creates indexes at startup. A live MongoDB instance has not been verified as part of this work.

## Deployment readiness

`APP_ENV=production` deliberately fails closed. Authentication and account-scoped document routes are implemented, but there is no production email provider, encryption or verified host ACL controls for private local files, distributed rate limiter, verified deployment topology, TLS/secret setup, backup/restore procedure, operations monitoring, or live Mongo verification. Local records and source files are not encrypted at rest. Do not expose this prototype to the public or upload real customer agreements.

Before a deployment, complete those controls, run npm install from a lockfile, run frontend lint/type/build checks, execute backend authorization tests, and verify the app against a live database and browser on desktop/mobile. Document actual hosting costs and quotas. No production deployment is tested or approved.
