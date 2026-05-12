#!/usr/bin/env python3
"""
Local browser UI server for email_sender.py.

Run this file, then open http://localhost:8000 in your browser.
"""

import json
import threading
import os
import re
import time
import secrets
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
    send_emails,
    send_single_email,
)

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", 8000))
LOGIN_USERNAME = os.environ.get("APP_USERNAME", "admin").strip()
LOGIN_PASSWORD = os.environ.get("APP_PASSWORD", "admin").strip()

APP_DIR = Path(__file__).resolve().parent.parent
RESUME_DIR = APP_DIR / "resume"
DATA_DIR = APP_DIR / "data"
EMAIL_BODY_DIR = APP_DIR / "email_bodies"
CREDENTIALS_FILE = APP_DIR / "config" / "owner_credentials.json"

EMAIL_TEMPLATES = [
    {"label": "Selenium", "path": "email_bodies/email_body_selenium.html"},
    {"label": "Both", "path": "email_bodies/email_body_template.html"},
    {"label": "Playwright", "path": "email_bodies/email_body_playwright.html"},
]

ENV_OWNER_ID = "__env__"

send_thread = None
stop_event = threading.Event()
state_lock = threading.Lock()
session_lock = threading.Lock()
sessions = set()

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


def create_session():
    token = secrets.token_urlsafe(32)
    with session_lock:
        sessions.add(token)
    return token


def delete_session(token):
    if not token:
        return
    with session_lock:
        sessions.discard(token)


def is_valid_session(token):
    with session_lock:
        return token in sessions


def load_credentials_store():
    if not CREDENTIALS_FILE.exists():
        return {"credentials": []}

    try:
        data = json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = {}

    credentials = data.get("credentials")
    if not isinstance(credentials, list):
        credentials = []

    return {"credentials": credentials}


def save_credentials_store(data):
    CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_FILE.write_text(
        json.dumps(data, indent=2),
        encoding="utf-8"
    )


def public_credential(credential):
    return {
        "id": credential["id"],
        "ownerName": credential["ownerName"],
        "senderEmail": credential["senderEmail"],
        "hasAppPassword": bool(credential.get("appPassword")),
        "editable": credential.get("editable", True),
    }


def env_credential():
    sender_email = os.environ.get("SENDER_EMAIL", "").strip()
    app_password = normalize_app_password(
        os.environ.get("GMAIL_APP_PASSWORD", "")
    )

    if not sender_email or not app_password:
        return None

    return {
        "id": ENV_OWNER_ID,
        "ownerName": "Environment Owner",
        "senderEmail": sender_email,
        "appPassword": app_password,
        "editable": False,
    }


def normalize_app_password(app_password):
    return re.sub(r"\s+", "", app_password or "")


def list_credentials(include_private=False):
    credentials = []
    env_owner = env_credential()

    if env_owner:
        credentials.append(env_owner)

    credentials.extend(load_credentials_store()["credentials"])

    if include_private:
        return credentials

    return [public_credential(item) for item in credentials]


def find_stored_credential(credential_id):
    store = load_credentials_store()

    for credential in store["credentials"]:
        if credential.get("id") == credential_id:
            return store, credential

    return store, None


def save_owner_credential(payload):
    credential_id = (payload.get("id") or "").strip()
    owner_name = (payload.get("ownerName") or "").strip()
    sender_email = (payload.get("senderEmail") or "").strip()
    app_password = normalize_app_password(
        payload.get("appPassword") or ""
    )

    if credential_id == ENV_OWNER_ID:
        raise ValueError("Environment credentials cannot be edited here.")

    if not owner_name:
        raise ValueError("Owner name is required.")

    if not sender_email:
        raise ValueError("Sender email is required.")

    store, existing = find_stored_credential(credential_id)

    if existing is None:
        if not app_password:
            raise ValueError("Google app password is required.")

        credential = {
            "id": f"owner_{int(time.time() * 1000)}",
            "ownerName": owner_name,
            "senderEmail": sender_email,
            "appPassword": app_password,
        }
        store["credentials"].append(credential)
    else:
        existing["ownerName"] = owner_name
        existing["senderEmail"] = sender_email

        if app_password:
            existing["appPassword"] = app_password

        credential = existing

    save_credentials_store(store)

    return public_credential(credential)


def delete_owner_credential(credential_id):
    credential_id = (credential_id or "").strip()

    if credential_id == ENV_OWNER_ID:
        raise ValueError("Environment credentials cannot be deleted here.")

    store = load_credentials_store()
    before_count = len(store["credentials"])
    store["credentials"] = [
        credential
        for credential in store["credentials"]
        if credential.get("id") != credential_id
    ]

    if len(store["credentials"]) == before_count:
        raise ValueError("Credential owner not found.")

    save_credentials_store(store)

    return credential_id


def get_owner_credentials(owner_id):
    owner_id = (owner_id or "").strip()
    credentials = list_credentials(include_private=True)

    if owner_id:
        for credential in credentials:
            if credential.get("id") == owner_id:
                return credential["senderEmail"], credential["appPassword"]

        raise ValueError("Selected owner credential was not found.")

    if credentials:
        credential = credentials[0]
        return credential["senderEmail"], credential["appPassword"]

    raise ValueError("No sender owner credential found. Add one in Manage Credentials.")


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
    templates = []
    seen_paths = set()

    for template in EMAIL_TEMPLATES:
        template_path = template["path"]
        if (APP_DIR / template_path).exists():
            templates.append(template)
            seen_paths.add(template_path)

    if EMAIL_BODY_DIR.exists():
        for html_file in sorted(
            EMAIL_BODY_DIR.glob("*.html"),
            key=lambda item: item.name.lower()
        ):
            relative_path = f"email_bodies/{html_file.name}"
            if relative_path in seen_paths:
                continue

            templates.append({
                "label": make_template_label(html_file.stem),
                "path": relative_path,
            })

    return templates


def make_template_label(stem):
    label = stem
    if label.startswith("email_body_"):
        label = label[len("email_body_"):]
    return " ".join(part.capitalize() for part in re.split(r"[_-]+", label) if part)


def safe_email_body_path(path_value):
    selected_path = (path_value or "").strip().replace("\\", "/")

    if not selected_path.startswith("email_bodies/"):
        raise ValueError("Email body must be inside the email_bodies folder.")

    target = (APP_DIR / selected_path).resolve()

    if not str(target).startswith(str(EMAIL_BODY_DIR.resolve())):
        raise ValueError("Invalid email body path.")

    if target.suffix.lower() != ".html":
        raise ValueError("Email body must be an .html file.")

    return target


def slugify_name(name):
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", (name or "").strip().lower())
    slug = slug.strip("_")
    if not slug:
        raise ValueError("Email body name is required.")
    return slug[:60]


def read_email_body(path_value):
    target = safe_email_body_path(path_value)

    if not target.exists():
        raise ValueError("Email body not found.")

    return target.read_text(encoding="utf-8")


def unique_email_body_path(name, existing_path=None):
    EMAIL_BODY_DIR.mkdir(parents=True, exist_ok=True)
    base_name = f"email_body_{slugify_name(name)}.html"
    target = EMAIL_BODY_DIR / base_name
    existing_target = (
        safe_email_body_path(existing_path)
        if existing_path
        else None
    )

    if existing_target and target.resolve() == existing_target.resolve():
        return target

    counter = 2

    while target.exists():
        if existing_target and target.resolve() == existing_target.resolve():
            return target
        target = EMAIL_BODY_DIR / f"email_body_{slugify_name(name)}_{counter}.html"
        counter += 1

    return target


def save_email_body(payload):
    name = (payload.get("name") or "").strip()
    content = (payload.get("content") or "").strip()

    if not content:
        raise ValueError("Email body content is required.")

    old_target = (
        safe_email_body_path(payload.get("path"))
        if payload.get("path")
        else None
    )
    target = unique_email_body_path(name, payload.get("path"))
    target.write_text(content, encoding="utf-8")

    if old_target and old_target.resolve() != target.resolve() and old_target.exists():
        old_target.unlink()

    relative_path = f"email_bodies/{target.name}"

    return {
        "label": make_template_label(target.stem),
        "path": relative_path,
        "content": content,
    }


def delete_email_body(path_value):
    target = safe_email_body_path(path_value)

    if not target.exists():
        raise ValueError("Email body not found.")

    target.unlink()

    return f"email_bodies/{target.name}"


def parse_multipart_file(handler, allowed_suffixes):
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
        raise ValueError("No file selected.")

    original_name = Path(
        file_part.get_filename() or ""
    ).name

    if not original_name:
        raise ValueError("No file selected.")

    suffix = Path(original_name).suffix.lower()

    if suffix not in allowed_suffixes:
        raise ValueError(
            f"Only {', '.join(sorted(allowed_suffixes))} files are allowed."
        )

    return original_name, file_part.get_payload(decode=True) or b""


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
    original_name, file_bytes = parse_multipart_file(handler, {".xlsx"})

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    safe_name = f"uploaded_receivers_{timestamp}.xlsx"

    target_path = DATA_DIR / safe_name

    with open(target_path, "wb") as out_file:
        out_file.write(file_bytes)

    return f"data/{safe_name}"


def save_uploaded_email_body(handler):
    original_name, file_bytes = parse_multipart_file(handler, {".html", ".htm"})

    EMAIL_BODY_DIR.mkdir(parents=True, exist_ok=True)
    stem = Path(original_name).stem
    target = unique_email_body_path(stem)
    content = file_bytes.decode("utf-8")
    target.write_text(content, encoding="utf-8")

    return {
        "label": make_template_label(target.stem),
        "path": f"email_bodies/{target.name}",
        "content": content,
    }


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

    sender_email, app_password = get_owner_credentials(
        payload.get("ownerId")
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
                sender_email=sender_email,
                app_password=app_password,
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
                sender_email=sender_email,
                app_password=app_password,
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
        path_only = self.path.split("?", 1)[0]

        if path_only == "/login.html":
            self.path = "/ui/login.html"
            super().do_GET()
            return

        if path_only == "/api/session":
            self.send_json({
                "ok": True,
                "authenticated": self.is_authenticated(),
            })
            return

        if not self.is_authenticated():
            if path_only.startswith("/api/"):
                self.send_json({
                    "ok": False,
                    "message": "Login required."
                }, status=401)
            else:
                self.redirect("/login.html")
            return

        if path_only in {"/", "/email_sender_ui.html"}:
            self.path = "/ui/email_sender_ui.html"

        if path_only == "/email_bodies_ui.html":
            self.path = "/ui/email_bodies_ui.html"

        if path_only == "/credentials_ui.html":
            self.path = "/ui/credentials_ui.html"

        if path_only == "/api/status":
            self.send_json(get_state())
            return

        if path_only == "/api/resumes":
            self.send_json({
                "ok": True,
                "resumes": list_resumes(),
                "default": DEFAULT_PDF_PATH
            })
            return

        if path_only == "/api/sender-preview-config":
            self.send_json({
                "ok": True,
                "senderEmail": os.environ.get("SENDER_EMAIL", "").strip(),
                "defaultSubject": DEFAULT_SUBJECT,
                "defaultPdf": DEFAULT_PDF_PATH,
            })
            return

        if path_only == "/api/credentials":
            selected_id = self.query_value("id")

            if selected_id:
                credential = next(
                    (
                        item
                        for item in list_credentials()
                        if item["id"] == selected_id
                    ),
                    None
                )

                if credential is None:
                    self.send_json({
                        "ok": False,
                        "message": "Credential owner not found."
                    }, status=404)
                    return

                self.send_json({
                    "ok": True,
                    "credential": credential,
                })
                return

            self.send_json({
                "ok": True,
                "credentials": list_credentials(),
                "default": (
                    list_credentials()[0]["id"]
                    if list_credentials()
                    else ""
                )
            })
            return

        if path_only == "/api/email-bodies":
            selected_path = self.query_value("path")

            if selected_path:
                try:
                    self.send_json({
                        "ok": True,
                        "body": {
                            "path": selected_path,
                            "content": read_email_body(selected_path),
                        }
                    })
                except Exception as exc:
                    self.send_json({
                        "ok": False,
                        "message": str(exc)
                    }, status=404)
                return

            self.send_json({
                "ok": True,
                "templates": list_email_templates(),
                "default": DEFAULT_HTML_BODY_PATH
            })
            return

        super().do_GET()

    def do_POST(self):
        if self.path == "/api/login":
            self.handle_login()
            return

        if not self.is_authenticated():
            self.send_json({
                "ok": False,
                "message": "Login required."
            }, status=401)
            return

        if self.path == "/api/logout":
            self.handle_logout()
            return

        if self.path == "/api/upload-excel":
            self.handle_upload_excel()
            return

        if self.path == "/api/upload-email-body":
            self.handle_upload_email_body()
            return

        if self.path == "/api/email-bodies":
            self.handle_save_email_body()
            return

        if self.path == "/api/credentials":
            self.handle_save_credential()
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

    def do_DELETE(self):
        path_only = self.path.split("?", 1)[0]

        if not self.is_authenticated():
            self.send_json({
                "ok": False,
                "message": "Login required."
            }, status=401)
            return

        if path_only == "/api/email-bodies":
            self.handle_delete_email_body()
            return

        if path_only == "/api/credentials":
            self.handle_delete_credential()
            return

        self.send_json(
            {"ok": False, "message": "Not found."},
            status=404
        )

    def handle_login(self):
        payload = self.read_json()
        username = (payload.get("username") or "").strip()
        password = (payload.get("password") or "").strip()

        if not username or not password:
            self.send_json({
                "ok": False,
                "message": "Username and password are required."
            }, status=400)
            return

        if username != LOGIN_USERNAME or password != LOGIN_PASSWORD:
            self.send_json({
                "ok": False,
                "message": "Invalid username or password."
            }, status=401)
            return

        token = create_session()

        self.send_json({
            "ok": True,
            "message": "Logged in successfully."
        }, cookies=[
            f"session={token}; Path=/; HttpOnly; SameSite=Lax"
        ])

    def handle_logout(self):
        delete_session(self.cookie_value("session"))

        self.send_json({
            "ok": True,
            "message": "Logged out."
        }, cookies=[
            "session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
        ])

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

    def handle_upload_email_body(self):

        try:
            saved_body = save_uploaded_email_body(self)

            self.send_json({
                "ok": True,
                "body": saved_body,
                "templates": list_email_templates(),
            })

        except Exception as exc:
            self.send_json({
                "ok": False,
                "message": str(exc)
            }, status=400)

    def handle_save_email_body(self):

        try:
            saved_body = save_email_body(self.read_json())

            self.send_json({
                "ok": True,
                "body": saved_body,
                "templates": list_email_templates(),
            })

        except Exception as exc:
            self.send_json({
                "ok": False,
                "message": str(exc)
            }, status=400)

    def handle_save_credential(self):

        try:
            credential = save_owner_credential(self.read_json())

            self.send_json({
                "ok": True,
                "credential": credential,
                "credentials": list_credentials(),
            })

        except Exception as exc:
            self.send_json({
                "ok": False,
                "message": str(exc)
            }, status=400)

    def handle_delete_email_body(self):

        try:
            deleted_path = delete_email_body(self.query_value("path"))

            self.send_json({
                "ok": True,
                "path": deleted_path,
                "templates": list_email_templates(),
            })

        except Exception as exc:
            self.send_json({
                "ok": False,
                "message": str(exc)
            }, status=400)

    def handle_delete_credential(self):

        try:
            deleted_id = delete_owner_credential(
                self.query_value("id")
            )

            self.send_json({
                "ok": True,
                "id": deleted_id,
                "credentials": list_credentials(),
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

    def query_value(self, name):
        if "?" not in self.path:
            return ""

        query = self.path.split("?", 1)[1]

        for pair in query.split("&"):
            key, _, value = pair.partition("=")
            if key == name:
                from urllib.parse import unquote_plus
                return unquote_plus(value)

        return ""

    def cookie_value(self, name):
        cookie_header = self.headers.get("Cookie", "")

        for cookie in cookie_header.split(";"):
            key, _, value = cookie.strip().partition("=")
            if key == name:
                return value

        return ""

    def is_authenticated(self):
        return is_valid_session(self.cookie_value("session"))

    def redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def send_json(self, data, status=200, cookies=None):

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

        for cookie in cookies or []:
            self.send_header("Set-Cookie", cookie)

        self.end_headers()

        self.wfile.write(body)


if __name__ == "__main__":

    print(f"Server started successfully.")
    print(f"Access the UI at: http://localhost:{PORT}")

    server = ThreadingHTTPServer(
        (HOST, PORT),
        Handler
    )

    try:
        server.serve_forever()

    except KeyboardInterrupt:
        print("\nServer stopped.")
        server.server_close()
