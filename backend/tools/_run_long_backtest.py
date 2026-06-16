"""One pass of the long snapshot-replay backtest. Usage: python _run_long_backtest.py <A|B|C>
A = Fix1 on, settle EXEC (real ROI).  B = Fix1 on, settle PROXY (old inflated claim).
C = Fix1 exec-gate OFF, settle EXEC (old selection at real prices).
Runs against /tmp/titibet.db (local copy). Writes /tmp/bt_<mode>.json."""
import asyncio, json, sys, time
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.services.backtester import run_backtest
from app.core.config import get_settings

mode = sys.argv[1]

def slim(s):
    d = {k: s[k] for k in ("total_bets","wins","losses","hit_rate","roi","total_profit","avg_odds")}
    if mode == "A":
        d["by_market"] = sorted(
            [{"market":m["market"],"n":m["total"],"hit":m["hit_rate"],"roi":m["roi"],"avg_odds":m["avg_odds"]}
             for m in s["by_market"]], key=lambda x:-x["n"])[:12]
    return d

async def main():
    eng = create_async_engine("sqlite+aiosqlite:////tmp/titibet.db")
    Session = async_sessionmaker(eng, expire_on_commit=False)
    s = get_settings()
    settle = True
    if mode == "B":
        settle = False
    if mode == "C":
        try: s.min_exec_ev_pct = -10.0
        except Exception: object.__setattr__(s, "min_exec_ev_pct", -10.0)
    async with Session() as db:
        t0 = time.time()
        summ = await run_backtest(db, settle_at_exec=settle)
        el = round(time.time()-t0, 1)
    await eng.dispose()
    res = slim(summ); res["secs"] = el; res["mode"] = mode
    json.dump(res, open(f"/tmp/bt_{mode}.json","w"), indent=2)
    print(f"{mode} DONE in {el}s: bets={res['total_bets']} hit={res['hit_rate']}% roi={res['roi']}%")

asyncio.run(main())
