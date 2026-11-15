import getpass
import sys

from app import create_app, db
from app.models import User


def main():
    if len(sys.argv) != 3:
        raise SystemExit("Usage: python create_admin.py <username> <email>")
    username = sys.argv[1].strip()
    email = sys.argv[2].strip().lower()
    if not 3 <= len(username) <= 40 or "@" not in email:
        raise SystemExit("Provide a username (3-40 characters) and a valid email address.")
    password = getpass.getpass("Administrator password (minimum 12 characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if len(password) < 12 or password != confirmation:
        raise SystemExit("Passwords must match and be at least 12 characters.")

    app = create_app()
    with app.app_context():
        if User.query.filter((User.username == username) | (User.email == email)).first():
            raise SystemExit("An account already uses that username or email.")
        admin = User(username=username, email=email, role="admin")
        admin.set_password(password)
        db.session.add(admin)
        db.session.commit()
        print(f"Administrator account '{username}' created.")


if __name__ == "__main__":
    main()
