import json
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
]

ROOT = Path(__file__).resolve().parent
TOKEN_FILE = ROOT / "secrets" / "google_token.json"

flow = InstalledAppFlow.from_client_secrets_file(
    ROOT / "secrets" / "google_client_secret.json",
    SCOPES
)

creds = flow.run_local_server(
    port=0,
    access_type="offline",
    prompt="consent"
)

TOKEN_FILE.write_text(
    json.dumps(
        {
            "refresh_token": creds.refresh_token,
            "scopes": SCOPES,
        },
        indent=2,
    )
    + "\n",
)

print(f"\nUpdated {TOKEN_FILE.relative_to(ROOT)} with the new refresh token.")
