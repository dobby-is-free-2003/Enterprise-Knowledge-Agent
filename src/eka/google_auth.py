"""OAuth helpers for Drive, Gmail drafts and Calendar (used only in drive/google modes).

Untested against live Google in this repo's offline run. See docs/SETUP_GOOGLE.md.

Scopes are deliberately narrow: read-only Drive, Gmail *compose* (drafts), Calendar events.
Test-mode OAuth apps with sensitive scopes issue short-lived refresh tokens (verify current rules in
the Google Cloud console), so expect to re-run `eka google-auth` periodically while in testing.
"""
from __future__ import annotations

from pathlib import Path

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar.events",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]


def run_local_flow(client_secret_path: Path, token_path: Path) -> None:
    """One-time interactive login on a machine with a browser. Writes an authorized-user token file."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), SCOPES)
    creds = flow.run_local_server(port=0)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json())


def load_credentials(token_path: Path):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json())
    return creds


def verify_google_id_token(token: str, client_id: str) -> str | None:
    """Return the verified email from a Google Sign-In ID token, or None."""
    try:
        from google.auth.transport import requests as g_requests
        from google.oauth2 import id_token

        info = id_token.verify_oauth2_token(token, g_requests.Request(), client_id)
        return info["email"].lower() if info.get("email_verified") else None
    except Exception:  # noqa: BLE001 - any failure means "not verified"
        return None
