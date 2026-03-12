import asyncio
import sys
import bcrypt
from sqlalchemy import select

import models
from database import AsyncSessionLocal

EMAIL = "instructor@nasacademy.com"
PASSWORD = "admin123"
NAME = "NAS Instructor"
PHONE = "9876543210"


async def main() -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(models.User).where(models.User.email == EMAIL))
        user = result.scalars().first()
        hashed_password = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode()

        if user:
            user.full_name = NAME
            user.phone_number = PHONE
            user.role = "instructor"
            user.hashed_password = hashed_password
            await db.commit()
            print("UPDATED")
            return

        user = models.User(
            email=EMAIL,
            hashed_password=hashed_password,
            full_name=NAME,
            role="instructor",
            phone_number=PHONE,
            is_active=True,
        )
        db.add(user)
        await db.commit()
        print("CREATED")


if __name__ == "__main__":
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
