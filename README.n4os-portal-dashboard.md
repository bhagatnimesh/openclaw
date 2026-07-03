# N4OS Portal+ Dashboard

This is the read-only V1 family-room dashboard for Meta Portal+ and laptop
development browsers. Google Calendar remains the source of truth for events,
Google Tasks remains the source of truth for tasks, and N4OS metadata is read
from calendar descriptions and task notes.

## Run Locally

```bash
source .venv/bin/activate
python dashboard_server.py
open http://localhost:8000/dashboard
```

For a Portal+ or another device on the local network, run:

```bash
source .venv/bin/activate
python dashboard_server.py --host 0.0.0.0
```

Then open `http://<mac-lan-ip>:8000/dashboard` from the Portal+ browser.
`/dashboard` and `/dashboard/` both work.

## Routes

- `GET /dashboard` and `GET /dashboard/` serve the Portal+ dashboard.
- `GET /api/dashboard` returns all dashboard JSON.
- `GET /api/calendar/today` returns today's calendar timeline.
- `GET /api/tasks/recommended` returns task recommendations.
- `GET /api/planning` returns upcoming planning items.
- `GET /api/home-board/today` returns today's pending Home Board notices.

## Scope

V1 is intentionally read-only. It does not create, edit, complete, delete, move,
or assign calendar events or tasks. It reuses the existing `family-calendar` and
`family-tasks` provider/tool modules, reads N4OS-native Home Board notices from
local SQLite, and only assembles dashboard-specific JSON.

Home Board notices are short-lived household instructions for the `Today at Home`
dashboard section. They are not Google Calendar events or Google Tasks.

## Test

Unit tests use fake calendar and task data, so they do not require live Google
APIs or credentials.

```bash
source .venv/bin/activate
python -m unittest test_dashboard_server.py
```
