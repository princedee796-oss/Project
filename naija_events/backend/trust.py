"""
trust.py
--------
The "is this event legit or a scam?" feature. Three layers work together:

  1. Automatic check   - assess_event() looks for warning signs and good
                         signs and turns them into a 0-100 trust score.
  2. Community reports - any logged-in user can report an event.
  3. Admin review      - an admin can mark an event Verified or Flagged.
                         Flagged events cannot be booked.

The automatic score is a guide, not proof. An event that "looks OK" has
simply not shown any warning signs yet.
"""

import re
import sqlite3
from datetime import datetime, timezone

from database import get_connection

REPORT_REASONS = {
    "fake_event": "The event looks fake or does not exist",
    "asks_for_transfer": "Asks for payment by direct bank transfer",
    "wrong_details": "Details are wrong or misleading",
    "impersonation": "Pretends to be another organizer or brand",
    "other": "Something else",
}

VALID_STATUSES = ("unverified", "verified", "flagged")

# Phrases that often show up in scam listings (kept short and specific
# so normal events are not caught by accident).
SCAM_PHRASES = [
    "guaranteed returns", "guaranteed profit", "double your money", "forex",
    "get rich", "free money", "processing fee", "western union",
    "gift card", "send money",
]


def _age_in_days(created_at):
    """How many days ago a 'YYYY-MM-DD HH:MM:SS' timestamp was."""
    try:
        made = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return 0
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return (now - made).days


def assess_event(event_id):
    """
    Return a dictionary describing how trustworthy an event looks:
    score (0-100), level, a label, and the list of signals behind it.
    Returns None if the event does not exist.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM events WHERE id = ?", (event_id,))
    ev = cur.fetchone()
    if ev is None:
        conn.close()
        return None

    signals = []

    def add(text, points, kind):
        signals.append({"text": text, "points": points, "kind": kind})

    organizer_id = ev["organizer_id"]

    # --- the organizer -----------------------------------------------------
    cur.execute("SELECT created_at FROM users WHERE id = ?", (organizer_id,))
    org = cur.fetchone()
    age = _age_in_days(org["created_at"]) if org else 0
    if age >= 30:
        add("Organizer account is more than 30 days old", 10, "good")
    elif age < 1:
        add("Organizer account was created today", -5, "bad")

    cur.execute("""
        SELECT COUNT(*) AS c FROM tickets t JOIN events e ON t.event_id = e.id
        WHERE e.organizer_id = ? AND t.status = 'used'
    """, (organizer_id,))
    if cur.fetchone()["c"] > 0:
        add("Guests have already been checked in at this organizer's events", 15, "good")

    cur.execute("SELECT COUNT(*) AS c FROM events WHERE organizer_id = ? AND id != ? "
                "AND verification_status = 'verified'", (organizer_id, event_id))
    if cur.fetchone()["c"] > 0:
        add("This organizer has other events verified by Naija Events", 15, "good")

    cur.execute("SELECT COUNT(*) AS c FROM events WHERE organizer_id = ? AND id != ? "
                "AND verification_status = 'flagged'", (organizer_id, event_id))
    if cur.fetchone()["c"] > 0:
        add("This organizer has another event flagged as a scam", -40, "bad")

    # --- the listing itself ------------------------------------------------
    description = (ev["description"] or "").strip()
    text = (ev["title"] + " " + description).lower()

    if len(description) < 30:
        add("Description is missing or very short", -15, "bad")
    elif len(description) >= 80:
        add("Event has a detailed description", 5, "good")

    hits = [phrase for phrase in SCAM_PHRASES if phrase in text]
    if hits:
        add("Uses phrases common in scams: " + ", ".join(hits), -min(30, 15 * len(hits)), "bad")

    # 10 digit numbers look like Nigerian bank account numbers
    if re.search(r"\b\d{10}\b", text) or re.search(r"account number|transfer to|pay to", text):
        add("Asks people to pay by bank transfer outside the platform", -20, "bad")

    cur.execute("SELECT AVG(price) AS avg_price, COUNT(*) AS c FROM events "
                "WHERE category = ? AND id != ? AND price > 0", (ev["category"], event_id))
    row = cur.fetchone()
    if row["c"] >= 2 and ev["price"] > 5 * row["avg_price"]:
        add("Ticket price is more than 5x the average for this category", -15, "bad")

    if ev["capacity"] > 50000:
        add("Unusually large capacity", -10, "bad")

    # --- community reports -------------------------------------------------
    cur.execute("SELECT COUNT(*) AS c FROM event_reports WHERE event_id = ?", (event_id,))
    report_count = cur.fetchone()["c"]
    if report_count:
        add(f"{report_count} user report(s) against this event", -min(45, 15 * report_count), "bad")

    conn.close()

    if not signals:
        add("No warning signs found automatically", 0, "info")

    score = max(0, min(100, 60 + sum(s["points"] for s in signals)))
    status = ev["verification_status"] or "unverified"

    # An admin decision always beats the automatic score
    if status == "flagged":
        level, label, score = "scam", "Flagged as a suspected scam", 0
    elif status == "verified":
        level, label = "verified", "Verified by Naija Events"
    elif score >= 60:
        level, label = "ok", "Looks OK (not yet verified)"
    elif score >= 35:
        level, label = "caution", "Be careful"
    else:
        level, label = "danger", "High risk"

    return {
        "event_id": ev["id"],
        "status": status,
        "score": score,
        "level": level,
        "label": label,
        "signals": signals,
        "report_count": report_count,
    }


def create_report(event_id, user_id, reason, details):
    """Save a user's report. Returns (ok, message, http_status)."""
    if reason not in REPORT_REASONS:
        return False, "Please choose a reason for your report", 400
    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return False, "Please log in to report an event", 401

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT organizer_id FROM events WHERE id = ?", (event_id,))
    ev = cur.fetchone()
    cur.execute("SELECT id FROM users WHERE id = ?", (user_id,))
    user = cur.fetchone()

    if ev is None:
        conn.close()
        return False, "Event not found", 404
    if user is None:
        conn.close()
        return False, "Please log in to report an event", 401
    if ev["organizer_id"] == user_id:
        conn.close()
        return False, "You cannot report your own event", 400

    try:
        cur.execute(
            "INSERT INTO event_reports (event_id, user_id, reason, details) VALUES (?, ?, ?, ?)",
            (event_id, user_id, reason, (details or "").strip()[:500]),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return False, "You have already reported this event", 400
    conn.close()
    return True, "Thank you - your report was sent to the Naija Events team", 201


def set_status(event_id, status):
    """Admin decision: verified, flagged, or back to unverified."""
    if status not in VALID_STATUSES:
        return False, "Status must be verified, flagged or unverified"
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE events SET verification_status = ? WHERE id = ?", (status, event_id))
    conn.commit()
    changed = cur.rowcount
    conn.close()
    if not changed:
        return False, "Event not found"
    return True, f"Event marked as {status}"


def admin_overview():
    """Every event with its reports and score, most-reported first (admin page)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT e.id, e.title, e.verification_status, u.full_name AS organizer,
               (SELECT COUNT(*) FROM event_reports r WHERE r.event_id = e.id) AS report_count
        FROM events e JOIN users u ON u.id = e.organizer_id
        ORDER BY report_count DESC, e.id DESC
    """)
    rows = [dict(r) for r in cur.fetchall()]
    for row in rows:
        cur.execute("""
            SELECT r.reason, r.details, r.created_at, u.full_name AS reporter
            FROM event_reports r JOIN users u ON u.id = r.user_id
            WHERE r.event_id = ? ORDER BY r.created_at DESC
        """, (row["id"],))
        row["reports"] = [dict(r) for r in cur.fetchall()]
    conn.close()

    for row in rows:
        a = assess_event(row["id"])
        row["score"], row["level"], row["label"] = a["score"], a["level"], a["label"]
    return rows
