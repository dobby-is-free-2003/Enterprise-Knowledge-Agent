# Setting up the Google pieces (untested against live services)

These steps describe the code paths that exist. They were **not executed against real Google accounts** while writing this repo. Expect small fixes. Check current free-tier limits and model names in Google's docs first.

## 1. Gemini API key (free tier)
Create a key in Google AI Studio, put it in `.env` as `GEMINI_API_KEY`, set `EKA_MODE=auto`. Free-tier content may be used to improve Google products: use only synthetic or non-confidential data. Re-run `eka ingest` after switching embedders (dimensions differ; the index is rebuilt).

## 2. Google Drive as the source
1. In Google Cloud Console create a project, enable the Drive API, create an OAuth client (Desktop app), download `client_secret.json`.
2. `eka google-auth --client-secret client_secret.json` opens a browser consent and stores a token in the data dir. The login requests three scopes at once: read-only Drive, Gmail compose (drafts) and Calendar events, plus basic sign-in identity. Trim `SCOPES` in `src/eka/google_auth.py` if you only want Drive.
3. Put your documents in a folder, set `SOURCE=drive` and `DRIVE_FOLDER_ID=<id>`, run `eka ingest`.
4. Permissions come from Drive sharing (user and group emails). Groups you want to respect must be resolvable in the user directory (`users.json`).

## 3. Cloud Run
`PROJECT_ID=... ./deploy/cloudrun.sh`. Keeps the session secret in Secret Manager. For real users set `DEMO_AUTH=false`, configure `GOOGLE_OAUTH_CLIENT_ID`, and a 32+ character `SESSION_SECRET` (the app refuses to start otherwise).

## 4. Qdrant (optional)
Set `VECTOR_BACKEND=qdrant`, `QDRANT_URL`, `QDRANT_API_KEY` (a free cloud cluster or a local container works).
