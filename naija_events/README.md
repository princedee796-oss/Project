# Naija Events - Event Discovery & Ticket Management System

A small event discovery and ticket booking platform for Nigerian events,
built as a school project.

**No web framework is used anywhere in this project.** The backend is
written only with Python's standard library (`http.server`, `sqlite3`,
`json`, `csv`, `argparse`). The frontend is plain HTML and CSS with a
small amount of vanilla JavaScript to call the backend's API.

## Project structure

```
naija_events/
  backend/
    server.py        <- the whole backend (pure Python, no Flask)
    database.py       <- SQLite table creation + connection helper
    utils.py           <- ticket codes, CSV export, JSON import, stats
    report.py            <- command line tool for charts/reports (argparse)
    data/
      sample_events.json <- starter data, loaded into SQLite on first run
      events.db            <- created automatically the first time you run it
  frontend/
    index.html         <- landing page
    css/style.css        <- one shared stylesheet for every page
    js/app.js              <- small shared JS helpers (fetch + login state)
    pages/                    <- the other 11 HTML pages
  exports/                       <- CSV ticket exports land here
  charts/                          <- matplotlib PNG charts land here
  reports/                           <- automated .txt reports land here
  requirements.txt
```

## Requirements

- Python 3.9 or newer
- `matplotlib` (only needed for the chart-drawing command, not for the
  website itself). Install it with:

  ```
  pip install -r requirements.txt
  ```

## Running the website

From inside the `backend/` folder, run:

```
python server.py
```

This will:
1. Create `backend/data/events.db` if it does not exist yet.
2. Load the sample events from `sample_events.json` the first time.
3. Start listening on `http://127.0.0.1:8000`.

Open that address in your browser to use the site. You can change the
port with `python server.py --port 8080`.

There is a demo organizer account already in the database:

- Email: `organizer@naijaevents.ng`
- Password: `demo1234`

Any new account you register through the sign-up page is saved for real
in the SQLite database, with the password stored as a salted SHA-256
hash (see `backend/security.py`) - never as plain text.

Only organizer accounts can create, edit or delete events, or see the
Organizer Dashboard. Attendee accounts have those nav links hidden and
are shown a plain message if they visit those pages directly; the
server enforces the same rule on `POST /api/events`.

## Pages included (12 total)

1. Landing page
2. Explore events page
3. Event details page
4. Search events page
5. Event filter page (by location + date range)
6. Login page
7. Registration page
8. Create / edit event page
9. Organizer dashboard (stats + charts + CSV export + manage events)
10. My tickets page
11. Ticket details page (also used to verify/check-in a ticket)
12. User profile page

## The REST API

Every page talks to the same small JSON API, served by `server.py`:

| Method | Path                    | What it does                              |
|--------|-------------------------|--------------------------------------------|
| GET    | /api/events              | list events, with optional filters         |
| GET    | /api/events/{id}          | one event's details                       |
| POST   | /api/events                | create an event                          |
| PUT    | /api/events/{id}            | edit an event                          |
| DELETE | /api/events/{id}             | delete an event                       |
| GET    | /api/categories                | list of categories in use          |
| GET    | /api/locations                   | list of locations in use         |
| POST   | /api/register                      | create a user account          |
| POST   | /api/login                           | log in                       |
| GET    | /api/users/{id}                        | profile info            |
| PUT    | /api/users/{id}                          | update profile        |
| POST   | /api/tickets                               | book a ticket      |
| GET    | /api/tickets?user_id=..                      | a user's tickets |
| GET    | /api/tickets/{code}                            | ticket lookup   |
| POST   | /api/tickets/verify                              | check a ticket in at the door |
| GET    | /api/stats                                         | dashboard numbers |
| GET    | /api/export/csv                                      | download tickets as CSV |

## The reporting command line tool

`backend/report.py` is a separate script (run from a terminal, not from
the browser) that demonstrates command line arguments, file handling
and data visualization:

```
python report.py --summary       # print numbers to the screen
python report.py --charts        # draw 3 matplotlib charts into charts/
python report.py --save-report   # write a dated .txt report into reports/
python report.py --export-csv    # export every ticket to a .csv file
```

You can combine flags, e.g. `python report.py --summary --charts`.
Re-run `--charts` after some tickets have been booked, then refresh the
organizer dashboard page to see the updated images.

## A few honest limitations

This was built to demonstrate the Python concepts from class
(variables, loops, functions, file handling, JSON, exception handling,
a REST API, SQLite, matplotlib, command line arguments, and hashing) rather than to be
production software, so a few corners were simplified on purpose:

- "Being logged in" is a random token kept in server memory and in the browser's
  `localStorage` - it does not expire on its own and is lost when the server restarts.
- There is no image upload - events use a single emoji as their icon,
  though every category still gets its own automatic colour and
  pattern theme wherever tickets/events are shown.

## Bonus ideas for later

The brief mentioned a couple of bonus features that were left out to
keep the project focused on the core requirements: automatic event
reminders and AI-generated event descriptions.


## Admin dashboard

`pages/admin.html` (the **Admin** link in the nav, visible to admins only) has four tabs:

- **Overview** - user counts by type, suspended accounts, new / active users this week, events, tickets, reports, latest activity.
- **Users** - search and filter all organizers and attendees, then for each one: view their events, tickets,
  reports and activity; change their account type; **suspend / reactivate**; reset their password; delete the account.
  Admins can also cancel or reinstate a user's tickets and remove an organizer's events from the user's detail view.
- **Activity** - the site-wide log (sign-ups, logins and failed logins, events created / edited / deleted, tickets
  booked and checked in, reports, profile edits, and every admin action), filterable and paged.
- **Event moderation** - the scam review (Verify / Flag / Reset) from before, plus removing an event.

### Who is an admin

- There is **exactly one admin at first**. On first start the server creates it from the environment variables
  `NAIJA_ADMIN_EMAIL` and `NAIJA_ADMIN_PASSWORD` if they are set; otherwise it falls back to the demo admin
  `admin@naijaevents.ng` / `admin1234` and prints a reminder to change that password (Admin > Users > Reset password).
  Or run `python create_admin.py --email you@example.com` on a database that has no admin yet.
- The sign-up page has no admin choice, and the server ignores `role: "admin"` in a sign-up request anyway.
- **Only an admin can make another admin** (Users tab > the account-type dropdown). That is the only route.
- Safety rules enforced in `backend/admin.py`: an admin can't suspend, demote or delete their own account, and the
  last active admin can never be removed by anyone. Every admin action is recorded in the activity log.

### Suspending a user

Suspending signs the person out immediately and blocks them everywhere: no login, no booking tickets, creating or
editing events, checking tickets in, or reporting events. Reactivating restores access. A password reset also signs
the person out. Role changes take effect on the very next request.

### What changed for security

To make suspension real, every account now gets a login token (not just admins and organizers), and the API checks
it: creating, editing and deleting events needs an organizer who owns the event (or an admin), booking and reporting
use the logged-in user rather than a `user_id` sent by the browser, and a profile can only be edited by its owner.
Tokens live in server memory, so everyone logs in again after a server restart.

New admin API routes (all need an admin token): `GET /api/admin/overview`, `GET /api/admin/users`,
`GET /api/admin/users/{id}`, `POST /api/admin/users/{id}/status | role | password`, `DELETE /api/admin/users/{id}`,
`GET /api/admin/activity`, `POST /api/admin/tickets/{code}/status`, `DELETE /api/admin/events/{id}`.
Also new: `POST /api/logout`.

## Scam check: is this event legit? (new)

Every event page has a **trust check** with three layers:

1. **Automatic score (0-100)** from `backend/trust.py`: looks at organizer account age,
   past check-ins, description quality, scam phrases ("double your money", "forex"...),
   requests to pay by bank transfer, unusually high prices, and user reports.
2. **User reports**: logged-in users can report an event (one report each, not their own).
3. **Admin review**: the admin page (`pages/admin.html`, "Moderation" link) lets an admin mark an
   event **Verified** or **Flagged**. Flagged events cannot be booked. Admin actions need a login token.

The login and sign-up pages no longer use images.

## Unique names and the organizer name

- Every account must have a **unique name** (capital letters and extra spaces are ignored, so
  "amaka okafor" and "Amaka  Okafor" count as the same name). This is checked when someone registers,
  when they change their name on the profile page, and when the first admin is created. The database
  also has a unique index on the name as a backstop.
- The event details page shows an **Organizer** line under the description with the organizer's name only
  (no email or other details). The API returns it as `organizer_name` on `/api/events` and `/api/events/<id>`.
