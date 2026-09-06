import argparse
import sys
from pathlib import Path

# Ensure app package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth.tokens import create_access_token
from app.schemas.canonical import UserRole


def main():
    parser = argparse.ArgumentParser(description="Generate development JWT access token for ARGUS API.")
    parser.add_argument("--user-id", required=True, help="User ID (e.g. officer-001)")
    parser.add_argument("--role", required=True, choices=[r.value for r in UserRole], help="User role")
    parser.add_argument("--name", default=None, help="User full name")
    parser.add_argument("--email", default=None, help="User email address")

    args = parser.parse_args()

    token = create_access_token(
        user_id=args.user_id,
        role=UserRole(args.role),
        name=args.name,
        email=args.email,
    )
    print(f"Generated Dev Token for {args.user_id} ({args.role}):\n")
    print(token)


if __name__ == "__main__":
    main()
