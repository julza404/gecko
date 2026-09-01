#!/usr/bin/env python3
"""Local HTTP API and static server for Gecko."""
from __future__ import annotations

import argparse
import json
from datetime import date
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from gecko_store import GeckoStore, store


ROOT = Path(__file__).resolve().parent


class GeckoHandler(SimpleHTTPRequestHandler):
    server_version = "Gecko/2"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, max-age=0, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def log_message(self, format_string: str, *args) -> None:
        print(f"{self.log_date_time_string()} {format_string % args}")

    def reply_json(self, value, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(value, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > 1_000_000:
            raise ValueError("Request is too large")
        raw = self.rfile.read(length) if length else b"{}"
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object")
        return value

    @staticmethod
    def request_date(query: dict[str, list[str]]) -> date | None:
        raw = query.get("date", [None])[0]
        return date.fromisoformat(raw) if raw else None

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/api/health":
                return self.reply_json({"status": "ok", "service": "Gecko"})
            if parsed.path in {"/api/state", "/api/dashboard"}:
                return self.reply_json(store.dashboard(self.request_date(query)))
            if parsed.path == "/favicon.ico":
                self.send_response(HTTPStatus.NO_CONTENT)
                self.end_headers()
                return
            if parsed.path == "/":
                self.path = "/index.html"
            return super().do_GET()
        except (ValueError, KeyError) as error:
            self.reply_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except Exception as error:
            print(f"GET {parsed.path} failed: {error!r}")
            self.reply_json({"error": "Gecko could not load the dashboard"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            body = self.read_json()
            if parsed.path == "/api/tasks":
                store.add_task(
                    str(body.get("title", "")),
                    str(body.get("type", "admin")),
                    int(body.get("estimateMinutes", 30)),
                    str(body.get("priority", "normal")),
                )
            elif parsed.path == "/api/frog":
                store.set_frog(body.get("taskId"))
            elif parsed.path == "/api/frog/toggle":
                store.toggle_frog()
            elif parsed.path == "/api/email/import":
                store.import_email(
                    str(body.get("subject", "")),
                    str(body.get("type", "admin")),
                    str(body.get("sender", "")),
                    str(body.get("url", "")),
                    str(body.get("note", "")),
                    int(body.get("estimateMinutes", 30)),
                )
            elif parsed.path == "/api/calendar/import":
                events = body.get("events", [])
                if not isinstance(events, list):
                    raise ValueError("Events must be a list")
                target = date.fromisoformat(body["date"]) if body.get("date") else None
                store.import_calendar(events, target)
            elif parsed.path == "/api/afk/intervals":
                intervals = body.get("intervals", [])
                if not isinstance(intervals, list):
                    raise ValueError("Intervals must be a list")
                target = date.fromisoformat(body["date"]) if body.get("date") else None
                store.record_afk_intervals(intervals, target)
            elif parsed.path == "/api/day/start":
                store.read(date.fromisoformat(body["date"]) if body.get("date") else None)
            elif parsed.path == "/api/weekly-reset":
                report = store.weekly_reset()
                return self.reply_json({"report": report, "dashboard": store.dashboard()})
            else:
                return self.reply_json({"error": "Unknown action"}, HTTPStatus.NOT_FOUND)
            return self.reply_json(store.dashboard(), HTTPStatus.CREATED)
        except KeyError as error:
            self.reply_json({"error": str(error).strip("'")}, HTTPStatus.NOT_FOUND)
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            self.reply_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except Exception as error:
            print(f"POST {parsed.path} failed: {error!r}")
            self.reply_json({"error": "Gecko could not save that change"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_PATCH(self) -> None:
        parsed = urlparse(self.path)
        try:
            if not parsed.path.startswith("/api/tasks/"):
                return self.reply_json({"error": "Unknown action"}, HTTPStatus.NOT_FOUND)
            task_id = parsed.path.rsplit("/", 1)[-1]
            store.update_task(task_id, self.read_json())
            self.reply_json(store.dashboard())
        except KeyError:
            self.reply_json({"error": "Task not found"}, HTTPStatus.NOT_FOUND)
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            self.reply_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except Exception as error:
            print(f"PATCH {parsed.path} failed: {error!r}")
            self.reply_json({"error": "Gecko could not save that change"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        try:
            if not parsed.path.startswith("/api/tasks/"):
                return self.reply_json({"error": "Unknown action"}, HTTPStatus.NOT_FOUND)
            store.delete_task(parsed.path.rsplit("/", 1)[-1])
            self.reply_json(store.dashboard())
        except KeyError:
            self.reply_json({"error": "Task not found"}, HTTPStatus.NOT_FOUND)
        except Exception as error:
            print(f"DELETE {parsed.path} failed: {error!r}")
            self.reply_json({"error": "Gecko could not delete that task"}, HTTPStatus.INTERNAL_SERVER_ERROR)


def run(host: str = "127.0.0.1", port: int = 4173) -> None:
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("Gecko only serves localhost to protect local work records")
    store.ensure_files()
    server = ThreadingHTTPServer((host, port), GeckoHandler)
    print(f"Gecko running at http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Gecko local service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4173)
    arguments = parser.parse_args()
    run(arguments.host, arguments.port)
