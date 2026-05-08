# Bulk Email Sender

Send personalised Gmail messages with a selected resume PDF and selected email body template.

## Folder Structure

| Path | Purpose |
|---|---|
| `scripts/` | Python sender and local UI server |
| `ui/` | Browser UI |
| `email_bodies/` | HTML email body templates |
| `data/` | Excel contact file |
| `logs/` | Send logs |
| `config/` | Saved local config |

## Main Files

| File | Purpose |
|---|---|
| `scripts/email_sender.py` | Main email sending logic |
| `scripts/email_sender_server.py` | Local browser UI server |
| `ui/email_sender_ui.html` | Browser UI page |
| `email_bodies/email_body_template.html` | Both template |
| `email_bodies/email_body_selenium.html` | Selenium template |
| `email_bodies/email_body_playwright.html` | Playwright template |
| `data/email.xlsx` | Contact list |
| `logs/email_log.txt` | Send results |

## Run The UI

From the `email_sender_abhi` folder:

```bash
python scripts/email_sender_server.py
```

Then open:

```text
http://127.0.0.1:8000/email_sender_ui.html
```

## Command Line

From the `email_sender_abhi` folder:

```bash
python scripts/email_sender.py --bulk
```

Example with explicit paths:

```bash
python scripts/email_sender.py \
  --excel data/email.xlsx \
  --pdf email_sender_abhi/resume/Abhishek_3Year.pdf \
  --html email_bodies/email_body_template.html \
  --subject "Application" \
  --limit 200 \
  --delay 2
```

Use `{{name}}` in email body HTML files to personalize each recipient.
