from __future__ import annotations

import argparse
import html
import json
import mimetypes
import secrets
from pathlib import Path
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlparse

from dashboard_data import complete_dashboard_task, get_dashboard_data


ROOT = Path(__file__).resolve().parent
TEMPLATE_FILE = ROOT / "templates" / "dashboard.html"
STATIC_ROOT = ROOT / "static" / "dashboard"
ACTION_TOKEN = secrets.token_urlsafe(24)


def _dashboard_asset_version() -> str:
    mtimes = [
        (STATIC_ROOT / "dashboard.css").stat().st_mtime_ns,
        (STATIC_ROOT / "dashboard.js").stat().st_mtime_ns,
    ]
    return str(max(mtimes))


def _render_dashboard_html() -> str:
    return (
        TEMPLATE_FILE.read_text(encoding="utf-8")
        .replace("{{ACTION_TOKEN}}", html.escape(ACTION_TOKEN, quote=True))
        .replace("{{ASSET_VERSION}}", html.escape(_dashboard_asset_version(), quote=True))
    )


def _json_response(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    body = json.dumps(payload, indent=2, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _file_response(handler: BaseHTTPRequestHandler, path: Path, content_type: str | None = None) -> None:
    if not path.exists() or not path.is_file():
        handler.send_error(404)
        return

    body = path.read_bytes()
    handler.send_response(200)
    handler.send_header(
        "Content-Type",
        content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream",
    )
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _dashboard_response(handler: BaseHTTPRequestHandler) -> None:
    body = _render_dashboard_html().encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    try:
        content_length = int(handler.headers.get("Content-Length", "0"))
    except ValueError:
        return {}
    if content_length <= 0:
        return {}
    body = handler.rfile.read(min(content_length, 32_768))
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _host_allowed(host: str) -> bool:
    hostname = host.rsplit("@", 1)[-1].rsplit(":", 1)[0].strip("[]").lower()
    if hostname in ("localhost", "127.0.0.1", "::1"):
        return True
    parts = hostname.split(".")
    if len(parts) != 4:
        return False
    try:
        octets = [int(part) for part in parts]
    except ValueError:
        return False
    if any(octet < 0 or octet > 255 for octet in octets):
        return False
    return (
        octets[0] == 10
        or (octets[0] == 172 and 16 <= octets[1] <= 31)
        or (octets[0] == 192 and octets[1] == 168)
        or (octets[0] == 169 and octets[1] == 254)
    )


def _same_origin_allowed(handler: BaseHTTPRequestHandler) -> bool:
    host = handler.headers.get("Host", "")
    if not host or not _host_allowed(host):
        return False
    allowed_origins = {f"http://{host}", f"https://{host}"}
    for header_name in ("Origin", "Referer"):
        raw = handler.headers.get(header_name)
        if not raw:
            continue
        parsed = urlparse(raw)
        if f"{parsed.scheme}://{parsed.netloc}" not in allowed_origins:
            return False
    return True


def _authorized_action_request(handler: BaseHTTPRequestHandler) -> bool:
    content_type = handler.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    return (
        content_type == "application/json"
        and handler.headers.get("X-N4OS-Dashboard-Action-Token") == ACTION_TOKEN
        and _same_origin_allowed(handler)
    )


class DashboardRequestHandler(BaseHTTPRequestHandler):
    server_version = "N4OSDashboard/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        message = format % args
        sys.stderr.write(
            "%s - - [%s] %s\n"
            % (
                self.address_string(),
                self.log_date_time_string(),
                html.escape(message),
            ),
        )

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        if route == "/":
            self.send_response(302)
            self.send_header("Location", "/dashboard")
            self.end_headers()
            return
        if route == "/dashboard":
            _dashboard_response(self)
            return
        if route == "/healthz":
            _json_response(self, {"status": "ok"})
            return
        if route == "/api/dashboard":
            _json_response(self, get_dashboard_data())
            return
        if route == "/api/calendar/today":
            data = get_dashboard_data()
            _json_response(self, data["calendar"]["today"])
            return
        if route == "/api/tasks/recommended":
            data = get_dashboard_data()
            _json_response(self, data["tasks"]["recommended"])
            return
        if route == "/api/planning":
            data = get_dashboard_data()
            _json_response(self, data["planning"])
            return
        if route == "/api/home-board/today":
            data = get_dashboard_data()
            _json_response(self, data["home_board"]["today"])
            return
        if route == "/api/decisions/open":
            data = get_dashboard_data()
            _json_response(self, data["decisions"]["open"])
            return
        if route.startswith("/static/dashboard/"):
            relative = unquote(route.removeprefix("/static/dashboard/"))
            requested = (STATIC_ROOT / relative).resolve()
            try:
                requested.relative_to(STATIC_ROOT.resolve())
            except ValueError:
                self.send_error(403)
                return
            _file_response(self, requested)
            return
        if not route.startswith("/api/"):
            self.send_response(302)
            self.send_header("Location", "/dashboard")
            self.end_headers()
            return
        self.send_error(404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        if route == "/api/tasks/complete":
            if not _authorized_action_request(self):
                _json_response(
                    self,
                    {
                        "status": "error",
                        "message": "Dashboard action is not authorized.",
                    },
                    status=403,
                )
                return
            payload = _read_json_body(self)
            response = complete_dashboard_task(
                task_id=payload.get("task_id"),
                task_list_id=payload.get("task_list_id"),
            )
            status = 200 if response.get("status") == "ok" else 400
            _json_response(self, response, status=status)
            return
        self.send_error(404)


def create_app() -> Any:
    try:
        from fastapi import FastAPI
        from fastapi.responses import HTMLResponse, JSONResponse
        from fastapi.staticfiles import StaticFiles
    except ImportError as error:
        raise RuntimeError("FastAPI is not installed; use run_stdlib_server().") from error

    app = FastAPI(title="N4OS Portal Dashboard", docs_url=None, redoc_url=None)
    app.mount("/static/dashboard", StaticFiles(directory=STATIC_ROOT), name="dashboard-static")

    @app.get("/dashboard/", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard() -> str:
        return _render_dashboard_html()

    @app.get("/healthz", response_class=JSONResponse)
    def healthz() -> Any:
        return {"status": "ok"}

    @app.get("/api/dashboard", response_class=JSONResponse)
    def api_dashboard() -> Any:
        return get_dashboard_data()

    @app.get("/api/calendar/today", response_class=JSONResponse)
    def api_calendar_today() -> Any:
        return get_dashboard_data()["calendar"]["today"]

    @app.get("/api/tasks/recommended", response_class=JSONResponse)
    def api_tasks_recommended() -> Any:
        return get_dashboard_data()["tasks"]["recommended"]

    @app.post("/api/tasks/complete", response_class=JSONResponse)
    async def api_tasks_complete(request: Any) -> Any:
        headers = request.headers
        if (
            headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json"
            or headers.get("x-n4os-dashboard-action-token") != ACTION_TOKEN
            or not _same_origin_allowed(request)
        ):
            return JSONResponse(
                {
                    "status": "error",
                    "message": "Dashboard action is not authorized.",
                },
                status_code=403,
            )
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        response = complete_dashboard_task(
            task_id=payload.get("task_id"),
            task_list_id=payload.get("task_list_id"),
        )
        return JSONResponse(
            response,
            status_code=200 if response.get("status") == "ok" else 400,
        )

    @app.get("/api/planning", response_class=JSONResponse)
    def api_planning() -> Any:
        return get_dashboard_data()["planning"]

    @app.get("/api/home-board/today", response_class=JSONResponse)
    def api_home_board_today() -> Any:
        return get_dashboard_data()["home_board"]["today"]

    @app.get("/api/decisions/open", response_class=JSONResponse)
    def api_decisions_open() -> Any:
        return get_dashboard_data()["decisions"]["open"]

    return app


def run_stdlib_server(host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), DashboardRequestHandler)
    print(f"N4OS Portal dashboard serving http://{host}:{port}/dashboard")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard server.")
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the N4OS Portal+ dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--fastapi",
        action="store_true",
        help="Run through FastAPI/uvicorn when installed.",
    )
    args = parser.parse_args()

    if args.fastapi:
        try:
            import uvicorn
        except ImportError:
            print("uvicorn is not installed; falling back to the standard-library server.")
        else:
            uvicorn.run(create_app(), host=args.host, port=args.port)
            return

    run_stdlib_server(args.host, args.port)


if __name__ == "__main__":
    main()
