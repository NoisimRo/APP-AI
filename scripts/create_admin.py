"""Bootstrap script — create the first admin user.

Usage:
    DATABASE_URL="postgresql+asyncpg://..." python scripts/create_admin.py --email simion.ilie@gmail.com --password <secret>
"""

import argparse
import asyncio
import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.auth import get_password_hash
from app.models.decision import User


async def create_admin(email: str, password: str, nume: str = "Administrator"):
    """Create an admin user in the database."""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL environment variable is required")
        sys.exit(1)

    if not db_url.startswith("postgresql+asyncpg://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://")

    engine = create_async_engine(db_url)
    async_session = async_sessionmaker(engine, class_=AsyncSession)

    async with async_session() as session:
        # Check if user already exists
        result = await session.execute(
            select(User).where(User.email == email.lower().strip())
        )
        existing = result.scalar_one_or_none()

        if existing:
            print(f"User {email} already exists (rol={existing.rol})")
            if existing.rol != "admin":
                existing.rol = "admin"
                existing.password_hash = get_password_hash(password)
                existing.activ = True
                existing.email_verified = True
                await session.commit()
                print(f"Updated to admin role with new password")
            else:
                print("Already an admin. No changes made.")
            return

        user = User(
            email=email.lower().strip(),
            nume=nume,
            password_hash=get_password_hash(password),
            rol="admin",
            activ=True,
            email_verified=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        print(f"Admin user created successfully!")
        print(f"  ID: {user.id}")
        print(f"  Email: {user.email}")
        print(f"  Name: {user.nume}")
        print(f"  Role: {user.rol}")

    await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="Create admin user for ExpertAP")
    parser.add_argument("--email", required=True, help="Admin email address")
    parser.add_argument("--password", required=True, help="Admin password")
    parser.add_argument("--nume", default="Administrator", help="Admin display name")
    args = parser.parse_args()

    if len(args.password) < 8:
        print("ERROR: Password must be at least 8 characters")
        sys.exit(1)

    asyncio.run(create_admin(args.email, args.password, args.nume))


if __name__ == "__main__":
    main()
