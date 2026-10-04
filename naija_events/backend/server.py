"""
server.py
---------
This is the whole backend for the Naija Events project.

IMPORTANT: this does NOT use Flask, Django or any web framework.
It only uses Python's built-in http.server module (http.server.BaseHTTPRequestHandler)
plus sqlite3 for the database and json for reading/writing data.

The server does two jobs at once:
  1. It serves the plain HTML/CSS/JS pages that sit in the frontend/ folder.
  2. It answers small JSON REST API requests under the /api/ path, which
     the pages call using fetch() in the browser.

Run it with:  python server.py
Or with a custom port:  python server.py --port 8080
"""

import argparse
import json
import os
import secrets
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# so "import database" / "import utils" work no matter where this is run from
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sqlite3

from database import (init_db, seed_if_empty, ensure_admin_user, get_connection, log_activity,
                      clean_name, name_taken)
import admin as admin_tools
from security import hash_password, verify_password
import trust
import utils

# Login tokens (given to every account) live in memory: {token: user_id}.
# They are cleared when the server restarts, so people just log in again.
SESSION_TOKENS = {}


def revoke_tokens(user_id):
    """Sign a user out everywhere (used when they are suspended, deleted or get a new password)."""
    for token in [t for t, uid in SESSION_TOKENS.items() if uid == int(user_id)]:
        del SESSION_TOKENS[token]

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
CHARTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "charts")

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".csv": "text/csv",
}


class NaijaEventsHandler(BaseHTTPRequestHandler):

    # ---------- small helpers ----------

    def _send_json(self, data, status=200):
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, status=200):
        ext = os.path.splitext(path)[1]
        mime = MIME_TYPES.get(ext, "application/octet-stream")
        try:
            with open(path, "rb") as f:
                body = f.read()
        except FileNotFoundError:
            self._send_json({"error": "Not found"}, 404)
            return
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def _user_from_token(self, cur):
        """Look up who is making this request from the X-Auth-Token header.
        Returns {"id", "role", "status"} read fresh from the database, or None.
        Because it is read fresh, a role change or suspension by an admin
        takes effect on the very next request."""
        user_id = SESSION_TOKENS.get(self.headers.get("X-Auth-Token", ""))
        if user_id is None:
            return None
        cur.execute("SELECT id, full_name, role, status FROM users WHERE id = ?", (user_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def _auth_user(self, cur, roles=None):
        """The logged-in, active user (optionally with one of `roles`).
        If there is no such user, sends the right error and returns None."""
        user = self._user_from_token(cur)
        if user is None:
            self._send_json({"error": "Please log in again to continue"}, 401)
            return None
        if user["status"] != "active":
            self._send_json({"error": "Your account has been suspended. Contact the site admin."}, 403)
            return None
        if roles and user["role"] not in roles:
            self._send_json({"error": "Your account type is not allowed to do this"}, 403)
            return None
        return user

    def _require_admin(self):
        """Return the admin's user id, or send a 401/403 and return None."""
        conn = get_connection()
        user = self._auth_user(conn.cursor(), roles=("admin",))
        conn.close()
        return None if user is None else user["id"]

    @staticmethod
    def _is_admin_tool_route(method, path):
        """Routes handled by admin.py (everything under /api/admin/ except the
        original event-moderation list and status routes)."""
        for base in ("/api/admin/overview", "/api/admin/users", "/api/admin/activity", "/api/admin/tickets"):
            if path == base or path.startswith(base + "/"):
                return True
        return method == "DELETE" and path.startswith("/api/admin/events/")

    def log_message(self, fmt, *args):
        # quieter console output than the default, still shows the basics
        print(f"[{self.log_date_time_string()}] {self.command} {self.path} -> {args[-1] if args else ''}")

    # ---------- routing ----------

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        try:
            if path.startswith("/api/"):
                self.handle_api_get(path, query)
            else:
                self.handle_static(path)
        except admin_tools.AdminError as err:
            self._send_json({"error": err.message}, err.status)
        except Exception as exc:
            traceback.print_exc()
            self._send_json({"error": f"Server error: {exc}"}, 500)

    def do_POST(self):
        try:
            if self.path.startswith("/api/"):
                self.handle_api_post(self.path)
            else:
                self._send_json({"error": "Not found"}, 404)
        except admin_tools.AdminError as err:
            self._send_json({"error": err.message}, err.status)
        except Exception as exc:
            traceback.print_exc()
            self._send_json({"error": f"Server error: {exc}"}, 500)

    def do_PUT(self):
        try:
            if self.path.startswith("/api/"):
                self.handle_api_put(self.path)
            else:
                self._send_json({"error": "Not found"}, 404)
        except admin_tools.AdminError as err:
            self._send_json({"error": err.message}, err.status)
        except Exception as exc:
            traceback.print_exc()
            self._send_json({"error": f"Server error: {exc}"}, 500)

    def do_DELETE(self):
        try:
            if self.path.startswith("/api/"):
                self.handle_api_delete(self.path)
            else:
                self._send_json({"error": "Not found"}, 404)
        except admin_tools.AdminError as err:
            self._send_json({"error": err.message}, err.status)
        except Exception as exc:
            traceback.print_exc()
            self._send_json({"error": f"Server error: {exc}"}, 500)

    # ---------- static file serving ----------

    def handle_static(self, path):
        if path == "/":
            path = "/index.html"

        # allow /pages/xxx.html to map into frontend/pages/xxx.html
        safe_path = path.lstrip("/")
        full_path = os.path.normpath(os.path.join(FRONTEND_DIR, safe_path))

        # basic safety check so people can't escape the frontend folder
        if not full_path.startswith(FRONTEND_DIR):
            self._send_json({"error": "Forbidden"}, 403)
            return

        if path.startswith("/charts/"):
            full_path = os.path.normpath(os.path.join(CHARTS_DIR, path.replace("/charts/", "", 1)))
            if not full_path.startswith(CHARTS_DIR):
                self._send_json({"error": "Forbidden"}, 403)
                return

        if os.path.isdir(full_path):
            full_path = os.path.join(full_path, "index.html")

        self._send_file(full_path)

    # ---------- API: admin dashboard ----------

    def handle_admin(self, method, path, query, body):
        """User/activity management. Every route needs an admin token; the
        rules about WHAT an admin may do live in admin.py."""
        actor = self._require_admin()
        if actor is None:
            return
        parts = path.strip("/").split("/")          # ["api", "admin", "users", "5", "role"]
        area = parts[2] if len(parts) > 2 else ""
        ident = parts[3] if len(parts) > 3 else ""
        action = parts[4] if len(parts) > 4 else ""
        first = lambda key, default="": (query.get(key) or [default])[0]

        if method == "GET" and area == "overview" and not ident:
            return self._send_json(admin_tools.overview())

        if method == "GET" and area == "activity" and not ident:
            user_id = first("user_id")
            return self._send_json(admin_tools.list_activity(
                user_id=int(user_id) if user_id.isdigit() else None,
                action=first("action"), limit=first("limit", "100") or 100,
                offset=first("offset", "0") or 0))

        if area == "users":
            if method == "GET" and not ident:
                return self._send_json({"users": admin_tools.list_users(
                    q=first("q").strip(), role=first("role"), status=first("status"))})
            if not ident.isdigit():
                return self._send_json({"error": "User not found"}, 404)
            user_id = int(ident)
            if method == "GET" and not action:
                return self._send_json(admin_tools.user_detail(user_id))
            if method == "DELETE" and not action:
                message = admin_tools.delete_user(actor, user_id)
                revoke_tokens(user_id)
                return self._send_json({"message": message})
            if method == "POST" and action == "status":
                message = admin_tools.set_user_status(actor, user_id, body.get("status"))
                if body.get("status") == "suspended":
                    revoke_tokens(user_id)
                return self._send_json({"message": message})
            if method == "POST" and action == "role":
                return self._send_json({"message": admin_tools.set_user_role(actor, user_id, body.get("role"))})
            if method == "POST" and action == "password":
                message = admin_tools.reset_user_password(actor, user_id, body.get("password"))
                revoke_tokens(user_id)
                return self._send_json({"message": message})

        if method == "POST" and area == "tickets" and action == "status":
            return self._send_json({"message": admin_tools.set_ticket_status(actor, ident, body.get("status"))})

        if method == "DELETE" and area == "events" and ident.isdigit():
            return self._send_json({"message": admin_tools.delete_event(actor, int(ident))})

        self._send_json({"error": "Unknown API route"}, 404)

    # ---------- API: GET ----------

    def handle_api_get(self, path, query):
        if self._is_admin_tool_route("GET", path):
            self.handle_admin("GET", path, query, {})
            return
        conn = get_connection()
        cur = conn.cursor()

        if path == "/api/events":
            sql = ("SELECT events.*, users.full_name AS organizer_name FROM events "
                   "LEFT JOIN users ON users.id = events.organizer_id WHERE 1=1")
            params = []

            if "q" in query and query["q"][0]:
                sql += " AND title LIKE ?"
                params.append(f"%{query['q'][0]}%")
            if "category" in query and query["category"][0] and query["category"][0] != "All":
                sql += " AND category = ?"
                params.append(query["category"][0])
            if "location" in query and query["location"][0] and query["location"][0] != "All":
                sql += " AND location = ?"
                params.append(query["location"][0])
            if "date" in query and query["date"][0]:
                sql += " AND event_date = ?"
                params.append(query["date"][0])
            if "date_from" in query and query["date_from"][0]:
                sql += " AND event_date >= ?"
                params.append(query["date_from"][0])
            if "date_to" in query and query["date_to"][0]:
                sql += " AND event_date <= ?"
                params.append(query["date_to"][0])
            if "organizer_id" in query and query["organizer_id"][0]:
                sql += " AND organizer_id = ?"
                params.append(query["organizer_id"][0])

            sql += " ORDER BY event_date ASC"
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            self._send_json({"events": rows})
            return

        if path.startswith("/api/events/") and path.endswith("/trust"):
            conn.close()
            result = trust.assess_event(path.split("/")[-2])
            if result is None:
                self._send_json({"error": "Event not found"}, 404)
            else:
                result["reasons"] = trust.REPORT_REASONS
                self._send_json({"trust": result})
            return

        if path == "/api/admin/events":
            conn.close()
            if self._require_admin() is not None:
                self._send_json({"events": trust.admin_overview(), "reasons": trust.REPORT_REASONS})
            return

        if path.startswith("/api/events/"):
            event_id = path.split("/")[-1]
            cur.execute(
                "SELECT events.*, users.full_name AS organizer_name FROM events "
                "LEFT JOIN users ON users.id = events.organizer_id WHERE events.id = ?",
                (event_id,),
            )
            row = cur.fetchone()
            conn.close()
            if row is None:
                self._send_json({"error": "Event not found"}, 404)
            else:
                self._send_json({"event": dict(row)})
            return

        if path == "/api/categories":
            cur.execute("SELECT DISTINCT category FROM events ORDER BY category")
            cats = [r["category"] for r in cur.fetchall()]
            conn.close()
            self._send_json({"categories": cats})
            return

        if path == "/api/locations":
            cur.execute("SELECT DISTINCT location FROM events ORDER BY location")
            locs = [r["location"] for r in cur.fetchall()]
            conn.close()
            self._send_json({"locations": locs})
            return

        if path == "/api/tickets":
            user_id = query.get("user_id", [None])[0]
            if not user_id:
                conn.close()
                self._send_json({"error": "user_id is required"}, 400)
                return
            cur.execute("""
                SELECT t.*, e.title, e.location, e.event_date, e.event_time, e.image_emoji, e.category
                FROM tickets t JOIN events e ON t.event_id = e.id
                WHERE t.user_id = ? ORDER BY t.issued_at DESC
            """, (user_id,))
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            self._send_json({"tickets": rows})
            return

        if path.startswith("/api/tickets/"):
            code = path.split("/")[-1]
            cur.execute("""
                SELECT t.*, e.title, e.location, e.event_date, e.event_time,
                       e.image_emoji, e.category, u.full_name, u.email
                FROM tickets t
                JOIN events e ON t.event_id = e.id
                JOIN users u ON t.user_id = u.id
                WHERE t.ticket_code = ?
            """, (code,))
            row = cur.fetchone()
            conn.close()
            if row is None:
                self._send_json({"error": "Ticket not found"}, 404)
            else:
                self._send_json({"ticket": dict(row)})
            return

        if path.startswith("/api/users/"):
            user_id = path.split("/")[-1]
            cur.execute("SELECT id, full_name, email, role, status, created_at FROM users WHERE id = ?", (user_id,))
            row = cur.fetchone()
            conn.close()
            if row is None:
                self._send_json({"error": "User not found"}, 404)
            else:
                self._send_json({"user": dict(row)})
            return

        if path == "/api/stats":
            conn.close()
            self._send_json({"stats": utils.get_statistics()})
            return

        if path == "/api/export/csv":
            conn.close()
            csv_path = utils.export_tickets_to_csv()
            self._send_file(csv_path)
            return

        conn.close()
        self._send_json({"error": "Unknown API route"}, 404)

    # ---------- API: POST ----------

    def handle_api_post(self, path):
        body = self._read_json_body()
        if self._is_admin_tool_route("POST", path):
            self.handle_admin("POST", path, {}, body)
            return
        conn = get_connection()
        cur = conn.cursor()

        if path == "/api/register":
            required = ["full_name", "email", "password"]
            if not all(body.get(f) for f in required):
                conn.close()
                self._send_json({"error": "full_name, email and password are required"}, 400)
                return
            # Anyone can sign up as attendee or organizer - never as admin
            role = body.get("role", "attendee")
            if role not in ("attendee", "organizer"):
                role = "attendee"
            full_name = clean_name(body["full_name"])
            if not full_name:
                conn.close()
                self._send_json({"error": "Please enter your name"}, 400)
                return
            # Every name on the site must be unique (so the organizer name
            # shown on an event always points to exactly one person)
            if name_taken(conn, full_name):
                conn.close()
                self._send_json({"error": "That name is already taken. Please use a different name."}, 400)
                return
            try:
                cur.execute(
                    "INSERT INTO users (full_name, email, password, role) VALUES (?, ?, ?, ?)",
                    (full_name, body["email"].lower().strip(), hash_password(body["password"]),
                     role),
                )
                conn.commit()
                user_id = cur.lastrowid
                conn.close()
                log_activity("register", user_id, f"signed up as {role}")
                self._send_json({"message": "Account created", "user_id": user_id}, 201)
            except Exception:
                conn.close()
                self._send_json({"error": "That email is already registered"}, 400)
            return

        if path == "/api/login":
            email = (body.get("email") or "").lower().strip()
            password = body.get("password") or ""
            cur.execute("SELECT * FROM users WHERE email = ?", (email,))
            row = cur.fetchone()
            if row is None or not verify_password(password, row["password"]):
                conn.close()
                log_activity("login_failed", None, email, target_user_id=row["id"] if row else None)
                self._send_json({"error": "Incorrect email or password"}, 401)
                return
            if row["status"] != "active":
                conn.close()
                log_activity("login_blocked", row["id"], "account is suspended")
                self._send_json({"error": "Your account has been suspended. Contact the site admin."}, 403)
                return
            cur.execute("UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?", (row["id"],))
            conn.commit()
            conn.close()
            user = dict(row)
            user.pop("password", None)
            token = secrets.token_hex(16)
            SESSION_TOKENS[token] = user["id"]
            user["token"] = token
            log_activity("login", user["id"], user["role"])
            self._send_json({"message": "Login successful", "user": user})
            return

        if path == "/api/logout":
            user = self._user_from_token(cur)
            conn.close()
            SESSION_TOKENS.pop(self.headers.get("X-Auth-Token", ""), None)
            if user:
                log_activity("logout", user["id"])
            self._send_json({"message": "Logged out"})
            return

        if path == "/api/events":
            organizer = self._auth_user(cur, roles=("organizer",))
            if organizer is None:
                conn.close()
                return
            required = ["title", "category", "location", "event_date"]
            if not all(body.get(f) for f in required):
                conn.close()
                self._send_json({"error": "Missing required event fields"}, 400)
                return

            cur.execute("""
                INSERT INTO events
                    (title, category, description, location, event_date, event_time,
                     price, capacity, organizer_id, image_emoji)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                body["title"], body["category"], body.get("description", ""),
                body["location"], body["event_date"], body.get("event_time", "TBA"),
                float(body.get("price", 0) or 0), int(body.get("capacity", 100) or 100),
                organizer["id"], body.get("image_emoji", "🎉"),
            ))
            conn.commit()
            event_id = cur.lastrowid
            conn.close()
            log_activity("event_create", organizer["id"], body["title"])
            self._send_json({"message": "Event created", "event_id": event_id}, 201)
            return

        if path == "/api/tickets":
            buyer = self._auth_user(cur)
            if buyer is None:
                conn.close()
                return
            event_id = body.get("event_id")
            user_id = buyer["id"]          # always the logged-in user, never a value from the request
            if not event_id:
                conn.close()
                self._send_json({"error": "event_id is required"}, 400)
                return

            # check capacity before issuing a ticket
            cur.execute("SELECT title, capacity, verification_status FROM events WHERE id = ?", (event_id,))
            event_row = cur.fetchone()
            if event_row is None:
                conn.close()
                self._send_json({"error": "Event not found"}, 404)
                return
            if event_row["verification_status"] == "flagged":
                conn.close()
                self._send_json({"error": "This event was flagged as a suspected scam and cannot be booked"}, 403)
                return
            cur.execute("SELECT COUNT(*) AS c FROM tickets WHERE event_id = ? AND status != 'cancelled'", (event_id,))
            sold = cur.fetchone()["c"]
            if sold >= event_row["capacity"]:
                conn.close()
                self._send_json({"error": "This event is sold out"}, 400)
                return

            code = utils.generate_ticket_code()
            cur.execute(
                "INSERT INTO tickets (ticket_code, event_id, user_id) VALUES (?, ?, ?)",
                (code, event_id, user_id),
            )
            conn.commit()
            conn.close()
            log_activity("ticket_book", user_id, f"{event_row['title']} ({code})")
            self._send_json({"message": "Ticket booked", "ticket_code": code}, 201)
            return

        if path == "/api/tickets/verify":
            # Only a logged-in organizer may check tickets in, and only
            # for events they own. Attendees are refused here even if they
            # call the API directly, not just hidden in the page.
            verifier = self._user_from_token(cur)
            if verifier is not None and verifier["status"] != "active":
                verifier = None
            if verifier is None or verifier["role"] != "organizer":
                conn.close()
                self._send_json({"error": "Only organizer accounts can verify tickets. "
                                          "If you are an organizer, please log in again.",
                                 "valid": False}, 403)
                return

            code = body.get("ticket_code", "")
            cur.execute("""
                SELECT t.*, e.organizer_id FROM tickets t
                JOIN events e ON t.event_id = e.id WHERE t.ticket_code = ?
            """, (code,))
            row = cur.fetchone()
            if row is None:
                conn.close()
                self._send_json({"error": "Ticket code not recognised", "valid": False}, 404)
                return
            if row["organizer_id"] != verifier["id"]:
                conn.close()
                self._send_json({"error": "You can only verify tickets for your own events",
                                 "valid": False}, 403)
                return
            if row["status"] == "used":
                conn.close()
                self._send_json({"message": "Ticket already used", "valid": False})
                return
            if row["status"] == "cancelled":
                conn.close()
                self._send_json({"message": "Ticket was cancelled", "valid": False})
                return
            cur.execute("UPDATE tickets SET status = 'used' WHERE ticket_code = ?", (code,))
            conn.commit()
            conn.close()
            log_activity("ticket_verify", verifier["id"], code, target_user_id=row["user_id"])
            self._send_json({"message": "Ticket verified - welcome!", "valid": True})
            return

        if path.startswith("/api/events/") and path.endswith("/report"):
            reporter = self._auth_user(cur)
            conn.close()
            if reporter is None:
                return
            event_id = path.split("/")[-2]
            ok, message, status = trust.create_report(
                event_id, reporter["id"], body.get("reason"), body.get("details", ""))
            if ok:
                log_activity("event_report", reporter["id"], f"event #{event_id}: {body.get('reason')}")
            self._send_json({"message": message} if ok else {"error": message}, status)
            return

        if path.startswith("/api/admin/events/") and path.endswith("/status"):
            conn.close()
            if self._require_admin() is None:
                return
            ok, message = trust.set_status(path.split("/")[-2], body.get("status"))
            if ok:
                log_activity("admin_event_status", self._require_admin(),
                             f"event #{path.split('/')[-2]} -> {body.get('status')}")
            self._send_json({"message": message} if ok else {"error": message}, 200 if ok else 400)
            return

        conn.close()
        self._send_json({"error": "Unknown API route"}, 404)

    # ---------- API: PUT ----------

    def handle_api_put(self, path):
        body = self._read_json_body()
        conn = get_connection()
        cur = conn.cursor()

        if path.startswith("/api/events/"):
            actor = self._auth_user(cur, roles=("organizer", "admin"))
            if actor is None:
                conn.close()
                return
            event_id = path.split("/")[-1]
            cur.execute("SELECT * FROM events WHERE id = ?", (event_id,))
            existing = cur.fetchone()
            if existing is None:
                conn.close()
                self._send_json({"error": "Event not found"}, 404)
                return
            if actor["role"] != "admin" and existing["organizer_id"] != actor["id"]:
                conn.close()
                self._send_json({"error": "You can only edit your own events"}, 403)
                return

            fields = ["title", "category", "description", "location", "event_date",
                      "event_time", "price", "capacity", "image_emoji"]
            updates = {f: body[f] for f in fields if f in body}
            if not updates:
                conn.close()
                self._send_json({"error": "Nothing to update"}, 400)
                return

            # Changing a verified event means the admin checked something
            # different, so it goes back to "unverified" for a fresh review.
            if existing["verification_status"] == "verified":
                updates["verification_status"] = "unverified"

            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [event_id]
            cur.execute(f"UPDATE events SET {set_clause} WHERE id = ?", values)
            conn.commit()
            conn.close()
            log_activity("event_update", actor["id"], existing["title"], target_user_id=existing["organizer_id"])
            self._send_json({"message": "Event updated"})
            return

        if path.startswith("/api/users/"):
            actor = self._auth_user(cur)
            if actor is None:
                conn.close()
                return
            user_id = path.split("/")[-1]
            if str(actor["id"]) != user_id:
                conn.close()
                self._send_json({"error": "You can only edit your own profile"}, 403)
                return
            fields = ["full_name", "email"]
            updates = {f: str(body[f]).strip() for f in fields if body.get(f)}
            if "email" in updates:
                updates["email"] = updates["email"].lower()
            if "full_name" in updates:
                updates["full_name"] = clean_name(updates["full_name"])
                if not updates["full_name"]:
                    conn.close()
                    self._send_json({"error": "Please enter your name"}, 400)
                    return
                if name_taken(conn, updates["full_name"], exclude_id=actor["id"]):
                    conn.close()
                    self._send_json({"error": "That name is already taken. Please use a different name."}, 400)
                    return
            if not updates:
                conn.close()
                self._send_json({"error": "Nothing to update"}, 400)
                return
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [user_id]
            try:
                cur.execute(f"UPDATE users SET {set_clause} WHERE id = ?", values)
                conn.commit()
            except sqlite3.IntegrityError:
                conn.close()
                self._send_json({"error": "That email or name is already registered"}, 400)
                return
            conn.close()
            log_activity("profile_update", actor["id"], ", ".join(updates))
            self._send_json({"message": "Profile updated"})
            return

        conn.close()
        self._send_json({"error": "Unknown API route"}, 404)

    # ---------- API: DELETE ----------

    def handle_api_delete(self, path):
        if self._is_admin_tool_route("DELETE", path):
            self.handle_admin("DELETE", path, {}, {})
            return
        conn = get_connection()
        cur = conn.cursor()

        if path.startswith("/api/events/"):
            actor = self._auth_user(cur, roles=("organizer", "admin"))
            if actor is None:
                conn.close()
                return
            event_id = path.split("/")[-1]
            cur.execute("SELECT title, organizer_id FROM events WHERE id = ?", (event_id,))
            existing = cur.fetchone()
            if existing is None:
                conn.close()
                self._send_json({"error": "Event not found"}, 404)
                return
            if actor["role"] != "admin" and existing["organizer_id"] != actor["id"]:
                conn.close()
                self._send_json({"error": "You can only delete your own events"}, 403)
                return
            cur.execute("DELETE FROM tickets WHERE event_id = ?", (event_id,))
            cur.execute("DELETE FROM event_reports WHERE event_id = ?", (event_id,))
            cur.execute("DELETE FROM events WHERE id = ?", (event_id,))
            conn.commit()
            conn.close()
            log_activity("event_delete", actor["id"], existing["title"], target_user_id=existing["organizer_id"])
            self._send_json({"message": "Event deleted"})
            return

        conn.close()
        self._send_json({"error": "Unknown API route"}, 404)


def main():
    parser = argparse.ArgumentParser(description="Run the Naija Events backend server")
    parser.add_argument("--port", type=int, default=8000, help="Port to run the server on (default: 8000)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host to bind to (default: 127.0.0.1)")
    args = parser.parse_args()

    print("Setting up the database...")
    init_db()
    seed_if_empty()
    demo_admin = ensure_admin_user()
    if demo_admin:
        print(f"NOTE: created the starting admin {demo_admin} with the demo password. "
              "Log in and change it from Admin > Users, or set NAIJA_ADMIN_EMAIL / "
              "NAIJA_ADMIN_PASSWORD before the first run.")

    server = ThreadingHTTPServer((args.host, args.port), NaijaEventsHandler)
    print(f"Naija Events server running at http://{args.host}:{args.port}")
    print("Press CTRL+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        server.server_close()


if __name__ == "__main__":
    main()
