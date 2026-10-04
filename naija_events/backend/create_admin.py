"""
create_admin.py
---------------
Creates the FIRST (and only starting) admin account. Run it once:

    python create_admin.py --email you@example.com --name "Your Name"

The password is typed in a hidden prompt (or set NAIJA_ADMIN_PASSWORD).
It refuses to run if an admin already exists - from then on, admins make
other admins from the Admin dashboard. Nobody can become admin by signing up.
"""

import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from database import init_db, create_admin


def main():
    parser = argparse.ArgumentParser(description="Create the first admin account")
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", default="Site Admin")
    args = parser.parse_args()

    password = os.environ.get("NAIJA_ADMIN_PASSWORD")
    if not password:
        password = getpass.getpass("Admin password (min 8 characters): ")
        if password != getpass.getpass("Repeat password: "):
            sys.exit("Passwords did not match.")

    init_db()
    ok, message = create_admin(args.name, args.email, password)
    print(message)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
