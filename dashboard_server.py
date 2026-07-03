from __future__ import annotations

import argparse
import html
import json
import mimetypes
from pathlib import Path
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlparse

from dashboard_data import get_dashboard_data


ROOT = Path(__file__).resolve().parent
TEMPLATE_FILE = ROOT / "templates" / "dashboard.html"
STATIC_ROOT = ROOT / "static" / "dashboard"


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
            _file_response(self, TEMPLATE_FILE, "text/html; charset=utf-8")
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
        return TEMPLATE_FILE.read_text(encoding="utf-8")

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

    @app.get("/api/planning", response_class=JSONResponse)
    def api_planning() -> Any:
        return get_dashboard_data()["planning"]

    @app.get("/api/home-board/today", response_class=JSONResponse)
    def api_home_board_today() -> Any:
        return get_dashboard_data()["home_board"]["today"]

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
