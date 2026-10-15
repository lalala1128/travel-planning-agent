"""Provision or rotate a tenant API key from the server console."""

import argparse
from secrets import token_urlsafe

from sqlalchemy.exc import IntegrityError

from app.auth import hash_api_key
from app.database import SessionLocal
from app.models import Tenant


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("create").add_argument("name")
    commands.add_parser("rotate").add_argument("tenant_id", type=int)
    args = parser.parse_args()

    api_key = f"tpa_{token_urlsafe(32)}"
    with SessionLocal() as db:
        if args.command == "create":
            if not 1 <= len(args.name) <= 100:
                parser.error("name must be between 1 and 100 characters")
            tenant = Tenant(name=args.name, api_key_hash=hash_api_key(api_key))
            db.add(tenant)
        else:
            tenant = db.get(Tenant, args.tenant_id)
            if tenant is None:
                parser.error("tenant not found")
            tenant.api_key_hash = hash_api_key(api_key)

        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            parser.error("tenant name already exists")
        db.refresh(tenant)

    print(f"tenant_id={tenant.id}")
    print(f"api_key={api_key}")


if __name__ == "__main__":
    main()
