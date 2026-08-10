#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time


REPO_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_SERVER = REPO_ROOT / "dashboard_server.py"
PID_FILE = REPO_ROOT / ".dashboard_server.pid"
LOG_FILE = REPO_ROOT / "dashboard_server.log"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Start or restart the local N4OS Portal dashboard server.",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Bind host. Defaults to LAN-visible 0.0.0.0.")
    parser.add_argument("--port", type=int, default=8000, help="Bind port. Defaults to 8000.")
    parser.add_argument("--stop", action="store_true", help="Stop the dashboard server and exit.")
    parser.add_argument("--status", action="store_true", help="Print dashboard server status and exit.")
    args = parser.parse_args()

    if args.status:
        return print_status(args.port)

    stopped = stop_existing_dashboard()
    if args.stop:
        print("Dashboard server stopped." if stopped else "Dashboard server was not running.")
        return 0

    process = start_dashboard(args.host, args.port)
    PID_FILE.write_text(f"{process.pid}\n", encoding="utf-8")
    time.sleep(0.5)

    if not process_is_running(process.pid):
        print(f"Dashboard server failed to start. See {LOG_FILE.name}.", file=sys.stderr)
        return 1

    if stopped:
        print("Restarted N4OS Portal dashboard server.")
    else:
        print("Started N4OS Portal dashboard server.")
    print(f"PID: {process.pid}")
    print(f"Log: {LOG_FILE}")
    print(f"Local: http://127.0.0.1:{args.port}/dashboard")
    if args.host == "0.0.0.0":
        lan_ip = infer_lan_ip()
        if lan_ip:
            print(f"Portal: http://{lan_ip}:{args.port}/dashboard")
        else:
            print(f"Portal: http://<mac-lan-ip>:{args.port}/dashboard")
    return 0


def print_status(port: int) -> int:
    pids = find_dashboard_pids()
    if not pids:
        print("Dashboard server is not running.")
        return 1
    print(f"Dashboard server running: {', '.join(str(pid) for pid in pids)}")
    print(f"Local: http://127.0.0.1:{port}/dashboard")
    return 0


def stop_existing_dashboard() -> bool:
    pids = find_dashboard_pids()
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not any(process_is_running(pid) for pid in pids):
            break
        time.sleep(0.1)

    for pid in pids:
        if process_is_running(pid):
            os.kill(pid, signal.SIGKILL)

    if PID_FILE.exists():
        PID_FILE.unlink()
    return bool(pids)


def start_dashboard(host: str, port: int) -> subprocess.Popen[bytes]:
    python = REPO_ROOT / ".venv" / "bin" / "python"
    executable = str(python if python.exists() else sys.executable)
    command = [executable, str(DASHBOARD_SERVER), "--host", host, "--port", str(port)]
    log = LOG_FILE.open("ab")
    return subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def find_dashboard_pids() -> list[int]:
    trusted_pid_file_candidates: set[int] = set()
    inspected_candidates: set[int] = set()
    if PID_FILE.exists():
        try:
            trusted_pid_file_candidates.add(int(PID_FILE.read_text(encoding="utf-8").strip()))
        except ValueError:
            pass

    try:
        output = subprocess.check_output(["ps", "-axo", "pid=,command="], text=True)
    except (OSError, subprocess.SubprocessError):
        return sorted(pid for pid in trusted_pid_file_candidates if process_is_running(pid))

    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        pid_text, _, command = stripped.partition(" ")
        if "dashboard_server.py" not in command:
            continue
        try:
            inspected_candidates.add(int(pid_text))
        except ValueError:
            continue

    current_pid = os.getpid()
    candidates = trusted_pid_file_candidates | inspected_candidates
    return sorted(pid for pid in candidates if pid != current_pid and process_is_dashboard(pid))


def process_is_dashboard(pid: int) -> bool:
    try:
        command = subprocess.check_output(["ps", "-p", str(pid), "-o", "command="], text=True).strip()
    except (OSError, subprocess.SubprocessError):
        return False
    return "dashboard_server.py" in command


def process_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def infer_lan_ip() -> str:
    for interface in ("en0", "en1"):
        try:
            value = subprocess.check_output(["ipconfig", "getifaddr", interface], text=True).strip()
        except (FileNotFoundError, subprocess.SubprocessError):
            continue
        if value:
            return value

    try:
        hostname = socket.gethostname()
        addresses = socket.gethostbyname_ex(hostname)[2]
    except OSError:
        return ""
    for address in addresses:
        if not address.startswith("127."):
            return address
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
