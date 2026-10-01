"""
Email report sender.
Uses Python's built-in smtplib — no extra package needed.
Sends an HTML summary of all saved data to REPORT_EMAIL.
"""

import json
import smtplib
import logging
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import database as db
from config import GMAIL_SENDER, GMAIL_APP_PASSWORD, REPORT_EMAIL

logger = logging.getLogger(__name__)


def _fmt(d) -> str:
    if not d:
        return "—"
    try:
        return date.fromisoformat(str(d)).strftime("%d %b %Y")
    except Exception:
        return str(d)


def _build_html() -> str:
    today       = date.today().strftime("%d %B %Y")
    month_label = date.today().strftime("%B %Y")
    stats       = db.get_summary_stats()
    s_rows      = db.get_shopping_list()
    l_rows      = db.get_learning_list()
    o_rows      = db.get_others_list()
    e_rows      = db.get_expenses()
    total_exp   = sum(r["amount"] for r in e_rows)

    def table_rows(rows, cols_fn) -> str:
        return "".join(
            f"<tr>{''.join(f'<td style=\"padding:6px 12px;border-bottom:1px solid #eee\">{v}</td>' for v in cols_fn(r))}</tr>"
            for r in rows
        )

    th = lambda *cols: "".join(
        f"<th style=\"padding:6px 12px;text-align:left;background:#f5f5f5;\">{c}</th>"
        for c in cols
    )

    shopping_html = ""
    if s_rows:
        shopping_html = f"""
        <h2 style="color:#2d7dd2">🛒 Shopping List</h2>
        <table style="border-collapse:collapse;width:100%;font-size:14px">
          <tr>{th("ID","Items","Date")}</tr>
          {table_rows(s_rows, lambda r: [
              f"s{r['id']}",
              ", ".join(json.loads(r["items"])),
              _fmt(r["date"])
          ])}
        </table>"""

    learning_html = ""
    if l_rows:
        learning_html = f"""
        <h2 style="color:#2d7dd2">📚 Learning List</h2>
        <table style="border-collapse:collapse;width:100%;font-size:14px">
          <tr>{th("ID","Topic","Resource","Date")}</tr>
          {table_rows(l_rows, lambda r: [
              f"l{r['id']}",
              r["topic"],
              r.get("resource") or "—",
              _fmt(r["date"])
          ])}
        </table>"""

    reminders_html = ""
    if o_rows:
        reminders_html = f"""
        <h2 style="color:#2d7dd2">📌 Reminders</h2>
        <table style="border-collapse:collapse;width:100%;font-size:14px">
          <tr>{th("ID","Description","Date")}</tr>
          {table_rows(o_rows, lambda r: [
              f"o{r['id']}",
              r["description"],
              _fmt(r["date"])
          ])}
        </table>"""

    expenses_html = ""
    if e_rows:
        expenses_html = f"""
        <h2 style="color:#2d7dd2">💸 Expenses — {month_label} &nbsp;
          <span style="font-size:16px;color:#e63946">Total: ₹{total_exp:.0f}</span>
        </h2>
        <table style="border-collapse:collapse;width:100%;font-size:14px">
          <tr>{th("Date","Amount","Category","Description")}</tr>
          {table_rows(e_rows, lambda r: [
              r["date"],
              f"₹{r['amount']:.0f}",
              r.get("category","—"),
              r.get("description","")[:50]
          ])}
        </table>"""

    return f"""
    <html><body style="font-family:Arial,sans-serif;max-width:700px;margin:auto;color:#333">
      <h1 style="background:#2d7dd2;color:white;padding:16px;border-radius:8px;margin-bottom:0">
        Your Life Manager Report
      </h1>
      <p style="color:#888;margin-top:4px">Generated on {today}</p>

      <div style="background:#f9f9f9;border-radius:8px;padding:16px;margin:16px 0">
        <b>Quick Stats</b><br><br>
        🛒 &nbsp;{stats['shopping']} shopping item(s) pending &nbsp;|&nbsp;
        📚 &nbsp;{stats['learning']} learning item(s) pending &nbsp;|&nbsp;
        📌 &nbsp;{stats['reminders']} reminder(s) &nbsp;|&nbsp;
        💸 &nbsp;₹{stats['expense_total']:.0f} spent in {month_label}
      </div>

      {shopping_html}
      {learning_html}
      {reminders_html}
      {expenses_html}

      <p style="color:#aaa;font-size:12px;margin-top:32px">
        Sent by your AI Life Manager bot · Reply to this email won't reach the bot.
      </p>
    </body></html>
    """


def send_report() -> str:
    """
    Build and send the HTML report email.
    Returns a status message string (for the Telegram reply).
    """
    if not GMAIL_SENDER or not GMAIL_APP_PASSWORD:
        return (
            "Email not configured.\n"
            "Add GMAIL_SENDER and GMAIL_APP_PASSWORD to your .env file.\n"
            "See .env.example for instructions."
        )

    html = _build_html()
    today = date.today().strftime("%d %b %Y")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Your Life Manager Report — {today}"
    msg["From"]    = GMAIL_SENDER
    msg["To"]      = REPORT_EMAIL
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_SENDER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_SENDER, REPORT_EMAIL, msg.as_string())
        logger.info(f"Report sent to {REPORT_EMAIL}")
        return f"Report sent to {REPORT_EMAIL}"
    except smtplib.SMTPAuthenticationError:
        logger.error("SMTP auth failed")
        return (
            "Gmail login failed. Make sure you're using an App Password, "
            "not your regular Gmail password.\n"
            "Get one at: myaccount.google.com → Security → App Passwords"
        )
    except Exception as e:
        logger.error(f"Email send failed: {e}")
        return f"Failed to send email: {e}"
