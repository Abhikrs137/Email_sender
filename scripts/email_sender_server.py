#!/usr/bin/env python3
"""
Local browser UI server for email_sender.py.

Run this file, then open http://localhost:8000 in your browser.
"""

import json
import threading
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from datetime import datetime
from email.parser import BytesParser
from email.policy import default

from email_sender import (
    DEFAULT_DAILY_LIMIT,
    DEFAULT_DELAY,
    DEFAULT_EXCEL_PATH,
    DEFAULT_HTML_BODY_PATH,
    DEFAULT_PDF_PATH,
    DEFAULT_SUBJECT,
    get_app_password,
    get_sender_email,
    send_emails,
    send_single_email,
)

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", 8000))

APP_DIR = Path(__file__).resolve().parent.parent
RESUME_DIR = APP_DIR / "resume"
DATA_DIR = APP_DIR / "data"

EMAIL_TEMPLATES = [
    {"label": "Selenium", "path": "email_bodies/email_body_selenium.html"},
    {"label": "Both", "path": "email_bodies/email_body_template.html"},
    {"label": "Playwright", "path": "email_bodies/email_body_playwright.html"},
]

send_thread = None
stop_event = threading.Event()
state_lock = threading.Lock()

state = {
    "running": False,
    "mode": None,
    "message": "Ready.",
    "events": [],
    "last_event_id": 0,
}


def set_state(**updates):
    with state_lock:
        state.update(updates)


def get_state():
    with state_lock:
        snapshot = dict(state)
        snapshot["events"] = list(state["events"])
        return snapshot


def add_event(status, name, email, message):
    with state_lock:
        event_id = state["last_event_id"] + 1
        state["last_event_id"] = event_id

        state["message"] = message

        state["events"].append({
            "id": event_id,
            "status": status,
            "name": name,
            "email": email,
            "message": message,
        })

        state["events"] = state["events"][-50:]


def list_resumes():
    if not RESUME_DIR.exists():
        return []

    return [
        {
            "name": pdf.name,
            "path": f"resume/{pdf.name}",
        }
        for pdf in sorted(
            RESUME_DIR.glob("*.pdf"),
            key=lambda item: item.name.lower()
        )
    ]


def get_pdf_path(payload):
    selected_pdf = (payload.get("pdf") or "").strip()

    valid_paths = {
        resume["path"]
        for resume in list_resumes()
    }

    if selected_pdf in valid_paths:
        return selected_pdf

    return DEFAULT_PDF_PATH


def list_email_templates():
    return [
        template
        for template in EMAIL_TEMPLATES
        if (APP_DIR / template["path"]).exists()
    ]


def get_email_body_choice(payload):
    body_type = (payload.get("bodyType") or "template").strip()

    if body_type == "custom":
        custom_body = (payload.get("customBody") or "").strip()

        if not custom_body:
            raise ValueError("Custom email body is empty.")

        return DEFAULT_HTML_BODY_PATH, custom_body

    selected_template = (
        payload.get("htmlBody")
        or DEFAULT_HTML_BODY_PATH
    ).strip()

    valid_paths = {
        template["path"]
        for template in list_email_templates()
    }

    if selected_template in valid_paths:
        return selected_template, None

    return DEFAULT_HTML_BODY_PATH, None


def get_excel_path(payload):
    source = (
        payload.get("receiverListType")
        or "default"
    ).strip().lower()

    if source == "default":
        return DEFAULT_EXCEL_PATH

    if source == "system":
        selected_excel = (
            payload.get("excelPath")
            or ""
        ).strip()

        if not selected_excel:
            raise ValueError("Receiver list Excel file is required.")

        if not selected_excel.lower().endswith(".xlsx"):
            raise ValueError("Receiver list must be an .xlsx file.")

        excel_file = APP_DIR / selected_excel

        if not excel_file.exists():
            raise ValueError(
                f"Receiver list not found: {selected_excel}"
            )

        return selected_excel

    raise ValueError("Unknown receiver list option.")


def save_uploaded_excel(handler):
    content_type = handler.headers.get("Content-Type", "")

    if "multipart/form-data" not in content_type:
        raise ValueError("Unsupported upload format.")

    length = int(handler.headers.get("Content-Length", "0"))

    if length <= 0:
        raise ValueError("Uploaded file is empty.")

    raw_body = handler.rfile.read(length)

    parsed = BytesParser(policy=default).parsebytes(
        f"Content-Type: {content_type}\r\n"
        f"MIME-Version: 1.0\r\n\r\n".encode("utf-8")
        + raw_body
    )

    if not parsed.is_multipart():
        raise ValueError("Invalid upload data.")

    file_part = None

    for part in parsed.iter_parts():
        if part.get_content_disposition() != "form-data":
            continue

        if (
            part.get_param(
                "name",
                header="content-disposition"
            ) == "file"
        ):
            file_part = part
            break

    if file_part is None:
        raise ValueError("No Excel file selected.")

    original_name = Path(
        file_part.get_filename() or ""
    ).name

    if not original_name:
        raise ValueError("No Excel file selected.")

    if not original_name.lower().endswith(".xlsx"):
        raise ValueError("Only .xlsx files are allowed.")

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    safe_name = f"uploaded_receivers_{timestamp}.xlsx"

    target_path = DATA_DIR / safe_name

    with open(target_path, "wb") as out_file:
        out_file.write(
            file_part.get_payload(decode=True) or b""
        )

    return f"data/{safe_name}"


def run_send(payload):
    mode = payload.get("mode")

    subject = (
        payload.get("subject")
        or DEFAULT_SUBJECT
    )

    pdf_path = get_pdf_path(payload)

    html_body_path, html_body_content = (
        get_email_body_choice(payload)
    )

    excel_path = (
        get_excel_path(payload)
        if mode == "bulk"
        else DEFAULT_EXCEL_PATH
    )

    try:
        if mode == "single":
            name = (
                payload.get("name")
                or ""
            ).strip()

            email = (
                payload.get("email")
                or ""
            ).strip()

            if not name or not email:
                raise ValueError(
                    "Recipient name and email are required."
                )

            send_single_email(
                recipient_name=name,
                recipient_email=email,
                sender_email=get_sender_email(),
                app_password=get_app_password(),
                pdf_path=pdf_path,
                html_body_path=html_body_path,
                subject=subject,
                on_progress=add_event,
                html_body_content=html_body_content,
            )

            set_state(
                message=f"Single email finished for {name} <{email}>."
            )

        elif mode == "bulk":
            daily_limit = int(
                payload.get("limit")
                or DEFAULT_DAILY_LIMIT
            )

            delay = float(
                payload.get("delay")
                or DEFAULT_DELAY
            )

            send_emails(
                excel_path=excel_path,
                sender_email=get_sender_email(),
                app_password=get_app_password(),
                pdf_path=pdf_path,
                html_body_path=html_body_path,
                subject=subject,
                daily_limit=daily_limit,
                delay=delay,
                should_stop=stop_event.is_set,
                on_progress=add_event,
                html_body_content=html_body_content,
            )

            if stop_event.is_set():
                set_state(message="Bulk sending stopped.")
            else:
                set_state(message="Bulk sending finished.")

        else:
            raise ValueError("Unknown send mode.")

    except SystemExit:
        set_state(
            message="Missing sender email or app password in email_sender.py."
        )

    except Exception as exc:
        set_state(message=f"Error: {exc}")

    finally:
        set_state(running=False, mode=None)
        stop_event.clear()


class Handler(SimpleHTTPRequestHandler):

    def __init__(self, *args, **kwargs):
        super().__init__(
            *args,
            directory=str(APP_DIR),
            **kwargs
        )

    def end_headers(self):
        self.send_header(
            "Cache-Control",
            "no-store, no-cache, must-revalidate, max-age=0"
        )

        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")

        super().end_headers()

    def do_GET(self):

        if self.path in {"/", "/email_sender_ui.html"}:
            self.path = "/ui/email_sender_ui.html"

        if self.path == "/api/status":
            self.send_json(get_state())
            return

        if self.path == "/api/resumes":
            self.send_json({
                "ok": True,
                "resumes": list_resumes(),
                "default": DEFAULT_PDF_PATH
            })
            return

        if self.path == "/api/email-bodies":
            self.send_json({
                "ok": True,
                "templates": list_email_templates(),
                "default": DEFAULT_HTML_BODY_PATH
            })
            return

        super().do_GET()

    def do_POST(self):

        if self.path == "/api/upload-excel":
            self.handle_upload_excel()
            return

        if self.path == "/api/send":
            self.handle_send()
            return

        if self.path == "/api/stop":
            self.handle_stop()
            return

        self.send_json(
            {"ok": False, "message": "Not found."},
            status=404
        )

    def handle_send(self):
        global send_thread

        payload = self.read_json()

        current = get_state()

        if current["running"]:
            self.send_json({
                "ok": False,
                "message": "Email sending is already running."
            }, status=409)

            return

        stop_event.clear()

        set_state(
            running=True,
            mode=payload.get("mode"),
            message="Sending started.",
            events=[],
            last_event_id=0,
        )

        send_thread = threading.Thread(
            target=run_send,
            args=(payload,),
            daemon=True
        )

        send_thread.start()

        self.send_json({
            "ok": True,
            **get_state()
        })

    def handle_upload_excel(self):

        try:
            uploaded_path = save_uploaded_excel(self)

            self.send_json({
                "ok": True,
                "path": uploaded_path
            })

        except Exception as exc:
            self.send_json({
                "ok": False,
                "message": str(exc)
            }, status=400)

    def handle_stop(self):

        if not get_state()["running"]:
            self.send_json({
                "ok": True,
                "message": "No email sending is running.",
                **get_state()
            })

            return

        stop_event.set()

        set_state(
            message="Stop requested. Waiting for the current email step to finish."
        )

        self.send_json({
            "ok": True,
            **get_state()
        })

    def read_json(self):

        length = int(
            self.headers.get("Content-Length", 0)
        )

        if not length:
            return {}

        raw = self.rfile.read(length).decode("utf-8")

        return json.loads(raw)

    def send_json(self, data, status=200):

        body = json.dumps(data).encode("utf-8")

        self.send_response(status)

        self.send_header(
            "Content-Type",
            "application/json"
        )

        self.send_header(
            "Content-Length",
            str(len(body))
        )

        self.end_headers()

        self.wfile.write(body)


if __name__ == "__main__":

    print(f"Server running on http://{HOST}:{PORT}")

    server = ThreadingHTTPServer(
        (HOST, PORT),
        Handler
    )

    try:
        server.serve_forever()

    except KeyboardInterrupt:
        print("\nServer stopped.")
        server.server_close()