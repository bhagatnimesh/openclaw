import json
from datetime import datetime, timedelta
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar"]

CLIENT_FILE = "secrets/google_client_secret.json"
TOKEN_FILE = "secrets/google_token.json"

with open(CLIENT_FILE) as f:
    client = json.load(f)["installed"]

with open(TOKEN_FILE) as f:
    token = json.load(f)

creds = Credentials(
    token=None,
    refresh_token=token["refresh_token"],
    token_uri=client["token_uri"],
    client_id=client["client_id"],
    client_secret=client["client_secret"],
    scopes=SCOPES,
)

service = build("calendar", "v3", credentials=creds)

calendar_id = "primary"  # replace later with your family calendar ID

start = datetime.now() + timedelta(hours=1)
end = start + timedelta(minutes=30)

event = {
    "summary": "N4OS Test Event",
    "start": {"dateTime": start.isoformat(), "timeZone": "America/Los_Angeles"},
    "end": {"dateTime": end.isoformat(), "timeZone": "America/Los_Angeles"},
}

created = service.events().insert(calendarId=calendar_id, body=event).execute()
print("Created:", created["id"])

events = service.events().list(
    calendarId=calendar_id,
    maxResults=5,
    singleEvents=True,
    orderBy="startTime",
).execute()

print("\nUpcoming events:")
for e in events.get("items", []):
    print("-", e.get("summary"), e.get("start"))

service.events().delete(calendarId=calendar_id, eventId=created["id"]).execute()
print("\nDeleted test event.")

