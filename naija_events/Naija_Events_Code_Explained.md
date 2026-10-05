# Naija Events: Code Explained Section by Section

## 1. Big picture

The browser (HTML pages) talks to a Python server using `fetch()`. The server reads and writes a SQLite database and replies with JSON. There is no Flask or Django; only Python's standard library.

```
Browser (HTML/CSS/JS)  --fetch /api/...-->  server.py  -->  SQLite (events.db)
                       <----- JSON -------
```

Run it with `python server.py` (from `backend/`), then open http://127.0.0.1:8000.

---

## 2. `backend/security.py` (password hashing)

- `hash_password(password, salt=None)`: if no salt is given, it makes a random one with `uuid.uuid4().hex`. It joins salt + password, hashes with SHA-256, and returns `"salt$hash"`. The salt is stored with the hash so it can be reused at login.
- `verify_password(password, stored_value)`: splits the stored value at `$`, re-hashes the typed password with the same salt, and compares. Returns `False` if the stored value is empty or malformed.
- **Why a salt?** Two users with the same password get different hashes, and pre-computed "rainbow tables" no longer work.
- It lives in its own file to avoid a circular import (`utils` imports `database`, and `database` needs hashing).

## 3. `backend/database.py` (SQLite)

- `DB_PATH`: builds the path to `data/events.db` relative to this file, so it works from any folder.
- `get_connection()`: opens SQLite, turns on foreign keys (`PRAGMA foreign_keys = ON`, off by default in SQLite), and sets `row_factory = sqlite3.Row` so you can write `row["title"]` instead of `row[1]`.
- `init_db()`: runs `CREATE TABLE IF NOT EXISTS` for five tables:
  - `users`: id, full_name (unique, see section 16), unique email, hashed password, role (`attendee`, `organizer` or `admin`), status (`active` or `suspended`), last_login_at, created_at.
  - `events`: title, category, description, location, event_date (YYYY-MM-DD), event_time, price, capacity, organizer_id, image_emoji. `organizer_id` is a foreign key to `users`.
  - `tickets`: unique ticket_code, event_id, user_id, status (`valid`, `used`, `cancelled`), issued_at. Two foreign keys.
  - `event_reports`: event_id, user_id, reason, details, with `UNIQUE (event_id, user_id)` so a user can report an event only once (see section 12).
  - `activity_log`: who did what and when (see section 15).
- `clean_name(name)` and `name_taken(conn, name, exclude_id=None)`: the helpers that keep every user name unique (see section 16).
- At the end of `init_db()`, a **unique index** on `lower(trim(full_name))` is created as a backstop for the same rule.
- `seed_if_empty()`: counts events; if zero, loads `sample_events.json`, creates the demo organizer (`organizer@naijaevents.ng` / `demo1234`, password hashed), and inserts every sample event. `INSERT OR IGNORE` avoids a crash if that user already exists. The `?` placeholders are **parameterized queries**, which prevent SQL injection.

## 4. `backend/utils.py` (helper functions)

- `generate_ticket_code()`: `NGE-` + 5 random hex characters (from uuid4, uppercased) + 4 random digits, e.g. `NGE-7F3K9-2481`.
- `export_tickets_to_csv()`: creates `exports/` if missing, names the file with a timestamp, runs a SQL query that **JOINs** tickets with events and users, and writes rows using Python's `csv.writer`. Returns the file path.
- `import_events_from_json()`: reads a JSON file and inserts each event for a given organizer; returns the count.
- `get_statistics()`: runs several queries and returns one dictionary:
  - totals of events, tickets, users;
  - top 5 events by tickets sold (`LEFT JOIN` + `GROUP BY` + `ORDER BY ... LIMIT 5`; LEFT JOIN keeps events with zero sales);
  - events per category;
  - revenue = sum of event prices for tickets that are not cancelled.

## 5. `backend/server.py` (the whole web server)

**Imports and setup**
- `sys.path.insert(...)` makes `import database` work wherever you run the file from.
- `FRONTEND_DIR` and `CHARTS_DIR` are absolute folder paths. `MIME_TYPES` maps file extensions to content types so the browser knows how to treat each file.

**`NaijaEventsHandler(BaseHTTPRequestHandler)`**: one instance handles each request.

*Helpers*
- `_send_json(data, status)`: turns a dict into JSON bytes, sends the status code, headers (`Content-Type`, `Content-Length`), then the body.
- `_send_file(path)`: reads a file in binary, picks the MIME type, or returns a 404 JSON error.
- `_read_json_body()`: reads `Content-Length` bytes from the request and parses them as JSON; returns `{}` if empty or invalid.
- `log_message()`: overrides the default log line for tidier console output.

*Routing*
- `do_GET`, `do_POST`, `do_PUT`, `do_DELETE`: Python calls the matching method automatically for each HTTP verb. If the path starts with `/api/` it goes to an API handler; otherwise GET serves a static file. Each is wrapped in `try/except` so any crash returns a JSON 500 error instead of killing the request.

*Static files: `handle_static`*
- `/` becomes `/index.html`. It joins the path onto `frontend/` and calls `os.path.normpath`. The check `startswith(FRONTEND_DIR)` blocks **path traversal** (`../../secret`). Paths starting with `/charts/` are served from the charts folder with the same safety check. Directories fall back to `index.html`.

*GET API: `handle_api_get`*
- `/api/events`: starts with `SELECT events.*, users.full_name AS organizer_name FROM events LEFT JOIN users ... WHERE 1=1` (the JOIN adds the organizer's name, see section 16) and appends `AND ...` for each filter present (`q` title search with `LIKE`, category, location, exact date, date range, organizer). Values go into a `params` list, never into the SQL string. Sorted by date.
- `/api/events/{id}`: single event (with `organizer_name`) or 404.
- `/api/categories`, `/api/locations`: `SELECT DISTINCT` lists for filter dropdowns.
- `/api/tickets?user_id=`: a user's tickets joined with event info; 400 if `user_id` is missing.
- `/api/tickets/{code}`: ticket with event and holder details.
- `/api/users/{id}`: profile without the password column.
- `/api/stats`, `/api/export/csv`: call the helpers in `utils`.

*POST API: `handle_api_post`*
- `/api/register`: requires name, email, password; cleans the name with `clean_name`; refuses with 400 if `name_taken` says someone already has that name (section 16); lowercases the email; hashes the password; inserts. If the insert fails (duplicate email violates `UNIQUE`), returns "already registered". Returns 201 on success.
- `/api/login`: looks up by email and uses `verify_password`. Same error for wrong email or wrong password, so attackers cannot tell which was wrong. Removes the `password` key before replying.
- `/api/events`: **checks the login token and that the account is an active organizer** (the organizer is always the logged-in user, never an id sent by the browser), validates required fields, then inserts, converting price to `float` and capacity to `int`.
- `/api/tickets`: needs a login; the ticket always goes to the logged-in user. Checks the event exists, counts non-cancelled tickets, refuses if sold out, then creates a code and inserts.
- `/api/tickets/verify`: the door check-in, **organizers only** (see section 11). Unknown code gives 404; `used` or `cancelled` gives `valid: False`; otherwise the status is changed to `used` so the same ticket cannot enter twice.

*PUT and DELETE*
- PUT events/users: builds the `SET` clause only from allowed field names (a whitelist), so a client cannot update columns like `organizer_id` or `role`. Values still use `?`. Editing an event needs its organizer (or an admin); editing a profile needs that profile's owner. When a profile's `full_name` changes, it is cleaned and checked with `name_taken(..., exclude_id=<own id>)` so a name already used by someone else is refused, but a person can keep their own name (section 16).
- DELETE event: same permission check, then deletes its tickets and reports first, then the event (foreign keys would otherwise block it).

**`main()`**: `argparse` reads `--port` and `--host`, then `init_db()`, `seed_if_empty()`, and `ThreadingHTTPServer(...).serve_forever()`. "Threading" means each request runs in its own thread. `KeyboardInterrupt` (Ctrl+C) shuts down cleanly.

## 6. `backend/report.py` (command line reporting tool)

- Uses `matplotlib.use("Agg")` so charts draw without a screen. Three brand colours are defined as constants.
- `draw_tickets_by_event_chart`: bar chart of the top 5 events (names cut to 18 characters).
- `draw_events_by_category_chart`: pie chart with percentages.
- `draw_overview_chart`: horizontal bars of events / tickets / users with the value printed next to each bar.
- Each chart function returns early if there is no data, saves a PNG into `charts/`, and calls `plt.close()` to free memory.
- `build_charts()`, `print_summary()`, `save_report()`: get stats, then draw, print, or write a timestamped `.txt` file (`"\n".join(lines)`).
- `main()`: `argparse` with four `store_true` flags. With no flags it prints help. Flags can be combined.

## 7. `frontend/js/app.js` (shared browser code)

- `apiGet`, `apiSend` (`apiPost`, `apiPut`), `apiDelete`: wrappers around `fetch`. They parse JSON and throw an `Error` with the server's message if the response is not OK, so pages can `try/catch` and show it.
- `getCurrentUser`, `setCurrentUser`, `logoutUser`: store the logged-in user in the browser's `localStorage` (simple, but not a real secure session; mention this as a limitation).
- `resolvePath`: picks the right relative link whether you are on the home page or inside `/pages/`.
- `paintNavAccount`: fills the navigation bar with the user's first name and Log out, or Log in / Sign up.
- `paintOrganizerNav`: hides Create Event and Dashboard links for attendees.
- `showMessage`: shows a success or error box. `formatNaira`: shows "Free" for 0, else the naira sign with commas. `formatDate`: turns `2026-10-05` into a readable date.
- **Category theming**: `categoryTheme` computes a simple hash of the category name and uses it to pick one of eight colour/pattern themes, so the same category always looks the same. `applyCategoryTheme` sets CSS variables and classes on an element.
- Two `DOMContentLoaded` listeners run the nav functions on every page.

## 8. Frontend pages (`frontend/pages/*.html`)

Each page has its HTML layout plus a small script using the helpers above.

- **login.html**: sends email and password to `/api/login`, saves the returned user, and redirects (to `?next=` if present, otherwise My Tickets). Errors show in the message box.
- **create-event.html**: reads `?id=` from the URL. If present it is **edit mode**: loads the event and fills the form, and submits with PUT. Otherwise it submits with POST (the server takes the organizer from the login token). Non-organizers see a message instead of the form.
- **dashboard.html**: organizer only. `loadStats()` builds the four stat cards; `loadMyEvents()` fills the table for that organizer, with Edit and Delete buttons (delete asks `confirm()` first, then refreshes).
- **ticket-details.html**: reads `?code=`, fetches the ticket and shows its code, holder and status. The Verify box is hidden unless the logged-in user is an organizer; when used it calls `/api/tickets/verify` and reloads the ticket on success.
- **event-details.html**: after the "About this event" description it prints an **Organizer:** line using `event.organizer_name` (name only, shown through `escapeHtml`). If the name is missing it shows "Unknown".
- The other pages (explore, search, filter, my-tickets, register, profile) follow the same pattern: read the URL or form values, call the API, and render the JSON into HTML.

## 9. Data flow example: booking a ticket

1. User clicks Book on an event page. `apiPost("/api/tickets", {event_id})` with the `X-Auth-Token` header.
2. `do_POST` routes to `handle_api_post`.
3. Server checks the event exists and is not sold out.
4. `generate_ticket_code()` makes a code; the row is inserted with status `valid`.
5. JSON `{ticket_code}` returns (201). The page shows it.
6. On the ticket page the code, holder and status are shown.
7. At the door, an organizer verifies the code: status changes to `used`.

## 10. Likely questions

- **Why no Flask?** To show how HTTP, routing and JSON work underneath a framework.
- **Is it secure?** Passwords are salted and hashed and queries are parameterized. Limitations: plain SHA-256 is fast (bcrypt or argon2 is better), login state is in `localStorage`, and login tokens never expire and live in server memory, so real expiring sessions would be the next step. (Booking, reporting and event changes now use the logged-in token rather than a user id sent by the browser.)
- **Two people booking the last ticket at once?** The capacity check and insert are separate steps, so a rare race is possible; a transaction or unique constraint would fix it.
- **Why must names be unique?** The event page shows only the organizer's name, so if two people could share a name, visitors could not tell which organizer they are looking at (see section 16).
- **How would you scale it?** Move to a framework and a server database such as PostgreSQL, add payment integration, add tokens.

---

## 11. Organizer-only ticket verification

**Problem:** hiding a button is not security. Anyone can send a request to the API directly, so the server must check who is asking.

**How it works**
1. At login (`/api/login`), every account gets a random token from `secrets.token_hex(16)`. It is stored in the in-memory dictionary `SESSION_TOKENS` as `{token: user_id}` and also returned to the browser, which saves it with the user in `localStorage`.
2. `app.js` (`authHeaders`) adds it to every request as the header `X-Auth-Token`.
3. `_user_from_token(cur)` in `server.py` reads that header, finds the user id, and looks up the role in the database.
4. `/api/tickets/verify` then applies three checks in order:
   - not logged in, or not an organizer: **403**;
   - ticket code unknown: **404**;
   - the ticket's event belongs to a different organizer: **403** ("You can only verify tickets for your own events").
5. `ticket-details.html` shows the Verify box only when `getCurrentUser().role === "organizer"`.

**Important detail:** `_require_admin` now also checks `role == "admin"` in the database. Without that, an organizer's token would have been accepted on admin routes.

**Limits:** tokens are in memory, so restarting the server logs everyone out (they log in again). Tokens never expire otherwise.

## 12. The "is this event legit?" feature (`backend/trust.py`)

Three layers work together:

1. **Automatic check, `assess_event(event_id)`.** Starts at a base of 60 and adds or subtracts points for signals, then clamps to 0-100. Good signs: organizer account older than 30 days, guests already checked in at their events, other verified events, a detailed description. Warning signs: very short description, scam phrases (`SCAM_PHRASES`, e.g. "double your money"), a 10-digit number or "transfer to" (bank details outside the platform), price over 5x the category average, huge capacity, a brand-new organizer account, another flagged event, and user reports. Result levels: `verified`, `ok` (60+), `caution` (35+), `danger`, or `scam`.
2. **Community reports, `create_report`.** A logged-in user picks a reason from `REPORT_REASONS` and may add details. One report per user per event (a `UNIQUE (event_id, user_id)` constraint in `event_reports`), and organizers cannot report their own events.
3. **Admin review, `set_status` and `admin_overview`.** An admin opens `admin.html` (the "Event moderation" tab, see section 15), sees events sorted by report count, and marks each Verified, Flagged, or Unverified. An admin decision always overrides the automatic score. Flagged events cannot be booked (the server returns 403). Editing a verified event resets it to unverified for a fresh review.

**Related code**
- `database.py`: `verification_status` column on `events`, the `event_reports` table, and an `ALTER TABLE` migration so older databases upgrade automatically; `ensure_admin_user()` creates the starting admin (see section 15).
- `server.py`: `GET /api/events/{id}/trust`, `POST /api/events/{id}/report`, `GET /api/admin/events`, `POST /api/admin/events/{id}/status`. Registration only accepts `attendee` or `organizer`, so nobody can sign up as admin.
- `event-details.html`: shows the trust banner, score meter, signal list and report form, and disables the booking button if the event is flagged.
- `app.js`: `escapeHtml` (prevents user-written text from injecting HTML) and `trustBadge` for event cards.

**Be honest when presenting:** the score is a guide based on warning signs, not proof. "Looks OK" means no warning signs were found, not that the event is guaranteed genuine.

Demo accounts: admin `admin@naijaevents.ng` / `admin1234`; organizer `organizer@naijaevents.ng` / `demo1234`.

## 13. Login and sign-up pages

The picture panel was removed. Each page is now a single centered form card (`.auth-grid` in `style.css`).

## 14. Removed: QR codes

The QR feature has been taken out of the project:

- `backend/qr_encoder.py` is deleted.
- `generate_qr_png` and its `import qr_encoder` are gone from `utils.py`.
- The `/api/tickets/{code}/qr` route is gone from `server.py`.
- `ticket-details.html` no longer shows the "Scan with a phone camera" block, and the `.qr-image` rule is gone from `style.css`.

A ticket is now identified by its code alone (for example `NGE-7F3K9-2481`), which the organizer types or pastes into the Verify box.

## 15. Admin dashboard: managing organizer and attendee activity

**What it is:** a page (`frontend/pages/admin.html`, the **Admin** nav link, admins only) where an admin can see and control everything organizers and attendees do on the site. The rules about *what an admin may do* live in `backend/admin.py`; `server.py` only checks the token and routes the request.

### 15.1 Who is an admin

- **Exactly one admin at first.** `ensure_admin_user()` (in `database.py`) runs at server start. It does nothing if any admin already exists. If none exists it calls `create_admin()` with `NAIJA_ADMIN_EMAIL` / `NAIJA_ADMIN_PASSWORD` from the environment, or falls back to the demo admin `admin@naijaevents.ng` / `admin1234` and prints a reminder to change that password.
- `create_admin.py` is a command line alternative (`python create_admin.py --email you@example.com`). It asks for the password with a hidden prompt (`getpass`) and refuses to run if an admin already exists.
- **No admin choice on sign-up.** `register.html` only offers Attendee and Organizer, and `/api/register` also turns any other role (including `"admin"`) into `attendee`, because hiding a dropdown is not security.
- **Only an admin can make another admin.** The one way is the account-type dropdown on the Users tab, which calls `POST /api/admin/users/{id}/role`.

### 15.2 New database pieces (`database.py`)

- `users.status` (`active` or `suspended`) and `users.last_login_at`. Added to old databases with `ALTER TABLE` in `init_db()`, the same migration trick as `verification_status`.
- `activity_log` table: `user_id` (who did it, empty for a failed login with an unknown email), `action` (e.g. `login`, `ticket_book`), `details`, `target_user_id` (who it was done *to*, used for admin actions and failed logins), `created_at`.
- `log_activity(action, user_id, details, target_user_id)`: writes one row. It swallows database errors so a logging problem can never break the real request.
- `create_admin(...)`: validates the email and an 8+ character password, refuses if an admin exists, refuses a new admin whose name is already taken (section 16), and returns `(ok, message)`.

### 15.3 What gets logged

Sign-up, login, failed login, blocked login (suspended), logout, event create / edit / delete, ticket booked, ticket checked in, event reported, profile edit, and every admin action (suspend, reactivate, role change, password reset, delete user, ticket change, delete event, moderation decision). These calls sit next to the code that does the action in `server.py` and `admin.py`.

### 15.4 `backend/admin.py` (the admin rules)

- `AdminError(message, status)`: raised for rule violations; `server.py` catches it in every `do_*` method and sends a JSON error with that status.
- **Reading:** `overview()` (counts and the 10 latest activities), `list_users(q, role, status)` (search plus filters, with events-created and tickets-booked counts from subqueries), `user_detail(id)` (the person's events, tickets, reports and activity), `list_activity(...)` (newest first, filter by user or action, `limit`/`offset` paging, includes things admins did *to* that user).
- **Changing:**
  - `set_user_status`: suspend or reactivate.
  - `set_user_role`: change account type.
  - `reset_user_password`: needs 8+ characters, stores a new salted hash.
  - `delete_user`: removes the account plus their events, those events' tickets and reports, their own tickets and reports.
  - `set_ticket_status`: valid, used or cancelled.
  - `delete_event`.
- **Safety rules:**
  - `_not_self`: an admin cannot suspend, demote or delete their own account.
  - `_keep_one_admin`: nobody can suspend, demote or delete the **last active admin**. This covers the case where two admins act on each other, which `_not_self` alone would allow.
  - All queries use `?` placeholders.

### 15.5 Server changes (`server.py`)

- `_user_from_token` now also reads `status`. `_auth_user(cur, roles=None)` is the single gatekeeper: no token gives **401**, suspended gives **403**, wrong role gives **403**. `_require_admin` uses it with `roles=("admin",)`.
- Tokens are now issued to **every** account at login. A suspended account cannot log in (403), and its login attempt is logged as `login_blocked`. Successful logins update `last_login_at`.
- `revoke_tokens(user_id)` deletes all of a user's tokens. It runs on suspend, delete and password reset, so they are signed out at once. A role change needs no revoke because the role is read fresh from the database on every request.
- New `POST /api/logout` removes the token and logs it. `logoutUser()` in `app.js` calls it.
- **Why event, ticket, report and profile routes now check the token:** otherwise a suspended person could still call the API directly. Events can be created by an organizer, and edited or deleted by the owner or an admin. Tickets and reports use the logged-in user's id, and a profile can only be edited by its owner.
- `handle_admin(method, path, query, body)` handles all the new `/api/admin/...` routes. `_is_admin_tool_route` decides whether a path goes there; the original moderation routes (`GET /api/admin/events`, `POST /api/admin/events/{id}/status`) are unchanged.

| Method | Path | Does |
|--------|------|------|
| GET | /api/admin/overview | dashboard numbers + latest activity |
| GET | /api/admin/users?q=&role=&status= | list / search users |
| GET | /api/admin/users/{id} | one user's events, tickets, reports, activity |
| POST | /api/admin/users/{id}/status | suspend or reactivate |
| POST | /api/admin/users/{id}/role | change account type (the only way to make an admin) |
| POST | /api/admin/users/{id}/password | reset password |
| DELETE | /api/admin/users/{id} | delete account and its data |
| GET | /api/admin/activity?user_id=&action=&limit=&offset= | the activity log |
| POST | /api/admin/tickets/{code}/status | valid / used / cancelled |
| DELETE | /api/admin/events/{id} | remove an event |

### 15.6 The page (`admin.html`) and helpers (`app.js`)

- **Four tabs:** Overview (stat cards and latest activity), Users, Activity, Event moderation (the old scam-review table plus a Remove button). A tab loads its data when you open it.
- **Users tab:** search box and filters (debounced by 250 ms so it does not query on every key). Each row has View, an account-type dropdown, Suspend / Reactivate, Reset password and Delete. Your own row only has View, matching the server rule. Dangerous actions go through a confirmation `<dialog>`; cancelling a role change puts the dropdown back.
- **User detail dialog:** the person's events (with Remove), tickets (a dropdown to change status), reports they made, and their activity.
- **Safe output:** every piece of user-written text goes through `escapeHtml` before it is put in `innerHTML`.
- `app.js`: `readResponse` is shared by all the fetch wrappers. If the server answers **401** (for example after a server restart cleared the tokens), it forgets the stored login and sends the person to the login page with `?next=`. The login page only follows `next` if it looks like a plain page name (`something.html`), so it cannot be used to redirect to another website, and sends admins to the dashboard, organizers to their dashboard, and attendees to My Tickets. `paintAdminNav` adds the Admin link for admins only.

### 15.7 How suspending works, step by step

1. Admin clicks Suspend and confirms. The browser calls `POST /api/admin/users/7/status` with `{"status": "suspended"}`.
2. `_require_admin` checks the token belongs to an active admin.
3. `set_user_status` rejects it if the target is the admin themselves or the last active admin; otherwise it updates the row and logs `admin_suspend_user`.
4. `revoke_tokens(7)` signs the person out everywhere.
5. From then on, their old token is dead (401), logging in returns 403, and so booking, creating events and reporting are all refused. Reactivating restores access.

### 15.8 Likely questions

- **How do you stop someone signing up as admin?** The page has no such option, the server converts any other role to attendee, and the only code path that sets `role = 'admin'` is the admin-only role route (plus the one-time first-admin creation).
- **What if the only admin gets suspended or demoted?** They can't do it to themselves, and `_keep_one_admin` stops any other admin from removing the last active one, so the site always keeps a working admin.
- **Why does the admin stay safe if the password is the demo one?** It isn't fully safe: the demo password is public in the README. That is why the server prints a warning and supports `NAIJA_ADMIN_EMAIL` / `NAIJA_ADMIN_PASSWORD` or `create_admin.py` for a real first admin.
- **Does a role change need a re-login?** No. The role is read from the database on every request.
- **Is the activity log tamper-proof?** No. Admin actions are logged, and nothing in the app edits or deletes log rows, but anyone with access to the database file could. Deleting a user keeps their log rows.
- **Limits:** tokens never expire and are lost on a restart; login attempts are not rate limited; the activity log has no automatic cleanup.

---

## 16. Organizer name on events and unique user names

Two small changes that work together: an event shows who runs it, and no two accounts can have the same name.

### 16.1 Showing the organizer's name

- **What the visitor sees:** on `event-details.html`, under the event description, a line reads `Organizer: <name>`. Only the name is shown; the email, role and other account details stay private.
- **Where the name comes from:** the `events` table only stores `organizer_id`. In `server.py`, both `GET /api/events` and `GET /api/events/{id}` now use a `LEFT JOIN` to `users` and return the name as an extra field called `organizer_name`:

```sql
SELECT events.*, users.full_name AS organizer_name
FROM events
LEFT JOIN users ON users.id = events.organizer_id
WHERE events.id = ?
```

- **Why `LEFT JOIN`:** an event is still returned even if its organizer row is somehow missing (the page then shows "Unknown").
- **Why only the name is sent:** the query selects `events.*` plus `users.full_name` only, so the organizer's email and password hash never leave the server on these routes.
- The page escapes the name with `escapeHtml`, like every other user-written text.

### 16.2 Unique names

**Rule:** no two accounts may have the same name. Capital letters and extra spaces are ignored, so `Amaka Okafor`, `amaka okafor` and `  Amaka   OKAFOR ` all count as the same name.

**Two helpers in `database.py`**
- `clean_name(name)`: trims the name and squashes repeated spaces (`" ".join(name.split())`). This is also what gets stored, so names are saved tidily.
- `name_taken(conn, name, exclude_id=None)`: runs `SELECT 1 FROM users WHERE lower(trim(full_name)) = ?` (with `AND id != ?` when `exclude_id` is given) and returns `True` if a row is found. `exclude_id` lets someone save their profile without clashing with their own current name.

**Where the rule is checked**
| Place | What happens on a clash |
|-------|--------------------------|
| `POST /api/register` | 400 "That name is already taken. Please use a different name." |
| `PUT /api/users/{id}` (profile page) | Same 400 message; keeping your own name is allowed |
| `create_admin()` (first admin) | Returns `(False, "The name '...' is already taken...")` |

An empty name (only spaces) is refused with 400 "Please enter your name".

**Database backstop:** `init_db()` also runs `CREATE UNIQUE INDEX IF NOT EXISTS idx_users_unique_name ON users (lower(trim(full_name)))`. Even if some code path forgot to call `name_taken`, SQLite itself would refuse a duplicate. If an old database already contains two people with the same name, the index cannot be built; the code catches that `IntegrityError` and prints a warning instead of crashing. The server checks still block new duplicates, and the admin can rename the old ones.

**Demo accounts:** `Demo Organizer` and `Site Admin` are already unique, so nothing changes for them. A new person cannot register as either name.

### 16.3 Step by step: registering with a taken name

1. Person submits the form on `register.html`; `apiPost("/api/register", ...)` is called.
2. `handle_api_post` checks name, email and password are present, then runs `clean_name` on the name.
3. `name_taken(conn, full_name)` finds an existing user with that name (ignoring case and spacing).
4. The server replies 400 with the "already taken" message, and `showMessage` displays it on the page. Nothing is inserted.

### 16.4 Likely questions

- **Why check in Python when there is a database index?** The Python check gives a friendly message and also works in the case where the index could not be created on an old database. The index is the safety net.
- **Is "Amaka Okafor" vs "Amaka Okafor." a clash?** No. Only case and spacing are ignored, so a name with extra punctuation is treated as different.
- **Can a race create two same names?** Two registrations at exactly the same moment could both pass `name_taken`, but the unique index then rejects the second one (the insert fails and the user sees an error instead of a duplicate being saved).
- **Does the organizer name change if the organizer renames themselves?** Yes. The name is read from `users` each time the page loads, so it always shows the current name.
