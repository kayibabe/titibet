import asyncio
from sqlalchemy import text
from app.core.database import engine

EMAIL = "admin@titibet.com"

async def promote():
    async with engine.begin() as conn:
        result = await conn.execute(
            text("UPDATE users SET tier='elite', subscription_status='active' WHERE email=:email"),
            {"email": EMAIL},
        )
        if result.rowcount == 0:
            print(f"No user found with email: {EMAIL}")
            print("Register first, then re-run this script.")
        else:
            print(f"Done — {EMAIL} is now Elite.")

asyncio.run(promote())
