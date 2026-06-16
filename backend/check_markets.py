"""Check what markets exist in signals and market_snapshots tables."""
import asyncio
import sys
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")


async def main():
    import aiosqlite

    async with aiosqlite.connect("titibet.db") as db:
        db.row_factory = aiosqlite.Row

        print("Markets in signals table:")
        c = await db.execute(
            "SELECT market, COUNT(*) as n, "
            "SUM(CASE WHEN dual_confidence IS NOT NULL AND dual_confidence != 'None' THEN 1 ELSE 0 END) as with_dual "
            "FROM signals GROUP BY market ORDER BY n DESC"
        )
        for r in await c.fetchall():
            r = dict(r)
            print(f"  {r['market']:<30} total={r['n']:>5}  with_dual={r['with_dual']:>5}")

        print()
        print("Markets in market_snapshots (top 20):")
        c = await db.execute(
            "SELECT market_type, COUNT(*) as n "
            "FROM market_snapshots GROUP BY market_type ORDER BY n DESC LIMIT 20"
        )
        for r in await c.fetchall():
            r = dict(r)
            print(f"  {r['market_type']:<32} {r['n']:>6}")

        print()
        print("Distinct dual_confidence values in signals:")
        c = await db.execute(
            "SELECT dual_confidence, COUNT(*) as n FROM signals GROUP BY dual_confidence ORDER BY n DESC"
        )
        for r in await c.fetchall():
            r = dict(r)
            print(f"  {str(r['dual_confidence']):<20} {r['n']:>6}")

        print()
        print("DISABLED_MARKETS from config:")
        from app.core.config import settings
        for m in sorted(settings.DISABLED_MARKETS):
            print(f"  - {m}")


asyncio.run(main())
