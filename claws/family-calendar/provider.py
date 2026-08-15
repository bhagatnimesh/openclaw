from pathlib import Path
import json
import re
from urllib.parse import quote
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
        recurrence=None,
        attendees=None,
        private_extended_properties=None,
        calendar_name=None,
        notify_attendees=False,
        all_day=False,
        event_label_background_color=None,
    ):
        calendar_id = self._calendar_id_for_name(calendar_name)
        event = {
            "summary": title,
        }
        if all_day:
            event["start"] = {"date": start_time}
            event["end"] = {"date": end_time}
        else:
            event["start"] = {
                "dateTime": start_time,
                "timeZone": timezone,
            }
            event["end"] = {
                "dateTime": end_time,
                "timeZone": timezone,
            }

        if description:
            event["description"] = description

        if location:
            event["location"] = location

        if recurrence:
            event["recurrence"] = recurrence

        if attendees:
            event["attendees"] = attendees

        if private_extended_properties:
            event["extendedProperties"] = {
                "private": private_extended_properties,
            }

        insert_kwargs = {"calendarId": calendar_id, "body": event}
        event_label_id = self._event_label_id_for_background_color(
            calendar_id,
            event_label_background_color,
        )
        if event_label_id:
            event["eventLabelId"] = event_label_id
            created = self._insert_event_with_label(calendar_id, event)
            created["calendarId"] = calendar_id
            return created
        if attendees and notify_attendees:
            insert_kwargs["sendUpdates"] = "all"
        created = self.service.events().insert(**insert_kwargs).execute()
        created["calendarId"] = calendar_id
        return created

    def _insert_event_with_label(self, calendar_id, event):
        encoded_calendar_id = quote(calendar_id, safe="")
        url = (
            "https://www.googleapis.com/calendar/v3/calendars/"
            f"{encoded_calendar_id}/events?eventLabelVersion=1"
        )
        response, content = self.service._http.request(
            url,
            method="POST",
            body=json.dumps(event),
            headers={"Content-Type": "application/json"},
        )
        status = int(response.get("status", 0))
        payload = json.loads(content.decode("utf-8")) if content else {}
        if status >= 400:
            message = payload.get("error", {}).get("message") or "Google Calendar labeled event insert failed."
            raise RuntimeError(message)
        return payload

    def _event_label_id_for_background_color(self, calendar_id, background_color):
        if not background_color:
            return None

        requested = background_color.strip().lower()
        calendar = self.service.calendars().get(calendarId=calendar_id).execute()
        labels = calendar.get("labelProperties", {}).get("eventLabels", [])
        for label in labels:
            if label.get("backgroundColor", "").lower() == requested:
                return label.get("id")
        raise ValueError(f"Calendar event label color not found: {background_color}")

    def _calendar_id_for_name(self, calendar_name):
        if not calendar_name:
            return self.calendar_id
        if calendar_name == self.calendar_id:
            return self.calendar_id

        requested = _normalize_calendar_name(calendar_name)
        page_token = None
        while True:
            list_kwargs = {"maxResults": 250, "minAccessRole": "writer"}
            if page_token:
                list_kwargs["pageToken"] = page_token
            response = (
                self.service.calendarList()
                .list(**list_kwargs)
                .execute()
            )
            for calendar in response.get("items", []):
                calendar_id = calendar.get("id")
                if calendar_id == calendar_name:
                    return calendar_id

                names = [
                    calendar.get("summary"),
                    calendar.get("summaryOverride"),
                ]
                if calendar_id and any(
                    _normalize_calendar_name(name) == requested
                    for name in names
                    if name
                ):
                    return calendar_id

            page_token = response.get("nextPageToken")
            if not page_token:
                break

        raise ValueError(f"Calendar not found: {calendar_name}")

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

    def get_event(self, event_id, calendar_id=None):
        calendar_id = calendar_id or self.calendar_id
        event = (
            self.service.events()
            .get(calendarId=calendar_id, eventId=event_id)
            .execute()
        )
        event["calendarId"] = calendar_id
        return event

    def delete_event(self, event_id, calendar_id=None):
        self.service.events().delete(
            calendarId=calendar_id or self.calendar_id,
            eventId=event_id,
        ).execute()

    def update_event(
        self,
        event_id,
        title,
        start_time,
        end_time,
        timezone="America/Los_Angeles",
        description=None,
        location=None,
        attendees=None,
        private_extended_properties=None,
        calendar_id=None,
        notify_attendees=False,
    ):
        calendar_id = calendar_id or self.calendar_id
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

        if attendees:
            event["attendees"] = attendees

        if private_extended_properties:
            event["extendedProperties"] = {
                "private": private_extended_properties,
            }

        update_kwargs = {
            "calendarId": calendar_id,
            "eventId": event_id,
            "body": event,
        }
        if attendees and notify_attendees:
            update_kwargs["sendUpdates"] = "all"
        updated = self.service.events().update(**update_kwargs).execute()
        updated["calendarId"] = calendar_id
        return updated


def _normalize_calendar_name(value):
    return " ".join(re.findall(r"[a-z0-9]+", str(value).lower()))
