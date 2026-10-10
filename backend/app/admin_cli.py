"""Bootstrap or reset admin access via the trusted API container shell."""

import asyncio
import getpass
import secrets
import sys
from datetime import timedelta

from sqlalchemy import delete

from app.api.v1.routers.admin import digest, passwords
from app.config import get_settings
from app.db.models import BootstrapCode, User, UserSession, utcnow
from app.db.session import Database


async def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "bootstrap-token"
    if command not in ("bootstrap-token", "reset-password"):
        raise SystemExit("Usage: python -m app.admin_cli [bootstrap-token|reset-password]")
    db = Database(get_settings().database_url)
    try:
        async with db.session() as session:
            admin = await session.get(User, 1)
            if command == "reset-password":
                if not admin or admin.status == "SETUP_REQUIRED":
                    raise SystemExit("No administrator account exists yet.")
                password = getpass.getpass("New administrator password (12+ characters): ")
                if len(password) < 12 or len(password) > 256:
                    raise SystemExit("Password must be between 12 and 256 characters.")
                if password != getpass.getpass("Repeat password: "):
                    raise SystemExit("Passwords do not match.")
                admin.password_hash = passwords.hash(password)
                await session.execute(delete(UserSession))
                await session.commit()
                print("Password reset; all existing admin sessions signed out.")
                return
            if admin and admin.status != "SETUP_REQUIRED":
                print("Administrator already configured; no setup code generated.")
                return
            code = secrets.token_urlsafe(24)
            await session.merge(
                BootstrapCode(
                    id=1,
                    token_hash=digest(code),
                    expires_at=utcnow() + timedelta(minutes=30),
                )
            )
            await session.commit()
            print(f"One-time admin setup code (expires in 30 minutes): {code}")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
