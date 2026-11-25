"""Admin command line.

    python -m app.cli create-user admin@example.com --role editor
(prompts for the password; pass --password for scripts)
"""

import argparse
import getpass
import sys

from sqlalchemy import select

from app.database import SessionLocal
from app.models import User
from app.schemas import UserCreate
from app.security import hash_password


def create_user(email: str, password: str, role: str, if_not_exists: bool) -> int:
    data = UserCreate(email=email, password=password, role=role)  # same validation as the API
    with SessionLocal() as db:
        existing = db.scalars(select(User).where(User.email == data.email)).first()
        if existing:
            print(f"User {data.email} already exists")
            return 0 if if_not_exists else 1
        db.add(User(email=data.email, hashed_password=hash_password(data.password), role=data.role))
        db.commit()
    print(f"Created {data.role} {data.email}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    cu = sub.add_parser("create-user")
    cu.add_argument("email")
    cu.add_argument("--role", choices=["viewer", "editor"], default="editor")
    cu.add_argument("--password")
    cu.add_argument("--if-not-exists", action="store_true")
    args = parser.parse_args()
    password = args.password or getpass.getpass("Password: ")
    sys.exit(create_user(args.email, password, args.role, args.if_not_exists))


if __name__ == "__main__":
    main()
