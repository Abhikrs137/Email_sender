import json
import os
import sys
from pathlib import Path

from flask import Flask, jsonify, make_response, redirect, request, send_file

APP_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = APP_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import email_sender_server as local_server


app = Flask(__name__)


def json_response(data, status=200, cookies=None):
    response = make_response(jsonify(data), status)
    for cookie in cookies or []:
        response.headers.add("Set-Cookie", cookie)
    return response


def is_authenticated():
    return local_server.is_valid_session(request.cookies.get("session", ""))


def require_auth():
    if is_authenticated():
        return None

    if request.path.startswith("/api/"):
        return json_response(
            {"ok": False, "message": "Login required."},
            status=401,
        )

    return redirect("/login.html")


def page_response(relative_path):
    response = make_response(send_file(APP_DIR / relative_path))
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def upload_file_to(folder, allowed_suffixes, generated_name):
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        raise ValueError("No file selected.")

    original_name = Path(uploaded.filename).name
    suffix = Path(original_name).suffix.lower()
    if suffix not in allowed_suffixes:
        raise ValueError(f"Only {', '.join(sorted(allowed_suffixes))} files are allowed.")

    target_dir = Path(os.getenv("TMPDIR", "/tmp")) / folder
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / generated_name(original_name)
    uploaded.save(target)
    return target


@app.get("/")
def index():
    auth = require_auth()
    if auth:
        return auth
    return page_response("ui/email_sender_ui.html")


@app.get("/login.html")
def login_page():
    return page_response("ui/login.html")


@app.get("/email_sender_ui.html")
def sender_page():
    auth = require_auth()
    if auth:
        return auth
    return page_response("ui/email_sender_ui.html")


@app.get("/credentials_ui.html")
def credentials_page():
    auth = require_auth()
    if auth:
        return auth
    return page_response("ui/credentials_ui.html")


@app.get("/email_bodies_ui.html")
def bodies_page():
    auth = require_auth()
    if auth:
        return auth
    return page_response("ui/email_bodies_ui.html")


@app.get("/api/session")
def session_status():
    return json_response({"ok": True, "authenticated": is_authenticated()})


@app.post("/api/login")
def login():
    payload = request.get_json(silent=True) or {}
    username = (payload.get("username") or "").strip()
    password = (payload.get("password") or "").strip()

    if not username or not password:
        return json_response(
            {"ok": False, "message": "Username and password are required."},
            status=400,
        )

    if username != local_server.LOGIN_USERNAME or password != local_server.LOGIN_PASSWORD:
        return json_response(
            {"ok": False, "message": "Invalid username or password."},
            status=401,
        )

    token = local_server.create_session()
    return json_response(
        {"ok": True, "message": "Logged in successfully."},
        cookies=[f"session={token}; Path=/; HttpOnly; SameSite=Lax"],
    )


@app.post("/api/logout")
def logout():
    auth = require_auth()
    if auth:
        return auth
    local_server.delete_session(request.cookies.get("session", ""))
    return json_response(
        {"ok": True, "message": "Logged out."},
        cookies=["session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"],
    )


@app.get("/api/status")
def status():
    auth = require_auth()
    if auth:
        return auth
    return json_response(local_server.get_state())


@app.get("/api/resumes")
def resumes():
    auth = require_auth()
    if auth:
        return auth
    return json_response(
        {
            "ok": True,
            "resumes": local_server.list_resumes(),
            "default": local_server.DEFAULT_PDF_PATH,
        }
    )


@app.get("/api/sender-preview-config")
def sender_preview_config():
    auth = require_auth()
    if auth:
        return auth
    return json_response(
        {
            "ok": True,
            "senderEmail": os.environ.get("SENDER_EMAIL", "").strip(),
            "defaultSubject": local_server.DEFAULT_SUBJECT,
            "defaultPdf": local_server.DEFAULT_PDF_PATH,
        }
    )


@app.get("/api/credentials")
def credentials():
    auth = require_auth()
    if auth:
        return auth

    selected_id = request.args.get("id", "")
    credentials_list = local_server.list_credentials()

    if selected_id:
        credential = next(
            (item for item in credentials_list if item["id"] == selected_id),
            None,
        )
        if credential is None:
            return json_response(
                {"ok": False, "message": "Credential owner not found."},
                status=404,
            )
        return json_response({"ok": True, "credential": credential})

    return json_response(
        {
            "ok": True,
            "credentials": credentials_list,
            "default": credentials_list[0]["id"] if credentials_list else "",
        }
    )


@app.post("/api/credentials")
def save_credential():
    auth = require_auth()
    if auth:
        return auth
    try:
        credential = local_server.save_owner_credential(request.get_json(silent=True) or {})
        return json_response(
            {
                "ok": True,
                "credential": credential,
                "credentials": local_server.list_credentials(),
            }
        )
    except Exception as exc:
        return json_response({"ok": False, "message": str(exc)}, status=400)


@app.delete("/api/credentials")
def delete_credential():
    auth = require_auth()
    if auth:
        return auth
    try:
        deleted_id = local_server.delete_owner_credential(request.args.get("id", ""))
        return json_response(
            {
                "ok": True,
                "id": deleted_id,
                "credentials": local_server.list_credentials(),
            }
        )
    except Exception as exc:
        return json_response({"ok": False, "message": str(exc)}, status=400)


@app.get("/api/email-bodies")
def email_bodies():
    auth = require_auth()
    if auth:
        return auth

    selected_path = request.args.get("path", "")
    if selected_path:
        try:
            return json_response(
                {
                    "ok": True,
                    "body": {
                        "path": selected_path,
                        "content": local_server.read_email_body(selected_path),
                    },
                }
            )
        except Exception as exc:
            return json_response({"ok": False, "message": str(exc)}, status=404)

    return json_response(
        {
            "ok": True,
            "templates": local_server.list_email_templates(),
            "default": local_server.DEFAULT_HTML_BODY_PATH,
        }
    )


@app.post("/api/email-bodies")
def save_email_body():
    auth = require_auth()
    if auth:
        return auth
    try:
        saved_body = local_server.save_email_body(request.get_json(silent=True) or {})
        return json_response(
            {
                "ok": True,
                "body": saved_body,
                "templates": local_server.list_email_templates(),
            }
        )
    except Exception as exc:
        return json_response({"ok": False, "message": str(exc)}, status=400)


@app.delete("/api/email-bodies")
def delete_email_body():
    auth = require_auth()
    if auth:
        return auth
    try:
        deleted_path = local_server.delete_email_body(request.args.get("path", ""))
        return json_response(
            {
                "ok": True,
                "path": deleted_path,
                "templates": local_server.list_email_templates(),
            }
        )
    except Exception as exc:
        return json_response({"ok": False, "message": str(exc)}, status=400)


@app.post("/api/upload-excel")
def upload_excel():
    auth = require_auth()
    if auth:
        return auth
    try:
        from datetime import datetime

        target = upload_file_to(
            "data",
            {".xlsx"},
            lambda _: f"uploaded_receivers_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
        )
        return json_response({"ok": True, "path": str(target)})
    except Exception as exc:
        return json_response({"ok": False, "message": str(exc)}, status=400)


@app.post("/api/upload-email-body")
def upload_email_body():
    auth = require_auth()
    if auth:
        return auth
    try:
        target = upload_file_to(
            "email_bodies",
            {".html", ".htm"},
            lambda original_name: Path(original_name).name,
        )
        return json_response(
            {
                "ok": True,
                "body": {
                    "label": local_server.make_template_label(target.stem),
                    "path": str(target),
                    "content": target.read_text(encoding="utf-8"),
                },
                "templates": local_server.list_email_templates(),
            }
        )
    except Exception as exc:
        return json_response({"ok": False, "message": str(exc)}, status=400)


@app.post("/api/send")
def send():
    auth = require_auth()
    if auth:
        return auth

    current = local_server.get_state()
    if current["running"]:
        return json_response(
            {"ok": False, "message": "Email sending is already running."},
            status=409,
        )

    payload = request.get_json(silent=True) or {}
    local_server.stop_event.clear()
    local_server.set_state(
        running=True,
        mode=payload.get("mode"),
        message="Sending started.",
        events=[],
        last_event_id=0,
    )

    # Vercel Python functions are serverless, so background threads are not
    # reliable after the HTTP response returns. Run the send step in-request.
    local_server.run_send(payload)
    return json_response({"ok": True, **local_server.get_state()})


@app.post("/api/stop")
def stop():
    auth = require_auth()
    if auth:
        return auth
    local_server.stop_event.set()
    local_server.set_state(message="Stop requested. Waiting for the current email step to finish.")
    return json_response({"ok": True, **local_server.get_state()})


@app.errorhandler(404)
def not_found(_error):
    if request.path.startswith("/api/"):
        return json_response({"ok": False, "message": "Not found."}, status=404)
    return redirect("/")
