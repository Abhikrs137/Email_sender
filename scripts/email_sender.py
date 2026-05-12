#!/usr/bin/env python3
"""
Bulk Email Sender
Sends up to 200 emails/day from an Excel contact list with PDF attachment.
After each email is sent, the row is deleted from the Excel file.
"""

import smtplib
import pandas as pd
import os
import sys
import json
import argparse
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from pathlib import Path
from datetime import datetime


CONFIG_FILE = "config/sender_config.json"
LOG_FILE = "logs/email_log.txt"
APP_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = APP_DIR / ".env"


def load_dotenv_file():
    if not ENV_FILE.exists():
        return
    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv_file()

# Deployment-safe defaults: read from environment variables.
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "").strip()
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "").strip()
DEFAULT_SUBJECT = "Application"
DEFAULT_DAILY_LIMIT = 200
DEFAULT_DELAY = 2.0
DEFAULT_EXCEL_PATH = "data/email.xlsx"
DEFAULT_PDF_PATH = "resume/Abhishek_3Year.pdf"
DEFAULT_HTML_BODY_PATH = "email_bodies/email_body_template.html"

MAX_DAILY = 200


def resolve_path(path):
    path = Path(path)
    if path.is_absolute():
        return path
    
    # Handle cases where the path might include the old folder name prefix
    if path.parts and path.parts[0] == "email_sender_abhi":
        return APP_DIR / Path(*path.parts[1:])
    return APP_DIR / path


def load_config():
    config_path = resolve_path(CONFIG_FILE)
    if config_path.exists():
        with open(config_path, "r") as f:
            return json.load(f)
    return {}


def save_config(config):
    config_path = resolve_path(CONFIG_FILE)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)


def log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    console_encoding = sys.stdout.encoding or "utf-8"
    print(line.encode(console_encoding, errors="replace").decode(console_encoding))
    log_path = resolve_path(LOG_FILE)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_contacts(excel_path):
    df = pd.read_excel(resolve_path(excel_path))
    # Normalize column names
    df.columns = [c.strip().lower() for c in df.columns]
    if "username" in df.columns and "name" not in df.columns:
        df = df.rename(columns={"username": "name"})
    if "name" not in df.columns or "email" not in df.columns:
        raise ValueError("Excel must have 'name' (or 'username') and 'email' columns.")
    df = df.dropna(subset=["email"])
    return df


def save_contacts(df, excel_path):
    df.to_excel(resolve_path(excel_path), index=False)


def build_email(sender_email, recipient_name, recipient_email, html_body, pdf_path, subject):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender_email
    msg["To"] = recipient_email

    # Personalize body: replace {{name}} placeholder if present
    personalized_html = html_body.replace("{{name}}", recipient_name).replace("{{Name}}", recipient_name)
    msg.attach(MIMEText(personalized_html, "html"))

    # Attach PDF
    if pdf_path:
        resolved_pdf_path = resolve_path(pdf_path)
        if not resolved_pdf_path.exists():
            raise FileNotFoundError(f"PDF attachment not found: {resolved_pdf_path}")
        with open(resolved_pdf_path, "rb") as f:
            pdf_data = f.read()
        filename = resolved_pdf_path.name
        part = MIMEApplication(pdf_data, Name=filename)
        part["Content-Disposition"] = f'attachment; filename="{filename}"'
        msg.attach(part)

    return msg


def load_html_body(html_body_path):
    with open(resolve_path(html_body_path), "r", encoding="utf-8") as f:
        return f.read()


def connect_smtp(sender_email, app_password):
    try:
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        server.login(sender_email, app_password)
        log("Connected to Gmail SMTP.")
        return server
    except Exception as e:
        log(f"SMTP connection failed: {e}")
        sys.exit(1)


def remove_contact_entry(excel_path, recipient_name, recipient_email):
    resolved_excel_path = resolve_path(excel_path)
    if not resolved_excel_path.exists():
        raise FileNotFoundError(f"Excel file not found while removing sent row: {resolved_excel_path}")

    df_updated = pd.read_excel(resolved_excel_path)
    df_updated.columns = [c.strip().lower() for c in df_updated.columns]

    if "email" not in df_updated.columns:
        raise ValueError("Excel file is missing 'email' column while removing sent row.")

    email_series = df_updated["email"].astype(str).str.strip().str.lower()
    target_email = str(recipient_email).strip().lower()

    # Match by both name and email first so duplicate emails are handled safely.
    match_mask = email_series == target_email
    if "name" in df_updated.columns:
        name_series = df_updated["name"].astype(str).str.strip().str.lower()
        target_name = str(recipient_name).strip().lower()
        strict_mask = match_mask & (name_series == target_name)
        if strict_mask.any():
            match_mask = strict_mask

    matching_indices = df_updated.index[match_mask]
    if len(matching_indices) == 0:
        raise ValueError(f"Sent contact row not found in Excel for {recipient_name} <{recipient_email}>")

    # Remove only one row (first match), then write atomically.
    df_after_delete = df_updated.drop(index=matching_indices[0])
    temp_path = resolved_excel_path.with_suffix(".tmp.xlsx")
    df_after_delete.to_excel(temp_path, index=False)
    temp_path.replace(resolved_excel_path)


def get_app_password(cli_password=None):
    app_password = cli_password or GMAIL_APP_PASSWORD
    if not app_password:
        print("Google App Password is missing. Set GMAIL_APP_PASSWORD environment variable.")
        sys.exit(1)
    return app_password


def get_sender_email(cli_sender=None):
    sender_email = cli_sender or SENDER_EMAIL
    if not sender_email:
        print("Sender email is missing. Set SENDER_EMAIL environment variable.")
        sys.exit(1)
    return sender_email


def send_single_email(recipient_name, recipient_email, sender_email, app_password, pdf_path, html_body_path, subject, on_progress=None, html_body_content=None):
    html_body = html_body_content if html_body_content is not None else load_html_body(html_body_path)
    server = connect_smtp(sender_email, app_password)

    name = recipient_name.strip()
    email = recipient_email.strip()

    try:
        msg = build_email(sender_email, name, email, html_body, pdf_path, subject)
        server.sendmail(sender_email, email, msg.as_string())
        log(f"Sent to {name} <{email}>")
        if on_progress:
            on_progress("sent", name, email, f"Sent to {name} <{email}>")
    except Exception as e:
        log(f"Failed to send to {name} <{email}>: {e}")
        if on_progress:
            on_progress("failed", name, email, f"Failed to send to {name} <{email}>: {e}")
    finally:
        server.quit()


def send_emails(excel_path, sender_email, app_password, pdf_path, html_body_path, subject, daily_limit=MAX_DAILY, delay=2, should_stop=None, on_progress=None, html_body_content=None):
    df = load_contacts(excel_path)
    total_remaining = len(df)

    if total_remaining == 0:
        log("No contacts remaining in Excel file.")
        return

    log(f"Contacts remaining: {total_remaining}")

    html_body = html_body_content if html_body_content is not None else load_html_body(html_body_path)

    to_send = df.head(daily_limit).copy()
    log(f"Sending up to {daily_limit} emails today ({len(to_send)} will be sent).")

    sent_count = 0
    failed_count = 0

    server = connect_smtp(sender_email, app_password)

    try:
        for idx, row in to_send.iterrows():
            if should_stop and should_stop():
                log("Bulk sending stopped by user.")
                break

            name = str(row["name"]).strip()
            email = str(row["email"]).strip()

            try:
                msg = build_email(sender_email, name, email, html_body, pdf_path, subject)
                server.sendmail(sender_email, email, msg.as_string())
                log(f"Sent to {name} <{email}>")
                if on_progress:
                    on_progress("sent", name, email, f"Sent to {name} <{email}>")
                remove_contact_entry(excel_path, name, email)
                sent_count += 1
            except Exception as e:
                log(f"Failed to send to {name} <{email}>: {e}")
                if on_progress:
                    on_progress("failed", name, email, f"Failed to send to {name} <{email}>: {e}")
                failed_count += 1

            if should_stop and should_stop():
                log("Bulk sending stopped by user.")
                break

            time.sleep(delay)
    finally:
        server.quit()

    log(f"\n=== Done: {sent_count} sent, {failed_count} failed ===")
    log(f"Remaining contacts in Excel: {total_remaining - sent_count}")


def interactive_mode():
    print("\n" + "="*55)
    print("         BULK EMAIL SENDER")
    print("="*55)
    config = load_config()
    config.setdefault("excel_path", DEFAULT_EXCEL_PATH)
    config.setdefault("pdf_path", DEFAULT_PDF_PATH)
    config.setdefault("html_body_path", DEFAULT_HTML_BODY_PATH)
    mode = ""
    while mode not in {"1", "2", "single", "bulk"}:
        print("\nChoose send mode:")
        print("1. Single mail")
        print("2. Bulk mail")
        mode = input("Enter 1 or 2: ").strip().lower()

    def prompt(label, key, secret=False):
        default = config.get(key, "")
        hint = f" [{default}]" if default and not secret else ""
        val = input(f"{label}{hint}: ").strip()
        if not val and default:
            val = default
        if val and not secret:
            config[key] = val
        return val

    html_path   = prompt("HTML email body path",       "html_body_path")
    subject     = prompt("Email subject",              "subject")
    sender = get_sender_email()
    app_pass = get_app_password()
    pdf_path = DEFAULT_PDF_PATH

    save_config(config)

    # Validate paths
    for path, label in [(pdf_path, "PDF"), (html_path, "HTML body")]:
        if not resolve_path(path).exists():
            print(f"{label} file not found: {path}")
            sys.exit(1)

    if mode in {"1", "single"}:
        recipient_name = input("Recipient name: ").strip()
        recipient_email = input("Recipient email: ").strip()
        if not recipient_name or not recipient_email:
            print("Name and email are required for single mail.")
            sys.exit(1)

        print(f"\nReady to send one email to {recipient_name} <{recipient_email}>.")
    else:
        excel_path = DEFAULT_EXCEL_PATH
        limit_str = prompt(f"Emails to send today (max {MAX_DAILY})", "daily_limit")
        delay_str = prompt("Delay between emails (seconds)", "delay")
        save_config(config)

        daily_limit = int(limit_str) if limit_str.isdigit() else MAX_DAILY
        daily_limit = min(daily_limit, MAX_DAILY)
        delay = float(delay_str) if delay_str else 2.0

        if not resolve_path(excel_path).exists():
            print(f"Excel file not found: {excel_path}")
            sys.exit(1)

        print(f"\nReady to send up to {daily_limit} emails.")

    confirm = input("Type 'yes' to start: ").strip().lower()
    if confirm != "yes":
        print("Aborted.")
        sys.exit(0)

    if mode in {"1", "single"}:
        send_single_email(recipient_name, recipient_email, sender, app_pass, pdf_path, html_path, subject)
    else:
        send_emails(excel_path, sender, app_pass, pdf_path, html_path, subject, daily_limit, delay)


def cli_mode(args):
    send_emails(
        excel_path=args.excel or DEFAULT_EXCEL_PATH,
        sender_email=get_sender_email(args.sender),
        app_password=get_app_password(args.password),
        pdf_path=args.pdf or DEFAULT_PDF_PATH,
        html_body_path=args.html or DEFAULT_HTML_BODY_PATH,
        subject=args.subject,
        daily_limit=min(args.limit, MAX_DAILY),
        delay=args.delay,
    )


def cli_single_mode(args):
    send_single_email(
        recipient_name=args.name,
        recipient_email=args.to,
        sender_email=get_sender_email(args.sender),
        app_password=get_app_password(args.password),
        pdf_path=args.pdf or DEFAULT_PDF_PATH,
        html_body_path=args.html or DEFAULT_HTML_BODY_PATH,
        subject=args.subject,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Bulk email sender (200/day) with Excel contact list and PDF attachment.",
        add_help=True
    )
    parser.add_argument("--excel",    help=f"Path to Excel contacts file (.xlsx), default {DEFAULT_EXCEL_PATH}")
    parser.add_argument("--sender",   help="Gmail sender address, optional if SENDER_EMAIL is set")
    parser.add_argument("--password", help="Google App Password, optional if GMAIL_APP_PASSWORD is set")
    parser.add_argument("--pdf",      help=f"Path to PDF resume attachment, default {DEFAULT_PDF_PATH}")
    parser.add_argument("--html",     help=f"Path to HTML email body file, default {DEFAULT_HTML_BODY_PATH}")
    parser.add_argument("--subject",  help="Email subject line", default=DEFAULT_SUBJECT)
    parser.add_argument("--limit",    help=f"Max emails to send (default {DEFAULT_DAILY_LIMIT})", type=int, default=DEFAULT_DAILY_LIMIT)
    parser.add_argument("--delay",    help=f"Seconds between emails (default {DEFAULT_DELAY})", type=float, default=DEFAULT_DELAY)
    parser.add_argument("--bulk",     help="Send bulk emails using the default Excel file", action="store_true")
    parser.add_argument("--single",   help="Send one email instead of bulk emails", action="store_true")
    parser.add_argument("--name",     help="Recipient name for single email mode")
    parser.add_argument("--to",       help="Recipient email address for single email mode")

    args = parser.parse_args()

    if args.single:
        for field in ["name", "to"]:
            if not getattr(args, field):
                print(f"--{field} is required in single email mode.")
                sys.exit(1)
        cli_single_mode(args)
    elif args.bulk or args.excel or args.sender:
        # CLI mode - all required args must be present
        cli_mode(args)
    else:
        # Interactive mode
        interactive_mode()
