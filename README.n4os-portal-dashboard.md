# N4OS Portal+ Dashboard

This is the V1 family-room dashboard for Meta Portal+ and laptop development
browsers. Google Calendar remains the source of truth for events, Google Tasks
remains the source of truth for tasks, and N4OS metadata is read from calendar
descriptions and task notes.

## Run Locally

```bash
./dashboard_restart
```

This restarts the server on `0.0.0.0:8000`, prints the laptop and Portal URLs,
and writes server output to `dashboard_server.log`.

Then open the printed `http://<mac-lan-ip>:8000/dashboard` URL from the Portal+
browser.
`/dashboard` and `/dashboard/` both work.

Useful commands:

```bash
./dashboard_restart --status
./dashboard_restart --stop
./dashboard_restart --host 127.0.0.1
```

## Screen Wake

The dashboard asks the browser to keep the screen awake while the page is
visible during display hours. Default display hours are `06:00-22:00` in the
browser's local time.

Use a bookmark query parameter to change the behavior:

- `http://<mac-lan-ip>:8000/dashboard?wake=always`
- `http://<mac-lan-ip>:8000/dashboard?wake=off`
- `http://<mac-lan-ip>:8000/dashboard?wake=07:30-21:00`

Browser wake lock support requires `localhost` or HTTPS. When a Portal+ opens
the page over plain LAN HTTP, the dashboard falls back to a tiny muted video
keepalive. If the Portal blocks autoplay, tap `Keep screen on` once after the
page loads. If the fallback is unavailable, use the Portal+ display settings or
serve the dashboard through trusted HTTPS.

## Routes

- `GET /dashboard` and `GET /dashboard/` serve the Portal+ dashboard.
- `GET /api/dashboard` returns all dashboard JSON.
- `GET /api/calendar/today` returns today's calendar timeline.
- `GET /api/tasks/recommended` returns task recommendations.
- `POST /api/tasks/complete` completes a Google Task by `task_id`.
- `GET /api/backlog` returns lane counts, attention items, review state, and all open backlog lanes.
- `POST /api/backlog/items` creates a Discussion, Planning, or Decision item.
- `POST /api/backlog/actions` applies a validated backlog action.
- `GET /api/planning` returns tracked Planning items plus untracked calendar suggestions.
- `GET /api/home-board/today` returns today's pending Home Board notices.
- `GET /api/decisions/open` returns pending family decisions.
- `GET /api/shopping` returns fixed shopping lists and pending items.
- `POST /api/shopping/items/check` checks off one shopping item by `item_id`.
- `POST /api/shopping/lists/clear` checks off all pending items in one list.

## Shopping Lists

Shopping uses Google Tasks as the live shared list source when the existing
Google credential files are present in `secrets/google_client_secret.json` and
`secrets/google_token.json`. Otherwise it falls back to local SQLite only.

The V1 Google Tasks mapping is:

- `Indian` -> `Grocery - Indian`
- `Costco` -> `Grocery - Costco`
- `Whole Foods` -> `Grocery - Wholefoods`
- `Amazon` -> `Shopping - Amazon`
- `Others` -> `Shopping`

Dashboard and Telegram writes update Google Tasks first and then append SQLite
history in `data/n4os.db`. Items added or completed directly in Google Tasks
are pulled into SQLite on the next dashboard refresh or shopping list read.
Unset `N4OS_OURGROCERIES_MCP_COMMAND` if it was set during the earlier
OurGroceries test.

## Family Backlog

The Family Backlog at `/dashboard/#backlog` has Discussion, Planning, and
Decisions lanes. Dashboard `Add` captures a typed item with an owner and
relevant date. Telegram accepts explicit forms such as `Discussion: Should we
attend the birthday?`, `Planning: Camping trip September 12`, and `Decision:
Choose Nysha's school next year`. `/backlog review` starts the same priority
review available from the dashboard.

Backlog records, activity, positions, options, evidence, and external links
are canonical SQLite state in `data/n4os.db`. Planning keeps Google Calendar
dates and Google Task execution authoritative by storing explicit source IDs.
Calendar planning suggestions remain read-only until a parent tracks them.
Moves and closes are never automatic; N4OS only shows suggestions or
`Ready to close` state.

## Scope

V1 is read-only outside task, backlog, reading, and shopping action controls. The
Tasks screen can complete existing Google Tasks through the existing
`family-tasks` tool layer. The Shopping screen can check off items and clear a
store list through Google Tasks when configured. It does not create, edit,
delete, move, or assign calendar events or tasks. It reuses the existing
`family-calendar` and `family-tasks` provider/tool modules, reads N4OS-native
Home Board notices from local SQLite, and only assembles dashboard-specific
JSON.

All dashboard mutations must come from the served dashboard page with the page
action token and a same-origin/private-host request. Restart the dashboard server
after code changes so the token and API handlers are refreshed together.

Home Board notices are short-lived household instructions for the `Today at Home`
dashboard section. They are not Google Calendar events or Google Tasks.

## Test

Unit tests use fake calendar and task data, so they do not require live Google
APIs or credentials.

```bash
source .venv/bin/activate
python -m unittest test_dashboard_server.py
```
