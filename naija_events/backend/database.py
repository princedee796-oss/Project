"""
database.py
-----------
Handles all the SQLite database work for the Naija Events project.
I kept this file simple on purpose - one connection helper, one function
that builds the tables, and a small set of functions that run queries.
No ORM, just plain sqlite3 + SQL, since that is what we learned in class.
"""

import sqlite3
import os

from security import hash_password

# The database file lives inside backend/data/
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "events.db")


def get_connection():
    """Open a connection to the SQLite file and turn on foreign keys."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row  # lets us access columns by name
    return conn


def clean_name(name):
    """Trim a name and squash repeated spaces, so 'Amaka   Okafor ' == 'Amaka Okafor'."""
    return " ".join(str(name or "").split())


def name_taken(conn, name, exclude_id=None):
    """
    True if another user already has this name. The check ignores capital
    letters and extra spaces, so 'amaka okafor' counts as 'Amaka Okafor'.
    exclude_id lets a user keep their own name when editing their profile.
    """
    sql = "SELECT 1 FROM users WHERE lower(trim(full_name)) = ?"
    params = [clean_name(name).lower()]
    if exclude_id is not None:
        sql += " AND id != ?"
        params.append(exclude_id)
    return conn.execute(sql, params).fetchone() is not None


def init_db():
    """
    Create every table the app needs if they do not already exist.
    Called once when the server starts.
    """
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'attendee',   -- 'attendee', 'organizer' or 'admin'
            status TEXT NOT NULL DEFAULT 'active',   -- 'active' or 'suspended'
            last_login_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT,
            location TEXT NOT NULL,
            event_date TEXT NOT NULL,      -- stored as YYYY-MM-DD
            event_time TEXT,
            price REAL DEFAULT 0,
            capacity INTEGER DEFAULT 100,
            organizer_id INTEGER NOT NULL,
            image_emoji TEXT DEFAULT '🎉',
            verification_status TEXT DEFAULT 'unverified',   -- unverified, verified, flagged
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (organizer_id) REFERENCES users (id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_code TEXT UNIQUE NOT NULL,
            event_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            status TEXT DEFAULT 'valid',      -- valid, used, cancelled
            issued_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (event_id) REFERENCES events (id),
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)

    # People can report a suspicious event. One report per person per event.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS event_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            details TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (event_id, user_id),
            FOREIGN KEY (event_id) REFERENCES events (id),
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)

    # Everything that happens on the site is written here so admins can see
    # (and manage) what each organizer and attendee has been doing.
    # user_id        = who did it (NULL for e.g. a failed login with an unknown email)
    # target_user_id = who it was done TO (used for admin actions on someone's account)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT NOT NULL,
            details TEXT,
            target_user_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_activity_user ON activity_log (user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_activity_target ON activity_log (target_user_id)")

    # Older databases (made before these features) do not have the new columns yet
    columns = [row["name"] for row in cur.execute("PRAGMA table_info(events)")]
    if "verification_status" not in columns:
        cur.execute("ALTER TABLE events ADD COLUMN verification_status TEXT DEFAULT 'unverified'")
    user_columns = [row["name"] for row in cur.execute("PRAGMA table_info(users)")]
    if "status" not in user_columns:
        cur.execute("ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
    if "last_login_at" not in user_columns:
        cur.execute("ALTER TABLE users ADD COLUMN last_login_at TEXT")

    # Names must be unique (ignoring capitals), so an event's organizer name
    # always means one person. If an old database already has two people with
    # the same name the index cannot be built - the checks in the server still
    # stop NEW duplicates, and the admin can rename the old ones.
    try:
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_unique_name ON users (lower(trim(full_name)))")
    except sqlite3.IntegrityError:
        print("Warning: some users already share a name. Please make the names unique.")

    conn.commit()
    conn.close()


def log_activity(action, user_id=None, details="", target_user_id=None):
    """Write one line to the activity log. Never lets a logging problem break a request."""
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO activity_log (user_id, action, details, target_user_id) VALUES (?, ?, ?, ?)",
            (user_id, action, details, target_user_id),
        )
        conn.commit()
        conn.close()
    except sqlite3.Error:
        pass


def create_admin(full_name, email, password):
    """
    Create the FIRST admin. Refuses if any admin already exists - after that,
    admins promote other users from the dashboard. Returns (ok, message).
    """
    email = (email or "").lower().strip()
    if not email or "@" not in email:
        return False, "Please give a valid email address."
    if not password or len(password) < 8:
        return False, "The admin password must be at least 8 characters."
    conn = get_connection()
    try:
        if conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0] > 0:
            return False, "An admin already exists. Existing admins can promote other users from the Admin dashboard."
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            conn.execute("UPDATE users SET role = 'admin', status = 'active' WHERE id = ?", (existing["id"],))
            message = f"Existing account {email} is now the admin."
        else:
            full_name = clean_name(full_name) or "Site Admin"
            if name_taken(conn, full_name):
                return False, f"The name '{full_name}' is already taken. Please choose a different name."
            conn.execute(
                "INSERT INTO users (full_name, email, password, role) VALUES (?, ?, ?, 'admin')",
                (full_name, email, hash_password(password)),
            )
            message = f"Admin account created for {email}."
        conn.commit()
    finally:
        conn.close()
    return True, message


def ensure_admin_user():
    """
    Make sure the site always starts with exactly one admin.

    If there is no admin yet, one is created from the NAIJA_ADMIN_EMAIL /
    NAIJA_ADMIN_PASSWORD environment variables when they are set, otherwise
    the demo admin from the README. Returns the email used when a NEW admin
    was created (so the server can warn about the demo password), else None.
    Once any admin exists this does nothing - it never adds a second admin.
    """
    conn = get_connection()
    has_admin = conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0] > 0
    conn.close()
    if has_admin:
        return None
    email = os.environ.get("NAIJA_ADMIN_EMAIL") or "admin@naijaevents.ng"
    password = os.environ.get("NAIJA_ADMIN_PASSWORD") or "admin1234"
    ok, _ = create_admin("Site Admin", email, password)
    return email if ok and password == "admin1234" else None


def seed_if_empty():
    """
    If the events table has nothing in it, load the sample dataset from
    backend/data/sample_events.json so the app is not empty on first run.
    """
    import json

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS total FROM events")
    total = cur.fetchone()["total"]

    if total == 0:
        sample_path = os.path.join(os.path.dirname(__file__), "data", "sample_events.json")
        if os.path.exists(sample_path):
            with open(sample_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Make one demo organizer account to own the sample events
            cur.execute(
                "INSERT OR IGNORE INTO users (full_name, email, password, role) VALUES (?, ?, ?, ?)",
                ("Demo Organizer", "organizer@naijaevents.ng", hash_password("demo1234"), "organizer"),
            )
            conn.commit()
            cur.execute("SELECT id FROM users WHERE email = ?", ("organizer@naijaevents.ng",))
            organizer_id = cur.fetchone()["id"]

            for ev in data.get("events", []):
                cur.execute("""
                    INSERT INTO events
                        (title, category, description, location, event_date,
                         event_time, price, capacity, organizer_id, image_emoji)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    ev["title"], ev["category"], ev["description"], ev["location"],
                    ev["event_date"], ev.get("event_time", "TBA"), ev.get("price", 0),
                    ev.get("capacity", 100), organizer_id, ev.get("image_emoji", "🎉"),
                ))
            conn.commit()

    conn.close()
