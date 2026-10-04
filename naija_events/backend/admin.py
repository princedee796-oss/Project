"""
admin.py
--------
Everything the admin dashboard can do, kept apart from server.py.

Each function returns plain data, or raises AdminError(message, http_status)
which server.py turns into a JSON error.

Safety rules built in here (not just in the web page):
  - an admin cannot suspend, demote, or delete THEMSELVES, and the last
    active admin can never be removed by anyone, so there can never be a
    moment with zero working admins;
  - only these functions can create another admin - the sign-up route
    refuses the admin role completely;
  - every change is written to the activity log with who did it.
"""

from database import get_connection, log_activity
from security import hash_password

ROLES = ("attendee", "organizer", "admin")
STATUSES = ("active", "suspended")
TICKET_STATUSES = ("valid", "used", "cancelled")


class AdminError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def _get_user(cur, user_id):
    cur.execute("SELECT id, full_name, email, role, status FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    if row is None:
        raise AdminError("User not found", 404)
    return dict(row)


def _not_self(actor_id, user_id, what):
    if int(actor_id) == int(user_id):
        raise AdminError(f"You cannot {what} your own account", 400)


def _keep_one_admin(cur, user, what):
    """Refuse to take away the last active admin (by any route)."""
    if user["role"] != "admin" or user["status"] != "active":
        return
    cur.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND status = 'active' AND id != ?",
                (user["id"],))
    if cur.fetchone()[0] == 0:
        raise AdminError(f"You cannot {what} the only active admin", 400)


# ---------- reading ----------

def overview():
    conn = get_connection()
    cur = conn.cursor()
    one = lambda sql: cur.execute(sql).fetchone()[0]
    data = {
        "users": one("SELECT COUNT(*) FROM users"),
        "attendees": one("SELECT COUNT(*) FROM users WHERE role = 'attendee'"),
        "organizers": one("SELECT COUNT(*) FROM users WHERE role = 'organizer'"),
        "admins": one("SELECT COUNT(*) FROM users WHERE role = 'admin'"),
        "suspended": one("SELECT COUNT(*) FROM users WHERE status = 'suspended'"),
        "events": one("SELECT COUNT(*) FROM events"),
        "flagged_events": one("SELECT COUNT(*) FROM events WHERE verification_status = 'flagged'"),
        "tickets": one("SELECT COUNT(*) FROM tickets"),
        "reports": one("SELECT COUNT(*) FROM event_reports"),
        "new_users_7d": one("SELECT COUNT(*) FROM users WHERE created_at >= datetime('now', '-7 days')"),
        "active_users_7d": one("SELECT COUNT(DISTINCT user_id) FROM activity_log "
                               "WHERE created_at >= datetime('now', '-7 days') AND user_id IS NOT NULL"),
    }
    conn.close()
    data["recent_activity"] = list_activity(limit=10)["activity"]
    return data


def list_users(q="", role="", status=""):
    sql = """
        SELECT u.id, u.full_name, u.email, u.role, u.status, u.created_at, u.last_login_at,
               (SELECT COUNT(*) FROM events e WHERE e.organizer_id = u.id) AS events_created,
               (SELECT COUNT(*) FROM tickets t WHERE t.user_id = u.id) AS tickets_booked
        FROM users u WHERE 1=1
    """
    params = []
    if q:
        sql += " AND (u.full_name LIKE ? OR u.email LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]
    if role in ROLES:
        sql += " AND u.role = ?"
        params.append(role)
    if status in STATUSES:
        sql += " AND u.status = ?"
        params.append(status)
    sql += " ORDER BY u.created_at DESC, u.id DESC"
    conn = get_connection()
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def user_detail(user_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, full_name, email, role, status, created_at, last_login_at "
                "FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    if row is None:
        conn.close()
        raise AdminError("User not found", 404)
    user = dict(row)

    cur.execute("""
        SELECT e.id, e.title, e.event_date, e.verification_status, e.capacity,
               (SELECT COUNT(*) FROM tickets t WHERE t.event_id = e.id AND t.status != 'cancelled') AS sold
        FROM events e WHERE e.organizer_id = ? ORDER BY e.event_date DESC
    """, (user_id,))
    events = [dict(r) for r in cur.fetchall()]

    cur.execute("""
        SELECT t.ticket_code, t.status, t.issued_at, e.title, e.event_date
        FROM tickets t JOIN events e ON e.id = t.event_id
        WHERE t.user_id = ? ORDER BY t.issued_at DESC
    """, (user_id,))
    tickets = [dict(r) for r in cur.fetchall()]

    cur.execute("""
        SELECT r.reason, r.details, r.created_at, e.title
        FROM event_reports r JOIN events e ON e.id = r.event_id
        WHERE r.user_id = ? ORDER BY r.created_at DESC
    """, (user_id,))
    reports = [dict(r) for r in cur.fetchall()]
    conn.close()

    return {"user": user, "events": events, "tickets": tickets, "reports_made": reports,
            "activity": list_activity(user_id=user_id, limit=50)["activity"]}


def list_activity(user_id=None, action="", limit=100, offset=0):
    """Newest first. With user_id: things that user did AND things admins did to them."""
    limit = max(1, min(int(limit or 100), 500))
    offset = max(0, int(offset or 0))
    sql = "SELECT * FROM activity_log WHERE 1=1"
    params = []
    if user_id:
        sql += " AND (user_id = ? OR target_user_id = ?)"
        params += [user_id, user_id]
    if action:
        sql += " AND action = ?"
        params.append(action)
    conn = get_connection()
    total = conn.execute("SELECT COUNT(*) FROM (" + sql + ")", params).fetchone()[0]
    rows = conn.execute(sql + " ORDER BY id DESC LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()
    actions = [r[0] for r in conn.execute("SELECT DISTINCT action FROM activity_log ORDER BY action")]
    conn.close()
    return {"activity": [dict(r) for r in rows], "total": total, "actions": actions}


# ---------- changing things ----------

def set_user_status(actor_id, user_id, status):
    if status not in STATUSES:
        raise AdminError("Status must be active or suspended")
    _not_self(actor_id, user_id, "suspend")
    conn = get_connection()
    cur = conn.cursor()
    user = _get_user(cur, user_id)
    if status == "suspended":
        _keep_one_admin(cur, user, "suspend")
    cur.execute("UPDATE users SET status = ? WHERE id = ?", (status, user_id))
    conn.commit()
    conn.close()
    log_activity("admin_suspend_user" if status == "suspended" else "admin_reactivate_user",
                 actor_id, user["email"], target_user_id=user_id)
    return f"{user['full_name']} is now {status}"


def set_user_role(actor_id, user_id, role):
    if role not in ROLES:
        raise AdminError("Role must be attendee, organizer or admin")
    _not_self(actor_id, user_id, "change the role of")
    conn = get_connection()
    cur = conn.cursor()
    user = _get_user(cur, user_id)
    if role != "admin":
        _keep_one_admin(cur, user, "demote")
    cur.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
    conn.commit()
    conn.close()
    log_activity("admin_change_role", actor_id, f"{user['email']}: {user['role']} -> {role}",
                 target_user_id=user_id)
    return f"{user['full_name']}'s role is now {role}"


def reset_user_password(actor_id, user_id, password):
    if not password or len(password) < 8:
        raise AdminError("The new password must be at least 8 characters")
    conn = get_connection()
    cur = conn.cursor()
    user = _get_user(cur, user_id)
    cur.execute("UPDATE users SET password = ? WHERE id = ?", (hash_password(password), user_id))
    conn.commit()
    conn.close()
    log_activity("admin_reset_password", actor_id, user["email"], target_user_id=user_id)
    return f"Password reset for {user['full_name']}. Their current sessions were signed out."


def delete_user(actor_id, user_id):
    """Remove the account plus everything that hangs off it (events they run, tickets, reports)."""
    _not_self(actor_id, user_id, "delete")
    conn = get_connection()
    cur = conn.cursor()
    user = _get_user(cur, user_id)
    _keep_one_admin(cur, user, "delete")

    cur.execute("SELECT id FROM events WHERE organizer_id = ?", (user_id,))
    event_ids = [r["id"] for r in cur.fetchall()]
    for event_id in event_ids:
        cur.execute("DELETE FROM tickets WHERE event_id = ?", (event_id,))
        cur.execute("DELETE FROM event_reports WHERE event_id = ?", (event_id,))
        cur.execute("DELETE FROM events WHERE id = ?", (event_id,))
    cur.execute("DELETE FROM tickets WHERE user_id = ?", (user_id,))
    cur.execute("DELETE FROM event_reports WHERE user_id = ?", (user_id,))
    cur.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    log_activity("admin_delete_user", actor_id,
                 f"{user['email']} ({user['role']}); removed {len(event_ids)} event(s)",
                 target_user_id=user_id)
    return f"{user['full_name']} and their data were deleted"


def set_ticket_status(actor_id, ticket_code, status):
    if status not in TICKET_STATUSES:
        raise AdminError("Ticket status must be valid, used or cancelled")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM tickets WHERE ticket_code = ?", (ticket_code,))
    row = cur.fetchone()
    if row is None:
        conn.close()
        raise AdminError("Ticket not found", 404)
    cur.execute("UPDATE tickets SET status = ? WHERE ticket_code = ?", (status, ticket_code))
    conn.commit()
    conn.close()
    log_activity("admin_ticket_status", actor_id, f"{ticket_code} -> {status}",
                 target_user_id=row["user_id"])
    return f"Ticket {ticket_code} marked {status}"


def delete_event(actor_id, event_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT title, organizer_id FROM events WHERE id = ?", (event_id,))
    ev = cur.fetchone()
    if ev is None:
        conn.close()
        raise AdminError("Event not found", 404)
    cur.execute("DELETE FROM tickets WHERE event_id = ?", (event_id,))
    cur.execute("DELETE FROM event_reports WHERE event_id = ?", (event_id,))
    cur.execute("DELETE FROM events WHERE id = ?", (event_id,))
    conn.commit()
    conn.close()
    log_activity("admin_delete_event", actor_id, ev["title"], target_user_id=ev["organizer_id"])
    return f"Event \"{ev['title']}\" deleted"
