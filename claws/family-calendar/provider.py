from pathlib import Path
import json
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar"]

# Project root (openclaw/)
ROOT = Path(__file__).resolve().parents[2]

CLIENT_FILE = ROOT / "secrets" / "google_client_secret.json"
TOKEN_FILE = ROOT / "secrets" / "google_token.json"


class GoogleCalendarProvider:
    def __init__(self, calendar_id="primary"):
        self.calendar_id = calendar_id
        self.service = self._build_service()

    def _build_service(self):
        with open(CLIENT_FILE, "r") as f:
            client = json.load(f)["installed"]

        with open(TOKEN_FILE, "r") as f:
            token = json.load(f)

        creds = Credentials(
            token=None,
            refresh_token=token["refresh_token"],
            token_uri=client["token_uri"],
            client_id=client["client_id"],
            client_secret=client["client_secret"],
            scopes=SCOPES,
        )

        return build("calendar", "v3", credentials=creds)

    def create_event(
        self,
        title,
        start_time,
        end_time,
        timezone="America/Los_Angeles",
        description=None,
        location=None,
    ):
        event = {
            "summary": title,
            "start": {
                "dateTime": start_time,
                "timeZone": timezone,
            },
            "end": {
                "dateTime": end_time,
                "timeZone": timezone,
            },
        }

        if description:
            event["description"] = description

        if location:
            event["location"] = location

        return (
            self.service.events()
            .insert(calendarId=self.calendar_id, body=event)
            .execute()
        )

    def list_events(self, time_min, time_max, max_results=10):
        return (
            self.service.events()
            .list(
                calendarId=self.calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
            .get("items", [])
        )

    def delete_event(self, event_id):
        self.service.events().delete(
            calendarId=self.calendar_id,
            eventId=event_id,
        ).execute()
