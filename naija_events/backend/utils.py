"""
utils.py
--------
Small helper functions used by server.py:
  - generating a unique ticket code
  - exporting tickets to a CSV file
  - importing events from a JSON file
  - building the numbers used on the organizer dashboard

Kept as plain functions (no classes) since that matches the level
this project was built at.
"""

import csv
import io
import json
import os
import random
import string
import uuid
from datetime import datetime

from database import get_connection

EXPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "exports")


def generate_ticket_code():
    """
    Build a ticket code that looks like: NGE-7F3K9-2481
    NGE = Naija Events, then a random 5 character block, then 4 digits.
    Using uuid4 under the hood so codes do not repeat.
    """
    block = uuid.uuid4().hex[:5].upper()
    tail = "".join(random.choices(string.digits, k=4))
    return f"NGE-{block}-{tail}"


def export_tickets_to_csv(filename=None):
    """
    Pull every ticket (joined with event + user info) and write it out
    as a CSV file inside the exports/ folder. Returns the file path.
    """
    if not os.path.exists(EXPORTS_DIR):
        os.makedirs(EXPORTS_DIR)

    if filename is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"tickets_export_{stamp}.csv"

    path = os.path.join(EXPORTS_DIR, filename)

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT t.ticket_code, t.status, t.issued_at,
               e.title AS event_title, e.location, e.event_date,
               u.full_name AS attendee_name, u.email AS attendee_email
        FROM tickets t
        JOIN events e ON t.event_id = e.id
        JOIN users u ON t.user_id = u.id
        ORDER BY t.issued_at DESC
    """)
    rows = cur.fetchall()
    conn.close()

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Ticket Code", "Status", "Issued At", "Event", "Location",
            "Event Date", "Attendee Name", "Attendee Email",
        ])
        for r in rows:
            writer.writerow([
                r["ticket_code"], r["status"], r["issued_at"], r["event_title"],
                r["location"], r["event_date"], r["attendee_name"], r["attendee_email"],
            ])

    return path


def import_events_from_json(json_path, organizer_id):
    """
    Read a JSON file shaped like sample_events.json and insert every
    event into the database under the given organizer_id.
    Returns how many events were added.
    """
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    conn = get_connection()
    cur = conn.cursor()
    count = 0
    for ev in data.get("events", []):
        cur.execute("""
            INSERT INTO events
                (title, category, description, location, event_date,
                 event_time, price, capacity, organizer_id, image_emoji)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ev["title"], ev["category"], ev.get("description", ""), ev["location"],
            ev["event_date"], ev.get("event_time", "TBA"), ev.get("price", 0),
            ev.get("capacity", 100), organizer_id, ev.get("image_emoji", "🎉"),
        ))
        count += 1
    conn.commit()
    conn.close()
    return count


def get_statistics():
    """
    Build the numbers shown on the organizer dashboard and used by
    report.py to draw the matplotlib charts.
    """
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) AS c FROM events")
    total_events = cur.fetchone()["c"]

    cur.execute("SELECT COUNT(*) AS c FROM tickets")
    total_tickets = cur.fetchone()["c"]

    cur.execute("SELECT COUNT(*) AS c FROM users")
    total_users = cur.fetchone()["c"]

    cur.execute("""
        SELECT e.title, COUNT(t.id) AS sold
        FROM events e LEFT JOIN tickets t ON t.event_id = e.id
        GROUP BY e.id ORDER BY sold DESC LIMIT 5
    """)
    top_events = [dict(row) for row in cur.fetchall()]

    cur.execute("""
        SELECT category, COUNT(*) AS total FROM events
        GROUP BY category ORDER BY total DESC
    """)
    by_category = [dict(row) for row in cur.fetchall()]

    cur.execute("""
        SELECT SUM(e.price) AS revenue
        FROM tickets t JOIN events e ON t.event_id = e.id
        WHERE t.status != 'cancelled'
    """)
    revenue_row = cur.fetchone()
    total_revenue = revenue_row["revenue"] or 0

    conn.close()

    return {
        "total_events": total_events,
        "total_tickets": total_tickets,
        "total_users": total_users,
        "total_revenue": total_revenue,
        "top_events": top_events,
        "by_category": by_category,
    }
